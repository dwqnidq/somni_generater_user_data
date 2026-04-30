"""生成血压计数据（data_source=sphygmomanometer，仅含收缩压/舒张压）。"""

import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)


DEFAULT_CONFIG_PATH = "config/config.json"
DEFAULT_OUTPUT_DIR = "output"


def local_to_utc_iso_z(local_dt):
    utc_dt = local_dt.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_range(range_obj, default_min, default_max):
    min_val = int(range_obj.get("min", [default_min])[0])
    max_val = int(range_obj.get("max", [default_max])[0])
    if min_val > max_val:
        min_val, max_val = max_val, min_val
    return {"min": [min_val], "max": [max_val]}


def get_bp_ranges(user_profile):
    wearable = user_profile.get("wearableMetrics", {})
    if "blood_pressure_systolic_mmhg" in wearable and "blood_pressure_diastolic_mmhg" in wearable:
        sys_range = normalize_range(wearable["blood_pressure_systolic_mmhg"], 100, 130)
        dia_range = normalize_range(wearable["blood_pressure_diastolic_mmhg"], 65, 85)
        return sys_range, dia_range

    fitness = user_profile.get("fitness", {})
    sys_range = normalize_range(fitness.get("systolicPressure", {}), 100, 130)
    dia_range = normalize_range(fitness.get("diastolicPressure", {}), 65, 85)
    return sys_range, dia_range


def create_record(user_profile, record_date, collected_local_dt, sys_range, dia_range):
    systolic = random.randint(sys_range["min"][0], sys_range["max"][0])
    diastolic = random.randint(dia_range["min"][0], dia_range["max"][0])
    if systolic <= diastolic:
        systolic = diastolic + random.randint(18, 35)

    now_text = collected_local_dt.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "uid": user_profile.get("user_id", ""),
        "record_date": record_date.strftime("%Y-%m-%d"),
        "collected_at": local_to_utc_iso_z(collected_local_dt),
        "data_source": "sphygmomanometer",
        "metrics": {
            "blood_pressure_systolic": systolic,
            "blood_pressure_diastolic": diastolic,
        },
        "device_id": f"sphygmomanometer_{user_profile.get('user_id', '')[-6:]}",
        "create_time": now_text,
        "update_time": now_text,
        "session_id": user_profile.get("session_id", ""),
    }


def generate_user_records(user_profile, records_per_day):
    date_cfg = user_profile.get("date", {})
    start_date = datetime.strptime(date_cfg.get("start", "2026-04-01"), "%Y-%m-%d").date()
    end_date = datetime.strptime(date_cfg.get("end", "2026-04-30"), "%Y-%m-%d").date()
    sys_range, dia_range = get_bp_ranges(user_profile)

    records = []
    current = start_date
    while current <= end_date:
        for _ in range(records_per_day):
            local_dt = datetime.combine(current, datetime.min.time()).replace(
                hour=random.randint(6, 23),
                minute=random.randint(0, 59),
                second=random.randint(0, 59),
            )
            records.append(create_record(user_profile, current, local_dt, sys_range, dia_range))
        current += timedelta(days=1)

    records.sort(key=lambda item: item["collected_at"])
    return records


def main():
    parser = argparse.ArgumentParser(description="生成血压计数据")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="配置文件路径")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录")
    parser.add_argument("--records-per-day", type=int, default=3, help="每天记录条数")
    parser.add_argument("--seed", type=int, default=20260422, help="随机种子")
    args = parser.parse_args()

    random.seed(args.seed)

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    os.makedirs(args.output_dir, exist_ok=True)
    total = 0

    for user_profile in config.get("user_profiles", []):
        user_id = user_profile.get("user_id")
        if not user_id:
            continue
        records = generate_user_records(user_profile, max(1, args.records_per_day))
        output_file = os.path.join(args.output_dir, f"{user_id}_sphygmomanometer_data.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        total += len(records)
        print(f"已生成 {output_file} ({len(records)} 条)")

    print(f"完成：共生成 {total} 条 sphygmomanometer 记录。")


if __name__ == "__main__":
    main()
