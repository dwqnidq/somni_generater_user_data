"""生成可穿戴设备体征数据，并将范围写入 config。"""

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
DEFAULT_PORTRAITS_PATH = "output/personality_sleep_portraits.json"
DEFAULT_OUTPUT_DIR = "output"


def parse_num_range(text, cast=float):
    left, right = str(text).split("-")
    return cast(left.strip()), cast(right.strip())


def clamp_range(min_value, max_value, floor=None):
    if floor is not None:
        min_value = max(floor, min_value)
        max_value = max(floor, max_value)
    if min_value > max_value:
        min_value, max_value = max_value, min_value
    return min_value, max_value


def offset_range(rng, delta_min=0, delta_max=0, floor=None, cast=int):
    min_value = rng["min"][0] + delta_min
    max_value = rng["max"][0] + delta_max
    min_value, max_value = clamp_range(min_value, max_value, floor=floor)
    return {"min": [cast(min_value)], "max": [cast(max_value)]}


def offset_range_float(rng, delta_min=0.0, delta_max=0.0, floor=None, precision=2):
    min_value = float(rng["min"][0]) + delta_min
    max_value = float(rng["max"][0]) + delta_max
    min_value, max_value = clamp_range(min_value, max_value, floor=floor)
    return {
        "min": [round(min_value, precision)],
        "max": [round(max_value, precision)],
    }


def parse_portrait_ranges(portrait):
    physiology = portrait.get("physiology", {})
    hr_min, hr_max = parse_num_range(physiology.get("heart_rate_per_min", "55-90"), int)
    rr_min, rr_max = parse_num_range(physiology.get("respiration_per_min", "12-20"), int)
    sys_min, sys_max = parse_num_range(physiology.get("systolic_bp_mmhg", "100-130"), int)
    dia_min, dia_max = parse_num_range(physiology.get("diastolic_bp_mmhg", "65-85"), int)
    hrv_min, hrv_max = parse_num_range(physiology.get("short_term_hrv_5min", "1.55-1.90"), float)
    return {
        "heart_rate_per_min": {"min": [hr_min], "max": [hr_max]},
        "respiration_per_min": {"min": [rr_min], "max": [rr_max]},
        "systolic_bp_mmhg": {"min": [sys_min], "max": [sys_max]},
        "diastolic_bp_mmhg": {"min": [dia_min], "max": [dia_max]},
        "short_term_hrv_5min": {"min": [round(hrv_min, 3)], "max": [round(hrv_max, 3)]},
        "blood_oxygen_saturation": {"min": [95], "max": [99]},
    }


def build_wearable_ranges(portrait):
    dims = portrait.get("dimensions", {})
    chronotype = dims.get("chronotype", "M")
    sensitivity = dims.get("sensitivity", "L")
    brain_activity = dims.get("brain_activity", "C")

    ranges = parse_portrait_ranges(portrait)

    base_by_chronotype = {
        "M": {
            "step_count": {"min": [7000], "max": [14000]},
            "walking_distance_km": {"min": [4.5], "max": [11.0]},
            "running_distance_km": {"min": [1.2], "max": [6.0]},
            "floors_climbed": {"min": [8], "max": [28]},
            "active_calories_kcal": {"min": [320], "max": [900]},
            "cadence_spm": {"min": [96], "max": [130]},
            "pace_km_per_min": {"min": [0.085], "max": [0.155]},
            "noise_exposure_db": {"min": [42], "max": [76]},
            "hand_wash_count": {"min": [5], "max": [14]},
            "sunlight_minutes": {"min": [45], "max": [160]},
            "stand_time_minutes": {"min": [260], "max": [560]},
            "exercise_time_minutes": {"min": [40], "max": [125]},
            "vo2_max": {"min": [33.0], "max": [55.0]},
        },
        "E": {
            "step_count": {"min": [4500], "max": [12000]},
            "walking_distance_km": {"min": [3.2], "max": [9.2]},
            "running_distance_km": {"min": [0.8], "max": [4.8]},
            "floors_climbed": {"min": [5], "max": [20]},
            "active_calories_kcal": {"min": [260], "max": [760]},
            "cadence_spm": {"min": [90], "max": [124]},
            "pace_km_per_min": {"min": [0.075], "max": [0.145]},
            "noise_exposure_db": {"min": [44], "max": [82]},
            "hand_wash_count": {"min": [4], "max": [12]},
            "sunlight_minutes": {"min": [20], "max": [120]},
            "stand_time_minutes": {"min": [210], "max": [480]},
            "exercise_time_minutes": {"min": [30], "max": [110]},
            "vo2_max": {"min": [30.0], "max": [50.0]},
        },
    }

    wearable = json.loads(json.dumps(base_by_chronotype.get(chronotype, base_by_chronotype["M"])))

    if sensitivity == "H":
        wearable["noise_exposure_db"] = offset_range(wearable["noise_exposure_db"], -4, -6, floor=35, cast=int)
        wearable["exercise_time_minutes"] = offset_range(wearable["exercise_time_minutes"], -8, -10, floor=15, cast=int)
        wearable["vo2_max"] = offset_range_float(wearable["vo2_max"], -2.0, -3.0, floor=20.0, precision=1)
    else:
        wearable["noise_exposure_db"] = offset_range(wearable["noise_exposure_db"], 1, 2, floor=35, cast=int)
        wearable["exercise_time_minutes"] = offset_range(wearable["exercise_time_minutes"], 3, 8, floor=15, cast=int)
        wearable["vo2_max"] = offset_range_float(wearable["vo2_max"], 0.8, 1.6, floor=20.0, precision=1)

    if brain_activity == "R":
        wearable["step_count"] = offset_range(wearable["step_count"], 700, 1200, floor=1000, cast=int)
        wearable["running_distance_km"] = offset_range_float(wearable["running_distance_km"], 0.4, 0.9, floor=0.0, precision=2)
        wearable["active_calories_kcal"] = offset_range(wearable["active_calories_kcal"], 60, 120, floor=100, cast=int)
        wearable["cadence_spm"] = offset_range(wearable["cadence_spm"], 2, 4, floor=50, cast=int)
    else:
        wearable["step_count"] = offset_range(wearable["step_count"], -300, -700, floor=1000, cast=int)
        wearable["running_distance_km"] = offset_range_float(wearable["running_distance_km"], -0.2, -0.6, floor=0.0, precision=2)
        wearable["active_calories_kcal"] = offset_range(wearable["active_calories_kcal"], -30, -80, floor=100, cast=int)

    wearable.update(
        {
            "blood_oxygen_saturation_percent": ranges["blood_oxygen_saturation"],
            "blood_pressure_systolic_mmhg": ranges["systolic_bp_mmhg"],
            "blood_pressure_diastolic_mmhg": ranges["diastolic_bp_mmhg"],
            "heart_rate_realtime_bpm": ranges["heart_rate_per_min"],
            "resting_heart_rate_bpm": offset_range(ranges["heart_rate_per_min"], -16, -8, floor=40, cast=int),
            # 可穿戴 HRV 按用户要求统一采用 30-150 区间（常见单位 ms）
            "hrv": {"min": [30], "max": [150]},
            "respiration_rate_bpm": ranges["respiration_per_min"],
            "blood_pressure_requires_external_device": True,
        }
    )
    return wearable


def rand_int(range_config):
    return random.randint(int(range_config["min"][0]), int(range_config["max"][0]))


def rand_float(range_config, precision=2):
    value = random.uniform(float(range_config["min"][0]), float(range_config["max"][0]))
    return round(value, precision)


def local_to_utc_iso_z(local_dt):
    utc_dt = local_dt.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def create_record(user_profile, record_date, collected_local_dt, wearable_ranges):
    metrics = {
        "blood_oxygen_saturation": rand_int(wearable_ranges["blood_oxygen_saturation_percent"]),
        "blood_pressure_external_device_required": True,
        "blood_pressure_systolic": rand_int(wearable_ranges["blood_pressure_systolic_mmhg"]),
        "blood_pressure_diastolic": rand_int(wearable_ranges["blood_pressure_diastolic_mmhg"]),
        "heart_rate_realtime": rand_int(wearable_ranges["heart_rate_realtime_bpm"]),
        "resting_heart_rate": rand_int(wearable_ranges["resting_heart_rate_bpm"]),
        "hrv": rand_int(wearable_ranges["hrv"]),
        "respiration_rate": rand_int(wearable_ranges["respiration_rate_bpm"]),
        "vo2_max": rand_float(wearable_ranges["vo2_max"], precision=1),
        "step_count": rand_int(wearable_ranges["step_count"]),
        "walking_distance_km": rand_float(wearable_ranges["walking_distance_km"], precision=2),
        "running_distance_km": rand_float(wearable_ranges["running_distance_km"], precision=2),
        "floors_climbed": rand_int(wearable_ranges["floors_climbed"]),
        "active_calories": rand_int(wearable_ranges["active_calories_kcal"]),
        "cadence_spm": rand_int(wearable_ranges["cadence_spm"]),
        "pace_km_per_min": rand_float(wearable_ranges["pace_km_per_min"], precision=3),
        "noise_exposure_db": rand_int(wearable_ranges["noise_exposure_db"]),
        "hand_wash_count": rand_int(wearable_ranges["hand_wash_count"]),
        "sunlight_minutes": rand_int(wearable_ranges["sunlight_minutes"]),
        "stand_time_minutes": rand_int(wearable_ranges["stand_time_minutes"]),
        "exercise_time_minutes": rand_int(wearable_ranges["exercise_time_minutes"]),
    }

    if metrics["blood_pressure_systolic"] <= metrics["blood_pressure_diastolic"]:
        metrics["blood_pressure_systolic"] = metrics["blood_pressure_diastolic"] + random.randint(18, 35)

    now_text = collected_local_dt.strftime("%Y-%m-%d %H:%M:%S")

    return {
        "uid": user_profile.get("user_id", ""),
        "record_date": record_date.strftime("%Y-%m-%d"),
        "collected_at": local_to_utc_iso_z(collected_local_dt),
        "data_source": "wearable",
        "metrics": metrics,
        "device_id": f"wearable_{user_profile.get('user_id', '')[-6:]}",
        "create_time": now_text,
        "update_time": now_text,
        "session_id": user_profile.get("session_id", ""),
    }


def generate_user_records(user_profile, records_per_day):
    date_config = user_profile.get("date", {})
    start_date = datetime.strptime(date_config.get("start", "2026-04-01"), "%Y-%m-%d").date()
    end_date = datetime.strptime(date_config.get("end", "2026-04-30"), "%Y-%m-%d").date()

    wearable_ranges = user_profile.get("wearableMetrics", {})
    if not wearable_ranges:
        return []

    records = []
    current = start_date
    while current <= end_date:
        for _ in range(records_per_day):
            hour = random.randint(6, 23)
            minute = random.randint(0, 59)
            second = random.randint(0, 59)
            local_dt = datetime.combine(current, datetime.min.time()).replace(
                hour=hour, minute=minute, second=second
            )
            records.append(create_record(user_profile, current, local_dt, wearable_ranges))
        current += timedelta(days=1)

    records.sort(key=lambda item: item["collected_at"])
    return records


def ensure_wearable_ranges(config, portraits):
    portrait_map = {item.get("personality_code"): item for item in portraits}
    for user_profile in config.get("user_profiles", []):
        personality_code = user_profile.get("personalInformation", {}).get("type")
        portrait = portrait_map.get(personality_code)
        if not portrait:
            continue
        user_profile["wearableMetrics"] = build_wearable_ranges(portrait)


def main():
    parser = argparse.ArgumentParser(description="生成可穿戴体征数据并更新 config 范围")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="配置文件路径")
    parser.add_argument("--portraits", default=DEFAULT_PORTRAITS_PATH, help="人格画像路径")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录")
    parser.add_argument("--records-per-day", type=int, default=3, help="每天记录条数")
    parser.add_argument("--seed", type=int, default=20260422, help="随机种子")
    args = parser.parse_args()

    random.seed(args.seed)

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)
    with open(args.portraits, "r", encoding="utf-8") as f:
        portraits = json.load(f)

    ensure_wearable_ranges(config, portraits)

    with open(args.config, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    os.makedirs(args.output_dir, exist_ok=True)
    total_records = 0

    for user_profile in config.get("user_profiles", []):
        user_id = user_profile.get("user_id")
        if not user_id:
            continue
        records = generate_user_records(user_profile, records_per_day=max(1, args.records_per_day))
        output_file = os.path.join(args.output_dir, f"{user_id}_wearable_vitals_data.json")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        total_records += len(records)
        print(f"已生成 {output_file} ({len(records)} 条)")

    print(f"完成：共生成 {total_records} 条 wearable 体征记录。")


if __name__ == "__main__":
    main()
