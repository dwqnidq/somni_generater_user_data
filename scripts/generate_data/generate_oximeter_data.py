"""生成血氧仪数据（data_source=oximeter，仅含血氧指标）。"""

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


def get_blood_oxygen_range(user_profile):
    wearable = user_profile.get("wearableMetrics", {})
    if "blood_oxygen_saturation_percent" in wearable:
        return normalize_range(wearable["blood_oxygen_saturation_percent"], 95, 99)

    fitness = user_profile.get("fitness", {})
    if "bloodOxygen" in fitness:
        return normalize_range(fitness["bloodOxygen"], 95, 99)

    return {"min": [95], "max": [99]}


def create_record(user_profile, record_date, collected_local_dt, bo_range):
    now_text = collected_local_dt.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "uid": user_profile.get("user_id", ""),
        "record_date": record_date.strftime("%Y-%m-%d"),
        "collected_at": local_to_utc_iso_z(collected_local_dt),
        "data_source": "oximeter",
        "metrics": {
            "blood_oxygen_saturation": random.randint(bo_range["min"][0], bo_range["max"][0])
        },
        "device_id": f"oximeter_{user_profile.get('user_id', '')[-6:]}",
        "create_time": now_text,
        "update_time": now_text,
        "session_id": user_profile.get("session_id", ""),
    }


def generate_user_records(user_profile, records_per_day):
    date_cfg = user_profile.get("date", {})
    start_date = datetime.strptime(date_cfg.get("start", "2026-04-01"), "%Y-%m-%d").date()
    end_date = datetime.strptime(date_cfg.get("end", "2026-04-30"), "%Y-%m-%d").date()
    bo_range = get_blood_oxygen_range(user_profile)

    records = []
    current = start_date
    while current <= end_date:
        for _ in range(records_per_day):
            local_dt = datetime.combine(current, datetime.min.time()).replace(
                hour=random.randint(6, 23),
                minute=random.randint(0, 59),
                second=random.randint(0, 59),
            )
            records.append(create_record(user_profile, current, local_dt, bo_range))
        current += timedelta(days=1)

    records.sort(key=lambda item: item["collected_at"])
    return records


def main():
    parser = argparse.ArgumentParser(description="生成血氧仪数据")
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
        output_file = os.path.join(args.output_dir, f"{user_id}_oximeter_data.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        total += len(records)
        print(f"已生成 {output_file} ({len(records)} 条)")

    print(f"完成：共生成 {total} 条 oximeter 记录。")


if __name__ == "__main__":
    main()
