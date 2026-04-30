"""将 sleep_events 影响统一回写到 environment_data 与 vitals_data。"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_ROOT)

from utils import atomic_write_json

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "health_data_personas_config.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")


def _time_to_minutes(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _build_rows_by_date(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if not isinstance(r, dict):
            continue
        rd = r.get("record_date")
        if rd:
            out[str(rd)].append(r)
    return out


def _event_local_min(event: dict) -> int | None:
    return _time_to_minutes(str(event.get("event_timestamp") or ""))


def _is_noise_related_event(event: dict) -> bool:
    et = str(event.get("event_type") or "")
    code = str(event.get("code") or "")
    if et == "打鼾":
        return True
    if "噪" in et:
        return True
    if "noise" in code.lower():
        return True
    detail = event.get("detail") or {}
    return "噪" in str(detail.get("trigger_cause") or "")


def _noise_target_db(event: dict) -> int:
    et = str(event.get("event_type") or "")
    detail = event.get("detail") or {}
    if et == "打鼾":
        try:
            v = int(round(float(detail.get("snoring_value_db"))))
        except Exception:
            v = 42
        return max(40, min(60, v))
    return 58


def _vitals_int(metrics: dict, key: str, default: int) -> int:
    try:
        return int(round(float(metrics.get(key, default))))
    except Exception:
        return default


def _onset_noise_bias_db(local_minute: int) -> int:
    # 入睡越早（21~23）背景越高；凌晨 1 点后更低
    if 21 * 60 <= local_minute <= 23 * 60 + 59:
        return 5
    if 23 * 60 <= local_minute or local_minute <= 30:
        return 2
    if 30 < local_minute <= 2 * 60:
        return -1
    if 2 * 60 < local_minute <= 5 * 60:
        return -3
    return 0


def _apply_time_context_noise_baseline(env_day: list[dict]) -> None:
    if not env_day:
        return
    # 按真实时间排序（跨午夜场景也正确），首点视作当夜入睡起点
    seq: list[tuple[int, datetime, int]] = []
    for i, row in enumerate(env_day):
        z = row.get("collected_at")
        if not z:
            continue
        try:
            local_dt = datetime.strptime(z, "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        except Exception:
            continue
        seq.append((i, local_dt, local_dt.hour * 60 + local_dt.minute))
    if not seq:
        return
    seq.sort(key=lambda x: x[1])
    onset_dt = seq[0][1]
    onset_min = seq[0][2]
    bias = _onset_noise_bias_db(onset_min)
    if bias == 0:
        return
    # 仅对入睡后前 3 小时背景噪声做时段偏移，随后 2 小时内衰减到 0
    for idx, dt_local, _mm in seq:
        elapsed_min = max(0.0, (dt_local - onset_dt).total_seconds() / 60.0)
        if elapsed_min <= 180:
            factor = 1.0
        elif elapsed_min <= 300:
            factor = max(0.0, 1.0 - (elapsed_min - 180.0) / 120.0)
        else:
            factor = 0.0
        if factor <= 0.0:
            continue
        try:
            cur = int(round(float(env_day[idx].get("noise", 25))))
        except Exception:
            cur = 25
        env_day[idx]["noise"] = max(18, min(80, cur + int(round(bias * factor))))


def _baseline_path(data_path: str) -> str:
    if data_path.endswith(".json"):
        return f"{data_path[:-5]}_baseline.json"
    return f"{data_path}.baseline"


def _load_or_create_baseline(data_path: str) -> list[dict] | None:
    base_path = _baseline_path(data_path)
    src_path = data_path
    if os.path.exists(base_path):
        try:
            base_mtime = os.path.getmtime(base_path)
            data_mtime = os.path.getmtime(data_path)
            # 若主数据更新晚于 baseline，自动刷新 baseline，避免基线陈旧
            if base_mtime >= data_mtime:
                src_path = base_path
        except OSError:
            src_path = data_path
    try:
        with open(src_path, "r", encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(rows, list):
        return None
    if (not os.path.exists(base_path)) or (src_path == data_path):
        atomic_write_json(base_path, rows)
    return copy.deepcopy(rows)


def _apply_env_impacts(events_day: list[dict], env_day: list[dict]) -> set[int]:
    if not env_day:
        return set()
    idx_by_min: dict[int, list[int]] = defaultdict(list)
    locked_idxs: set[int] = set()
    for i, row in enumerate(env_day):
        z = row.get("collected_at")
        if not z:
            continue
        try:
            local_dt = datetime.strptime(z, "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        except Exception:
            continue
        mm = local_dt.hour * 60 + local_dt.minute
        idx_by_min[mm].append(i)

    for ev in events_day:
        if not _is_noise_related_event(ev):
            continue
        m = _event_local_min(ev)
        if m is None:
            continue
        target = _noise_target_db(ev)
        et = str(ev.get("event_type") or "")
        if et == "打鼾":
            detail = ev.get("detail") or {}
            dur_sec = ev.get("duration_sec") or detail.get("duration_sec") or 300
            try:
                dur_sec = int(round(float(dur_sec)))
            except Exception:
                dur_sec = 300
            dur_sec = max(60, min(2400, dur_sec))
            dur_min = max(1, int(round(dur_sec / 60.0)))
            # 在 duration 区间内生成平缓噪声包络，控制在 40~60
            for step in range(dur_min):
                mm = (m + step) % 1440
                if dur_min <= 1:
                    smooth = 0.85
                else:
                    x = step / float(dur_min - 1)
                    smooth = 0.72 + 0.28 * (1.0 - abs(2.0 * x - 1.0))
                want = int(round(max(40, min(60, target * smooth))))
                for idx in idx_by_min.get(mm, []):
                    env_day[idx]["noise"] = want
                    locked_idxs.add(idx)
            # 前后过渡，避免边缘突变
            for off, scale in [(-3, 0.72), (-2, 0.82), (-1, 0.9), (dur_min, 0.9), (dur_min + 1, 0.82), (dur_min + 2, 0.72)]:
                mm = (m + off) % 1440
                want = int(round(max(35, min(60, target * scale))))
                for idx in idx_by_min.get(mm, []):
                    try:
                        cur = int(round(float(env_day[idx].get("noise", 25))))
                    except Exception:
                        cur = 25
                    env_day[idx]["noise"] = max(cur, want)
                    if 40 <= env_day[idx]["noise"] <= 60:
                        locked_idxs.add(idx)
        else:
            for off, scale in [(-2, 0.55), (-1, 0.78), (0, 1.0), (1, 0.82), (2, 0.60)]:
                mm = (m + off) % 1440
                for idx in idx_by_min.get(mm, []):
                    try:
                        cur = int(round(float(env_day[idx].get("noise", 25))))
                    except Exception:
                        cur = 25
                    env_day[idx]["noise"] = max(cur, int(round(target * scale)))
    return locked_idxs


def _enforce_no_sudden_noise_drop(
    env_day: list[dict], max_drop_per_min: float = 1.5, locked_idxs: set[int] | None = None
) -> None:
    if not env_day:
        return
    locked = locked_idxs or set()
    seq: list[tuple[datetime, int]] = []
    for i, row in enumerate(env_day):
        z = row.get("collected_at")
        if not z:
            continue
        try:
            dt = datetime.strptime(z, "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue
        seq.append((dt, i))
    if not seq:
        return
    seq.sort(key=lambda x: x[0])
    prev_idx = seq[0][1]
    try:
        prev_noise = float(env_day[prev_idx].get("noise", 25))
    except Exception:
        prev_noise = 25.0
    prev_dt = seq[0][0]
    for dt, idx in seq[1:]:
        if idx in locked:
            try:
                prev_noise = float(env_day[idx].get("noise", prev_noise))
            except Exception:
                pass
            prev_dt = dt
            continue
        try:
            cur_noise = float(env_day[idx].get("noise", prev_noise))
        except Exception:
            cur_noise = prev_noise
        gap_min = max(1.0, (dt - prev_dt).total_seconds() / 60.0)
        min_allowed = prev_noise - max_drop_per_min * gap_min
        if cur_noise < min_allowed:
            env_day[idx]["noise"] = int(round(min_allowed))
            cur_noise = float(env_day[idx]["noise"])
        prev_noise = cur_noise
        prev_dt = dt


def _apply_vitals_impacts(events_day: list[dict], vitals_day: list[dict]) -> None:
    if not vitals_day:
        return
    idx_by_min: dict[int, list[int]] = defaultdict(list)
    for i, row in enumerate(vitals_day):
        z = row.get("collected_at")
        if not z:
            continue
        try:
            local_dt = datetime.strptime(z, "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
        except Exception:
            continue
        mm = local_dt.hour * 60 + local_dt.minute
        idx_by_min[mm].append(i)

    for ev in events_day:
        et = str(ev.get("event_type") or "")
        code = str(ev.get("code") or "")
        if et not in {"噩梦应激", "异常体动"} and code not in {"nightmare", "abnormal_movement"}:
            continue
        is_nightmare = (et == "噩梦应激") or (code == "nightmare")
        min_hr_threshold = 100 if is_nightmare else 80
        m = _event_local_min(ev)
        if m is None:
            continue
        for off, hr_add, rr_add in [
            (-3, 1, 0),
            (-2, 2, 1),
            (-1, 3, 1),
            (0, 5, 2),
            (1, 4, 2),
            (2, 3, 1),
            (3, 2, 1),
            (4, 1, 0),
        ]:
            mm = (m + off) % 1440
            for idx in idx_by_min.get(mm, []):
                metrics = vitals_day[idx].get("metrics") or {}
                hr = _vitals_int(metrics, "heart_rate", 65)
                rr = _vitals_int(metrics, "respiration_rate", 15)
                # 事件影响窗口内：噩梦应激>=100，其它异常体动>=80
                metrics["heart_rate"] = min(130, max(min_hr_threshold, hr + hr_add))
                metrics["respiration_rate"] = min(30, max(8, rr + rr_add))
                vitals_day[idx]["metrics"] = metrics


def apply_event_impacts_for_persona(persona: dict, start_date: str | None, end_date: str | None) -> bool:
    uid = persona["user_id"]
    env_path = os.path.join(OUTPUT_DIR, f"{uid}_environment_data.json")
    vit_path = os.path.join(OUTPUT_DIR, f"{uid}_vitals_data.json")
    ev_path = os.path.join(OUTPUT_DIR, f"{uid}_sleep_events.json")
    if not (os.path.exists(env_path) and os.path.exists(vit_path) and os.path.exists(ev_path)):
        print(f"[{uid}] 缺少 env/vitals/events 文件，跳过事件回写")
        return False

    env_rows = _load_or_create_baseline(env_path)
    vit_rows = _load_or_create_baseline(vit_path)
    with open(ev_path, "r", encoding="utf-8") as f:
        ev_rows = json.load(f)
    if env_rows is None or vit_rows is None or not isinstance(ev_rows, list):
        print(f"[{uid}] env/vitals/events 格式异常，跳过")
        return False

    # P6: load blocked_events from persona config
    blocked = set(
        (persona.get("sleep_event_probabilities") or {}).get("blocked_events") or []
    )

    env_by_date = _build_rows_by_date(env_rows)
    vit_by_date = _build_rows_by_date(vit_rows)
    ev_by_date = _build_rows_by_date(ev_rows)

    lo = start_date or "0000-01-01"
    hi = end_date or "9999-12-31"
    for rd, day_events in ev_by_date.items():
        if rd < lo or rd > hi:
            continue
        # P6: filter blocked events before applying impacts
        if blocked:
            day_events = [
                e for e in day_events
                if str(e.get("event_type") or "") not in blocked
                and str(e.get("code") or "") not in blocked
            ]
        env_day = env_by_date.get(rd, [])
        _apply_time_context_noise_baseline(env_day)
        locked_idxs = _apply_env_impacts(day_events, env_day)
        _enforce_no_sudden_noise_drop(env_day, locked_idxs=locked_idxs)
        _apply_vitals_impacts(day_events, vit_by_date.get(rd, []))

    atomic_write_json(env_path, env_rows)
    atomic_write_json(vit_path, vit_rows)
    print(f"[{uid}] 事件影响已回写到 environment/vitals")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="将 sleep_events 影响统一回写到 environment/vitals")
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--config", default=CONFIG_PATH)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    personas = cfg.get("personas") or []
    if args.user_id:
        personas = [p for p in personas if p.get("user_id") == args.user_id]
        if not personas:
            print(f"未找到 user_id={args.user_id}")
            return

    for p in personas:
        apply_event_impacts_for_persona(p, args.start_date, args.end_date)
    print("完成。")


if __name__ == "__main__":
    main()
