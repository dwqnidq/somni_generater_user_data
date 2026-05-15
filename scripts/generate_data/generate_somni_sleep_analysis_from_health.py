"""
根据 output 下每用户的 {uid}_health_data.json 与 {uid}_sleep_events.json，
按产品规则生成睡眠地图表结构 somni_sleep_analysis（按 uid × record_date 一条）。

根字段：
  - sleep_seconds：当日 total_sleep_minutes × 60
  - deep_sleep_ratio：raw_data.deep_sleep_ratio（0~100 整数）÷ 100 → 0~1 小数
  - deep_sleep_seconds：当日总卧床分钟 × (深睡比例÷100) × 60；卧床来自 bed_time→wake_up_time，
    缺失时回退为 total_sleep_minutes ÷ max(0.01, 1 − awake_ratio/100)（与 SPT 口径一致）
  - evaluation：默认空串

dimensions：
  - deep_sleep / sleep_efficiency / abnormal_events 的 score 按「当日」数据计算；
  - sleep_duration 的 score 按当日总睡眠计算；
  - routine_regularity 的 score 按以 stats_date 为结束日的连续 14 个日历日窗口内
    入睡/起床时刻的分钟数标准差（与 utils.calculate_sleep_map_score_window 一致：(σ睡+σ起)/2）；
  - sleep_duration.value、sleep_efficiency.value、abnormal_events.value 为该 14 日窗口内
    有数据日的算术平均（分钟或秒、次数按文档约定）；
  - deep_sleep.value 为当日深睡占比整数（不除以 100）；
  - city_avg / city_score 默认固定，可通过命令行覆盖。

库用法（供 main.py 等调用）：

  from generate_somni_sleep_analysis_from_health import write_somni_sleep_analysis_for_uid
  write_somni_sleep_analysis_for_uid(uid, output_dir, user_name="完美主义百灵鸟")

用法（CLI）：
  python scripts/generate_data/generate_somni_sleep_analysis_from_health.py
  python scripts/generate_data/generate_somni_sleep_analysis_from_health.py \\
      --output-dir output --out output/somni_sleep_analysis.json --uid <uid>
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time as _time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

# 与产品约定：北京市海淀区（写入 main 流水线时固定使用）
DEFAULT_SOMNI_SLEEP_REGION: Dict[str, str] = {
    "province_code": "110000",
    "city_code": "110100",
    "district_code": "110108",
}

DEFAULT_CITY_DIMS_FOR_DIMENSIONS: dict = {
    "deep_sleep": {"city_avg": 15.0, "city_score": 68},
    "sleep_duration": {"city_avg": 26432.0, "city_score": 91},
    "sleep_efficiency": {"city_avg": 43.0, "city_score": 48},
    "abnormal_events": {"city_avg": 2.0, "city_score": 78},
    "routine_regularity": {"city_avg": "Fair", "city_score": 45},
}

from utils import (  # noqa: E402
    _build_abnormal_event_index,
    _minutes_since_midnight,
    _parse_iso_datetime,
    _safe_float,
    score_no_abnormal_events,
    score_sleep_latency,
    score_sleep_regularity,
)

WEIGHTS_FRAC = {
    "deep_sleep": 0.25,
    "sleep_duration": 0.25,
    "abnormal_events": 0.20,
    "sleep_efficiency": 0.15,
    "routine_regularity": 0.15,
}


def make_object_id(ts: Optional[int] = None) -> str:
    if ts is None:
        ts = int(_time.time())
    ts_hex = format(ts & 0xFFFFFFFF, "08x")
    rand_hex = "".join(random.choices("0123456789abcdef", k=16))
    return ts_hex + rand_hex


def _parse_date(s: str) -> date:
    parts = s.split("-")
    if len(parts) != 3:
        raise ValueError("日期格式应为 YYYY-MM-DD")
    return date(int(parts[0]), int(parts[1]), int(parts[2]))


def _date_range_inclusive(end: date, n_days: int) -> List[date]:
    start = end - timedelta(days=n_days - 1)
    out: List[date] = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def _tib_minutes_fallback_spt(raw: dict, total_sleep_minutes: float) -> float:
    """卧床分钟：优先 bed→wake_up；否则用 TST 与清醒占 SPT% 反推 SPT。"""
    bt = _parse_iso_datetime(raw.get("bed_time"))
    wu = _parse_iso_datetime(raw.get("wake_up_time"))
    if bt and wu:
        w = wu
        if w <= bt:
            w = w + timedelta(days=1)
        return max(0.0, (w - bt).total_seconds() / 60.0)
    awake_p = max(0.0, min(99.9, _safe_float(raw.get("awake_ratio"))))
    denom = max(0.01, 1.0 - awake_p / 100.0)
    return max(float(total_sleep_minutes), 0.0) / denom


def score_sleep_duration_seconds(sleep_sec: float) -> int:
    """7~9h 满分；不足 7h 每少 30 分钟扣 10（低于 4h 0 分）；超过 9h 每多 30 分钟扣 5。"""
    s4, s7, s9 = 14400, 25200, 32400
    if sleep_sec < s4:
        return 0
    if sleep_sec <= s7:
        deficit = (s7 - sleep_sec) / 1800.0
        return max(0, round(100 - deficit * 10))
    if sleep_sec <= s9:
        return 100
    excess = (sleep_sec - s9) / 1800.0
    return max(0, round(100 - excess * 5))


def score_deep_sleep_by_ratio(deep_sleep_minutes: float, ratio_decimal: float) -> int:
    """占比分档 + 深睡 <30 分钟则 0 分。"""
    if deep_sleep_minutes < 30:
        return 0
    r = ratio_decimal
    if r >= 0.20:
        return 100
    if r >= 0.15:
        return 80
    if r >= 0.10:
        return 60
    return 40


def routine_text(score: int) -> str:
    if score >= 90:
        return "Excellent"
    if score >= 70:
        return "Great"
    if score >= 50:
        return "Good"
    if score >= 30:
        return "Fair"
    return "Poor"


def _pstdev_or_zero(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    return float(statistics.pstdev(values))


def _fluctuation_minutes_for_window(
    health_by_date: Dict[str, dict],
    window_dates: List[date],
) -> float:
    sleep_mins: List[float] = []
    wake_mins: List[float] = []
    for d in window_dates:
        item = health_by_date.get(d.isoformat())
        if not item:
            continue
        raw = item.get("raw_data") or {}
        sdt = _parse_iso_datetime(raw.get("sleep_time"))
        wdt = _parse_iso_datetime(raw.get("wake_up_time"))
        sm = _minutes_since_midnight(sdt)
        wm = _minutes_since_midnight(wdt)
        if sm is not None:
            sleep_mins.append(float(sm))
        if wm is not None:
            wake_mins.append(float(wm))
    sleep_std = _pstdev_or_zero(sleep_mins)
    wake_std = _pstdev_or_zero(wake_mins)
    return (sleep_std + wake_std) / 2.0


def _rolling_averages(
    health_by_date: Dict[str, dict],
    abnormal_by_date: Dict[str, dict],
    window_dates: List[date],
) -> Tuple[float, float, float]:
    """返回 (avg_total_sleep_min, avg_latency_min, avg_abnormal_count)。"""
    sleeps: List[float] = []
    latencies: List[float] = []
    counts: List[float] = []
    for d in window_dates:
        ds = d.isoformat()
        h = health_by_date.get(ds)
        if not h:
            continue
        raw = h.get("raw_data") or {}
        sleeps.append(max(0.0, _safe_float(raw.get("total_sleep_minutes"))))
        latencies.append(max(0.0, _safe_float(raw.get("sleep_latency"))))
        st = abnormal_by_date.get(ds, {"abnormal_count": 0, "abnormal_duration_sec": 0.0})
        counts.append(float(st.get("abnormal_count", 0)))
    if not sleeps:
        return 0.0, 0.0, 0.0
    return (
        sum(sleeps) / len(sleeps),
        sum(latencies) / len(latencies),
        sum(counts) / len(counts),
    )


def comprehensive_score(dim_scores: Dict[str, int]) -> int:
    return int(
        round(
            sum(WEIGHTS_FRAC[k] * float(dim_scores[k]) for k in WEIGHTS_FRAC)
        )
    )


def _iter_uids(output_dir: str) -> List[str]:
    uids = []
    for name in os.listdir(output_dir):
        if name.endswith("_health_data.json"):
            uids.append(name.replace("_health_data.json", ""))
    return sorted(uids)


def build_somni_sleep_analysis_records_for_uid(
    uid: str,
    output_dir: str,
    *,
    user_name: str,
    window_days: int = 14,
    region: Optional[dict] = None,
    city_dims: Optional[dict] = None,
    now_iso: Optional[str] = None,
) -> List[dict]:
    """
    读取 output_dir/{uid}_health_data.json 与可选的 {uid}_sleep_events.json，
    生成该用户全部 stats_date 的 somni_sleep_analysis 行（不写盘）。
    """
    output_dir = os.path.abspath(output_dir)
    health_path = os.path.join(output_dir, f"{uid}_health_data.json")
    if not os.path.isfile(health_path):
        return []

    with open(health_path, encoding="utf-8") as f:
        health_list: List[dict] = json.load(f)
    events_path = os.path.join(output_dir, f"{uid}_sleep_events.json")
    events_list: List[dict] = []
    if os.path.isfile(events_path):
        with open(events_path, encoding="utf-8") as f:
            events_list = json.load(f)
    abnormal_by_date = _build_abnormal_event_index(events_list)

    health_by_date: Dict[str, dict] = {}
    for item in health_list:
        rd = item.get("record_date")
        if isinstance(rd, str) and len(rd) >= 10:
            health_by_date[rd[:10]] = item

    reg = dict(region) if region is not None else dict(DEFAULT_SOMNI_SLEEP_REGION)
    cd = city_dims if city_dims is not None else DEFAULT_CITY_DIMS_FOR_DIMENSIONS
    ts_iso = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    records: List[dict] = []
    for stats_date, health_item in sorted(health_by_date.items()):
        try:
            end_d = _parse_date(stats_date)
        except ValueError:
            continue
        window_dates = _date_range_inclusive(end_d, window_days)
        records.append(
            build_one_record(
                uid=uid,
                stats_date=stats_date,
                health_item=health_item,
                health_by_date=health_by_date,
                abnormal_by_date=abnormal_by_date,
                window_dates=window_dates,
                region=reg,
                city_dims=cd,
                now_iso=ts_iso,
                user_name=user_name,
            )
        )
    records.sort(
        key=lambda r: (r["stats_date"], r["region"]["district_code"], r["uid"], -r["score"])
    )
    return records


def write_somni_sleep_analysis_for_uid(
    uid: str,
    output_dir: str,
    *,
    user_name: str = "",
    window_days: int = 14,
    region: Optional[dict] = None,
    city_dims: Optional[dict] = None,
) -> Tuple[Optional[str], int]:
    """
    写入 output_dir/{uid}_somni_sleep_analysis.json。
    返回 (文件路径, 条数)；无 health 或无有效日时为 (None, 0)。
    user_name 为空则回退为 sleep_map_user_{uid 前 8 位}。
    """
    display = (user_name or "").strip() or f"sleep_map_user_{uid[:8]}"
    records = build_somni_sleep_analysis_records_for_uid(
        uid,
        output_dir,
        user_name=display,
        window_days=window_days,
        region=region,
        city_dims=city_dims,
    )
    if not records:
        return None, 0
    out_path = os.path.join(os.path.abspath(output_dir), f"{uid}_somni_sleep_analysis.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    return out_path, len(records)


def build_one_record(
    uid: str,
    stats_date: str,
    health_item: dict,
    health_by_date: Dict[str, dict],
    abnormal_by_date: Dict[str, dict],
    window_dates: List[date],
    region: dict,
    city_dims: dict,
    now_iso: str,
    user_name: str,
) -> dict:
    raw = health_item.get("raw_data") or {}
    total_sleep_min = max(0.0, _safe_float(raw.get("total_sleep_minutes")))
    sleep_sec = int(round(total_sleep_min * 60.0))

    deep_ratio_int = int(round(_safe_float(raw.get("deep_sleep_ratio"))))
    deep_ratio_int = max(0, min(100, deep_ratio_int))
    ratio_dec = deep_ratio_int / 100.0

    tib_min = _tib_minutes_fallback_spt(raw, total_sleep_min)
    deep_sleep_min = tib_min * ratio_dec
    deep_sleep_sec = int(round(deep_sleep_min * 60.0))

    latency_min = max(0.0, _safe_float(raw.get("sleep_latency")))
    ev_stats = abnormal_by_date.get(
        stats_date, {"abnormal_count": 0, "abnormal_duration_sec": 0.0}
    )
    abn_count = int(ev_stats.get("abnormal_count", 0))
    abn_dur_sec = float(ev_stats.get("abnormal_duration_sec", 0.0))

    fluctuation = _fluctuation_minutes_for_window(health_by_date, window_dates)
    avg_sleep_min, avg_latency_min, avg_abn_count = _rolling_averages(
        health_by_date, abnormal_by_date, window_dates
    )

    dim_deep = score_deep_sleep_by_ratio(deep_sleep_min, ratio_dec)
    dim_dur = score_sleep_duration_seconds(float(sleep_sec))
    dim_eff = int(round(score_sleep_latency(latency_min)))
    dim_abn, _, _ = score_no_abnormal_events(total_sleep_min, abn_dur_sec, float(abn_count))
    dim_abn_i = int(round(float(dim_abn)))
    dim_routine = int(round(score_sleep_regularity(fluctuation)))

    dim_scores = {
        "deep_sleep": dim_deep,
        "sleep_duration": dim_dur,
        "abnormal_events": dim_abn_i,
        "sleep_efficiency": dim_eff,
        "routine_regularity": dim_routine,
    }
    total = comprehensive_score(dim_scores)

    cd = city_dims

    return {
        "_id": make_object_id(),
        "uid": uid,
        "stats_date": stats_date,
        "region": region,
        "user_name": user_name,
        "score": total,
        "sleep_seconds": sleep_sec,
        "deep_sleep_seconds": deep_sleep_sec,
        "deep_sleep_ratio": round(ratio_dec, 4),
        "evaluation": "",
        "dimensions": {
            "deep_sleep": {
                "score": dim_deep,
                "weight": 25,
                "value": deep_ratio_int,
                "city_avg": cd["deep_sleep"]["city_avg"],
                "city_score": cd["deep_sleep"]["city_score"],
            },
            "sleep_duration": {
                "score": dim_dur,
                "weight": 25,
                "value": int(round(avg_sleep_min * 60.0)),
                "city_avg": cd["sleep_duration"]["city_avg"],
                "city_score": cd["sleep_duration"]["city_score"],
            },
            "sleep_efficiency": {
                "score": dim_eff,
                "weight": 15,
                "value": int(round(avg_latency_min)),
                "city_avg": cd["sleep_efficiency"]["city_avg"],
                "city_score": cd["sleep_efficiency"]["city_score"],
            },
            "abnormal_events": {
                "score": dim_abn_i,
                "weight": 20,
                "value": int(round(avg_abn_count)),
                "city_avg": cd["abnormal_events"]["city_avg"],
                "city_score": cd["abnormal_events"]["city_score"],
            },
            "routine_regularity": {
                "score": dim_routine,
                "weight": 15,
                "value": routine_text(dim_routine),
                "city_avg": cd["routine_regularity"]["city_avg"],
                "city_score": cd["routine_regularity"]["city_score"],
            },
        },
        "is_env_sensitive": False,
        "create_time": now_iso,
        "update_time": now_iso,
    }


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", default=os.path.join(PROJECT_ROOT, "output"))
    p.add_argument(
        "--out",
        default=os.path.join(PROJECT_ROOT, "output", "somni_sleep_analysis_from_health.json"),
    )
    p.add_argument("--uid", default="", help="仅处理该用户；默认处理目录下所有 *_health_data.json")
    p.add_argument("--window-days", type=int, default=14, help="滚动窗口天数，默认 14")
    p.add_argument("--seed", type=int, default=0, help="ObjectId 随机部分种子，0 表示不固定")
    p.add_argument("--province-code", default="110000")
    p.add_argument("--city-code", default="110100")
    p.add_argument("--district-code", default="110108")
    p.add_argument("--deep-city-avg", type=float, default=15.0)
    p.add_argument("--deep-city-score", type=int, default=68)
    p.add_argument("--duration-city-avg", type=float, default=26432.0)
    p.add_argument("--duration-city-score", type=int, default=91)
    p.add_argument("--efficiency-city-avg", type=float, default=43.0)
    p.add_argument("--efficiency-city-score", type=int, default=48)
    p.add_argument("--abnormal-city-avg", type=float, default=2.0)
    p.add_argument("--abnormal-city-score", type=int, default=78)
    p.add_argument("--routine-city-avg", default="Fair")
    p.add_argument("--routine-city-score", type=int, default=45)
    return p.parse_args()


def main():
    args = parse_args()
    if args.seed:
        random.seed(args.seed)

    output_dir = os.path.abspath(args.output_dir)
    if not os.path.isdir(output_dir):
        print(f"错误：目录不存在 {output_dir}", file=sys.stderr)
        sys.exit(1)

    uids = [args.uid.strip()] if args.uid.strip() else _iter_uids(output_dir)
    if not uids:
        print("未找到任何 *_health_data.json", file=sys.stderr)
        sys.exit(1)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    region = {
        "province_code": args.province_code,
        "city_code": args.city_code,
        "district_code": args.district_code,
    }
    city_dims = {
        "deep_sleep": {"city_avg": args.deep_city_avg, "city_score": args.deep_city_score},
        "sleep_duration": {
            "city_avg": args.duration_city_avg,
            "city_score": args.duration_city_score,
        },
        "sleep_efficiency": {
            "city_avg": args.efficiency_city_avg,
            "city_score": args.efficiency_city_score,
        },
        "abnormal_events": {
            "city_avg": args.abnormal_city_avg,
            "city_score": args.abnormal_city_score,
        },
        "routine_regularity": {
            "city_avg": args.routine_city_avg,
            "city_score": args.routine_city_score,
        },
    }

    records: List[dict] = []
    for uid in uids:
        display = f"sleep_map_user_{uid[:8]}"
        sub = build_somni_sleep_analysis_records_for_uid(
            uid,
            output_dir,
            user_name=display,
            window_days=args.window_days,
            region=region,
            city_dims=city_dims,
            now_iso=now_iso,
        )
        if not sub:
            print(f"[跳过] 无 health 文件或无可解析日期: {uid}")
            continue
        records.extend(sub)

    records.sort(
        key=lambda r: (r["stats_date"], r["region"]["district_code"], r["uid"], -r["score"])
    )

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"已写入 {len(records)} 条 → {out_path}")


if __name__ == "__main__":
    main()
