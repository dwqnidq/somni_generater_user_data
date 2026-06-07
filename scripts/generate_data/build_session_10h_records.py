"""把 session_10h 扁平数据转换为项目标准的 vitals / environment 记录结构。

读取：
  output/session_10h_vitals.json       {ts,heart_rate,respiration_rate,body_motion,presence,heart_rate_random}
  output/session_10h_environment.json  {ts,temperature,humidity,illuminance,noise}

输出（与 {uid}_vitals_data.json / {uid}_environment_data.json 同结构）：
  output/session_10h_vitals_records.json
  output/session_10h_environment_records.json

时间规则：
  传入本地「睡着」时间（如 2026-06-01 23:00）。每条 collected_at = 本地时间往前推 8 小时（UTC，带 Z），
  逐条 +5 秒；create_time/update_time = 该条对应本地时间（UTC+8）再 +1 秒。
  record_date 由 --record-date 指定；不填则取 --start 的本地日期。

用法：
  python scripts/generate_data/build_session_10h_records.py --start "2026-06-01 23:00"
  python scripts/generate_data/build_session_10h_records.py --start "2026-06-01 23:00" --record-date 2026-06-01
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils import atomic_write_json  # noqa: E402

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
DEFAULT_UID = "69aea6d8af5e6cbf08027966"

INTERVAL_SEC = 5
UTC_OFFSET_HOURS = 8  # 本地 = UTC + 8；collected_at(UTC) = 本地 - 8h
CREATE_TIME_LAG_SEC = 1  # create_time 比 collected_at 对应本地时刻晚 1 秒

SIGNAL_STRENGTH_RANGE = (-72.0, -38.0)
DISTANCE_RANGE = (40, 180)
VITAL_SIGNS_NORMAL = 0
PRESENCE_IN_BED = 1
PRESENCE_ABSENT = 2

DEFAULT_VITALS_IN = os.path.join(OUTPUT_DIR, "session_10h_vitals.json")
DEFAULT_ENV_IN = os.path.join(OUTPUT_DIR, "session_10h_environment.json")
DEFAULT_VITALS_OUT = os.path.join(OUTPUT_DIR, "session_10h_vitals_records.json")
DEFAULT_ENV_OUT = os.path.join(OUTPUT_DIR, "session_10h_environment_records.json")


def _parse_start_local(s: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间：{s!r}（应为 'YYYY-MM-DD HH:MM' 或带秒）")


def _load_list(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path} 不是 JSON 数组")
    return data


def _utc_iso_z(dt_utc: datetime) -> str:
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _local_str(dt_local: datetime) -> str:
    return dt_local.strftime("%Y-%m-%d %H:%M:%S")


def _activity_state(body_motion: bool) -> int:
    """0无人/1静息/2安静/3动作/4持续动作。有体动→动作，静止→静息/安静。"""
    return random.choice([3, 4]) if body_motion else random.choice([1, 2])


def _sleep_state(body_motion: bool) -> int:
    """1清醒/2REM/3浅睡/4深睡/7离床。有体动偏清醒，静止偏浅睡（含少量深睡/REM）。"""
    return 1 if body_motion else random.choice([2, 3, 3, 3, 4])


def _build_vitals(rows: list[dict], uid: str, record_date: str,
                  start_utc: datetime, start_local: datetime) -> list[dict]:
    out: list[dict] = []
    for i, r in enumerate(rows):
        offset = timedelta(seconds=INTERVAL_SEC * i)
        collected_utc = start_utc + offset
        create_local = start_local + offset + timedelta(seconds=CREATE_TIME_LAG_SEC)
        create_s = _local_str(create_local)
        body_motion = bool(r.get("body_motion"))
        presence = bool(r.get("presence", True))
        metrics = {
            "respiration_rate": int(r["respiration_rate"]),
            "heart_rate": int(r["heart_rate"]),
            "presence_state": PRESENCE_IN_BED if presence else PRESENCE_ABSENT,
            "activity_state": _activity_state(body_motion),
            "nearest_target_distance": random.randint(*DISTANCE_RANGE),
            "vital_signs_state": VITAL_SIGNS_NORMAL,
            "signal_strength": round(random.uniform(*SIGNAL_STRENGTH_RANGE), 2),
            "in_bed_duration": (INTERVAL_SEC * i) // 60,
            "out_bed_duration": 0,
            "sleep_state": _sleep_state(body_motion),
        }
        out.append(
            {
                "uid": uid,
                "record_date": record_date,
                "collected_at": _utc_iso_z(collected_utc),
                "data_source": "radar",
                "metrics": metrics,
                "device_id": "",
                "session_id": "",
                "create_time": create_s,
                "update_time": create_s,
            }
        )
    return out


def _build_environment(rows: list[dict], uid: str, record_date: str,
                       start_utc: datetime, start_local: datetime) -> list[dict]:
    out: list[dict] = []
    for i, r in enumerate(rows):
        offset = timedelta(seconds=INTERVAL_SEC * i)
        collected_utc = start_utc + offset
        create_local = start_local + offset + timedelta(seconds=CREATE_TIME_LAG_SEC)
        create_s = _local_str(create_local)
        out.append(
            {
                "uid": uid,
                "session_id": "",
                "record_date": record_date,
                "collected_at": _utc_iso_z(collected_utc),
                "temperature": int(r["temperature"]),
                "humidity": int(r["humidity"]),
                "illuminance": int(r["illuminance"]),
                "noise": int(r["noise"]),
                "device_id": "",
                "create_time": create_s,
                "update_time": create_s,
            }
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将 session_10h 扁平数据转换为标准 vitals/environment 记录结构"
    )
    parser.add_argument("--start", required=True, help="本地睡着时间，如 '2026-06-01 23:00'")
    parser.add_argument("--record-date", default=None, help="record_date（YYYY-MM-DD）；不填则取 --start 的本地日期")
    parser.add_argument("--uid", default=DEFAULT_UID, help=f"两个文件共用的 uid（默认 {DEFAULT_UID}）")
    parser.add_argument("--vitals-in", default=DEFAULT_VITALS_IN)
    parser.add_argument("--env-in", default=DEFAULT_ENV_IN)
    parser.add_argument("--vitals-out", default=DEFAULT_VITALS_OUT)
    parser.add_argument("--env-out", default=DEFAULT_ENV_OUT)
    parser.add_argument("--seed", type=int, default=None, help="随机种子（用于可复现随机字段）")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    start_local = _parse_start_local(args.start)
    start_utc = start_local - timedelta(hours=UTC_OFFSET_HOURS)
    record_date = args.record_date or start_local.strftime("%Y-%m-%d")

    vitals_rows = _load_list(args.vitals_in)
    env_rows = _load_list(args.env_in)
    if len(vitals_rows) != len(env_rows):
        print(
            f"警告：vitals({len(vitals_rows)}) 与 environment({len(env_rows)}) 条数不一致，"
            f"各自按自身长度生成。"
        )

    vitals_out = _build_vitals(vitals_rows, args.uid, record_date, start_utc, start_local)
    env_out = _build_environment(env_rows, args.uid, record_date, start_utc, start_local)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    atomic_write_json(args.vitals_out, vitals_out)
    atomic_write_json(args.env_out, env_out)

    print(f"start 本地 {start_local:%Y-%m-%d %H:%M:%S} → collected_at 起 {_utc_iso_z(start_utc)}（UTC）")
    print(f"record_date = {record_date}，uid = {args.uid}，间隔 {INTERVAL_SEC}s")
    print(f"体征 {len(vitals_out)} 条 → {args.vitals_out}")
    print(f"环境 {len(env_out)} 条 → {args.env_out}")


if __name__ == "__main__":
    main()
