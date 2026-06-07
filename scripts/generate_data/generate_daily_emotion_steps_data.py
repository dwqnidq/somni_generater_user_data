"""根据 config/health_data_personas_config.json 生成每日情绪分与步数记录。

每条记录字段：
  event_date — 日历日 YYYY-MM-DD
  uid — 人格配置中的 user_id
  create_time / update_time — UTC ISO 字符串（入库脚本可转为 ISODate）
  score — 情绪分，在 70～95（含）之间均匀随机
  steps — 在当日 sleep 状态（good/bad）对应配置的 daily_steps [min,max] 内随机整数

输出：output/{uid}_daily_emotion_steps.json

用法：
  python scripts/generate_data/generate_daily_emotion_steps_data.py
  python scripts/generate_data/generate_daily_emotion_steps_data.py --user-id 69aea6f3af5e6cbf0802796a
  python scripts/generate_data/generate_daily_emotion_steps_data.py --state good
  python scripts/generate_data/generate_daily_emotion_steps_data.py --overwrite
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "health_data_personas_config.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")


def _randint_range(rng: list) -> int:
    lo, hi = int(rng[0]), int(rng[1])
    return random.randint(min(lo, hi), max(lo, hi))


def _utc_ts_iso(now_utc: datetime) -> str:
    """与健康数据脚本一致：UTC 时间 ISO 字符串，微秒 6 位 + Z"""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)
    return now_utc.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _date_range(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _build_day_states(
    total_days: int,
    state_mode: str,
    good_ratio: float,
    max_bad_days_per_week: int,
) -> list[str]:
    if state_mode == "good":
        return ["good"] * total_days
    if state_mode == "bad":
        return ["bad"] * total_days
    day_states: list[str] = []
    i = 0
    cap = max(0, max_bad_days_per_week)
    while i < total_days:
        week_size = min(7, total_days - i)
        expected_bad = round((1 - good_ratio) * week_size)
        bad_count = min(cap, max(0, expected_bad))
        week_states = ["bad"] * bad_count + ["good"] * (week_size - bad_count)
        random.shuffle(week_states)
        day_states.extend(week_states)
        i += week_size
    return day_states


def generate_records_for_persona(
    persona: dict,
    day_states: list[str],
    start_dt: date,
    end_dt: date,
    now_utc: datetime,
) -> list[dict]:
    uid = persona["user_id"]
    states = persona["sleep_metric_states"]
    ts = _utc_ts_iso(now_utc)
    out: list[dict] = []
    for cur, state_key in zip(_date_range(start_dt, end_dt), day_states):
        state_cfg = states[state_key]
        steps_rng = state_cfg.get("daily_steps")
        if not steps_rng or len(steps_rng) < 2:
            raise KeyError(
                f"人格 {persona.get('name')} 状态 {state_key} 缺少 daily_steps [min,max]"
            )
        steps = _randint_range(steps_rng)
        score = random.randint(70, 95)
        out.append({
            "event_date": cur.strftime("%Y-%m-%d"),
            "uid": uid,
            "create_time": ts,
            "score": score,
            "steps": steps,
            "update_time": ts,
        })
    return out


def generate_persona_file(
    persona: dict,
    state_mode: str,
    good_ratio: float,
    max_bad_days_per_week: int,
    start_date_override: date | None,
    end_date_override: date | None,
    overwrite: bool,
) -> str:
    uid = persona["user_id"]
    name = persona.get("name", uid)
    date_range = persona.get("date_range", {})
    start_str = date_range["start"]
    end_str = date_range["end"]
    config_start = datetime.strptime(start_str, "%Y-%m-%d").date()
    config_end = datetime.strptime(end_str, "%Y-%m-%d").date()
    from date_range_helpers import apply_date_range_overrides  # noqa: WPS433

    merged = apply_date_range_overrides(
        config_start, config_end, start_date_override, end_date_override
    )
    if not merged:
        print(f"[{name}] 日期范围无效，跳过")
        return ""
    start_dt, end_dt = merged

    out_file = os.path.join(OUTPUT_DIR, f"{uid}_daily_emotion_steps.json")
    if not overwrite and os.path.exists(out_file):
        print(f"[{name}] 文件已存在，跳过（使用 --overwrite 强制重新生成）")
        return out_file

    total_days = (end_dt - start_dt).days + 1
    day_states = _build_day_states(
        total_days, state_mode, good_ratio, max_bad_days_per_week
    )
    now_utc = datetime.now(timezone.utc)
    records = generate_records_for_persona(
        persona, day_states, start_dt, end_dt, now_utc
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"[{name}] 已生成 {len(records)} 条 → {out_file}")
    return out_file


def generate_daily_emotion_steps_for_personas(
    personas: list[dict],
    *,
    state_mode: str,
    good_ratio: float,
    max_bad_days_per_week: int,
    start_date_override: date | None,
    end_date_override: date | None,
    overwrite: bool,
) -> None:
    """供 main.py 等批量调用；逻辑与 CLI 单用户循环一致。"""
    for persona in personas:
        generate_persona_file(
            persona=persona,
            state_mode=state_mode,
            good_ratio=good_ratio,
            max_bad_days_per_week=max_bad_days_per_week,
            start_date_override=start_date_override,
            end_date_override=end_date_override,
            overwrite=overwrite,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="根据 health_data_personas_config.json 生成每日情绪分与步数 JSON"
    )
    parser.add_argument("--user-id", default=None, help="仅生成指定 user_id")
    parser.add_argument(
        "--state",
        choices=["good", "bad", "mixed"],
        default=None,
        help="good/bad/mixed；默认读取 config.generation.day_state_mix.mode",
    )
    parser.add_argument(
        "--good-ratio",
        type=float,
        default=None,
        help="mixed 下好天占比；默认读取 config.generation.day_state_mix.good_ratio",
    )
    parser.add_argument(
        "--max-bad-days-per-week",
        type=int,
        default=None,
        help="mixed 下每周最多坏天数；默认读取 day_state_mix.max_bad_days_per_week",
    )
    parser.add_argument("--start-date", default=None, help="覆盖起始日 YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="覆盖结束日 YYYY-MM-DD")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--config", default=CONFIG_PATH)
    parser.add_argument("--seed", type=int, default=None, help="随机种子（可复现）")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    mix = (config.get("generation") or {}).get("day_state_mix") or {}
    state_mode = args.state or str(mix.get("mode", "mixed"))
    if state_mode not in ("good", "bad", "mixed"):
        state_mode = "mixed"
    good_ratio = (
        float(args.good_ratio)
        if args.good_ratio is not None
        else float(mix.get("good_ratio", 0.5))
    )
    max_bad = (
        int(args.max_bad_days_per_week)
        if args.max_bad_days_per_week is not None
        else int(mix.get("max_bad_days_per_week", 3))
    )

    personas = config["personas"]
    if args.user_id:
        personas = [p for p in personas if p["user_id"] == args.user_id]
        if not personas:
            print(f"未找到 user_id={args.user_id}")
            sys.exit(1)

    start_ov = (
        datetime.strptime(args.start_date, "%Y-%m-%d").date()
        if args.start_date
        else None
    )
    end_ov = (
        datetime.strptime(args.end_date, "%Y-%m-%d").date()
        if args.end_date
        else None
    )

    for persona in personas:
        generate_persona_file(
            persona=persona,
            state_mode=state_mode,
            good_ratio=good_ratio,
            max_bad_days_per_week=max_bad,
            start_date_override=start_ov,
            end_date_override=end_ov,
            overwrite=args.overwrite,
        )

    print("\n全部完成。")


if __name__ == "__main__":
    main()
