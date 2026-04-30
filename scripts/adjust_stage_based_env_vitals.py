import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output"


# 参考《睡眠过程环境体征变化.md》的分期范围（简化为可执行区间）
STAGE_RULES = {
    "awake": {
        "env": {
            "temperature": (20, 24),
            "humidity": (42, 60),
            "illuminance": (8, 50),
            "noise": (30, 40),
        },
        "vitals": {
            "respiration_rate": (14, 18),
            "heart_rate": (58, 74),
            "body_motion_level": (20, 45),
            "blood_oxygen": (95, 99),
            "blood_pressure_systolic": (112, 126),
            "blood_pressure_diastolic": (72, 82),
            "hrv": (1.55, 1.85),
        },
    },
    "light": {
        "env": {
            "temperature": (19, 22),
            "humidity": (45, 58),
            "illuminance": (0, 5),
            "noise": (28, 35),
        },
        "vitals": {
            "respiration_rate": (12, 16),
            "heart_rate": (50, 65),
            "body_motion_level": (8, 20),
            "blood_oxygen": (95, 99),
            "blood_pressure_systolic": (102, 116),
            "blood_pressure_diastolic": (62, 74),
            "hrv": (1.75, 2.05),
        },
    },
    "deep": {
        "env": {
            "temperature": (18, 21),
            "humidity": (46, 56),
            "illuminance": (0, 2),
            "noise": (25, 33),
        },
        "vitals": {
            "respiration_rate": (10, 14),
            "heart_rate": (40, 55),
            "body_motion_level": (1, 8),
            "blood_oxygen": (96, 99),
            "blood_pressure_systolic": (96, 110),
            "blood_pressure_diastolic": (58, 68),
            "hrv": (1.95, 2.35),
        },
    },
    "rem": {
        "env": {
            "temperature": (19, 22),
            "humidity": (45, 56),
            "illuminance": (0, 1),
            "noise": (22, 30),
        },
        "vitals": {
            "respiration_rate": (12, 20),
            "heart_rate": (50, 75),
            "body_motion_level": (0, 5),
            "blood_oxygen": (95, 98),
            "blood_pressure_systolic": (108, 124),
            "blood_pressure_diastolic": (68, 82),
            "hrv": (1.50, 2.20),
        },
    },
}


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, data):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def hhmm_to_minutes(hhmm: str) -> Optional[int]:
    if not hhmm or ":" not in hhmm:
        return None
    parts = hhmm.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour * 60 + minute


def parse_collected_at(iso_utc: str) -> Optional[datetime]:
    if not iso_utc:
        return None
    try:
        dt = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    # 当前数据中 create_time 比 collected_at 快 8 小时；按北京时间转换后再与 idf_data 比较
    return dt + timedelta(hours=8)


def build_stage_windows(record_date: str, idf_data: List[dict]) -> List[Tuple[datetime, datetime, str]]:
    base = datetime.strptime(record_date, "%Y-%m-%d")
    windows: List[Tuple[datetime, datetime, str]] = []
    prev_start_minutes = None
    day_offset = 0

    for seg in idf_data:
        stage = str(seg.get("stage") or "").strip().lower()
        start_m = hhmm_to_minutes(str(seg.get("start") or ""))
        end_m = hhmm_to_minutes(str(seg.get("end") or ""))
        if stage not in STAGE_RULES or start_m is None or end_m is None:
            continue

        if prev_start_minutes is not None and start_m < prev_start_minutes:
            day_offset += 1
        start_dt = base + timedelta(days=day_offset, minutes=start_m)

        end_offset = day_offset
        if end_m < start_m:
            end_offset += 1
        end_dt = base + timedelta(days=end_offset, minutes=end_m)
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(minutes=1)

        windows.append((start_dt, end_dt, stage))
        prev_start_minutes = start_m

    return windows


def pick_stage(dt_local: datetime, windows: List[Tuple[datetime, datetime, str]]) -> Optional[str]:
    for start_dt, end_dt, stage in windows:
        if start_dt <= dt_local < end_dt:
            return stage
    return None


def _stable_u64(text: str) -> int:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def stable_value(key: str, low, high):
    seed = _stable_u64(key)
    if isinstance(low, int) and isinstance(high, int):
        span = high - low + 1
        return low + (seed % span)
    # float
    ratio = (seed % 1_000_000) / 1_000_000.0
    return round(low + (high - low) * ratio, 3)


def apply_env_rules(row: dict, stage: str):
    env_rule = STAGE_RULES[stage]["env"]
    ts = str(row.get("collected_at") or "")
    for k, bounds in env_rule.items():
        row[k] = stable_value(f"env|{stage}|{ts}|{k}", bounds[0], bounds[1])


def apply_vitals_rules(row: dict, stage: str):
    vitals_rule = STAGE_RULES[stage]["vitals"]
    ts = str(row.get("collected_at") or "")
    metrics = row.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
        row["metrics"] = metrics

    for k, bounds in vitals_rule.items():
        metrics[k] = stable_value(f"vitals|{stage}|{ts}|{k}", bounds[0], bounds[1])

    # 一点弱生理约束：舒张压始终小于收缩压
    sbp = int(metrics.get("blood_pressure_systolic", 110))
    dbp = int(metrics.get("blood_pressure_diastolic", 70))
    if dbp >= sbp:
        metrics["blood_pressure_diastolic"] = sbp - 8


def build_record_date_stage_windows(health_rows: List[dict]) -> Dict[str, List[Tuple[datetime, datetime, str]]]:
    mapping: Dict[str, List[Tuple[datetime, datetime, str]]] = {}
    for row in health_rows:
        record_date = row.get("record_date")
        idf_data = row.get("idf_data") or []
        if not record_date or not isinstance(idf_data, list):
            continue
        windows = build_stage_windows(record_date, idf_data)
        if windows:
            mapping[record_date] = windows
    return mapping


def process_user_files(user_id: str) -> Tuple[int, int]:
    health_path = OUTPUT_DIR / f"{user_id}_health_data.json"
    env_path = OUTPUT_DIR / f"{user_id}_environment_data.json"
    vitals_path = OUTPUT_DIR / f"{user_id}_vitals_data.json"
    if not (health_path.exists() and env_path.exists() and vitals_path.exists()):
        return 0, 0

    health_rows = load_json(health_path)
    env_rows = load_json(env_path)
    vitals_rows = load_json(vitals_path)
    stage_windows_map = build_record_date_stage_windows(health_rows)

    env_updates = 0
    for row in env_rows:
        record_date = row.get("record_date")
        windows = stage_windows_map.get(record_date) or []
        dt_local = parse_collected_at(str(row.get("collected_at") or ""))
        if not windows or dt_local is None:
            continue
        stage = pick_stage(dt_local, windows)
        if not stage:
            continue
        apply_env_rules(row, stage)
        env_updates += 1

    vitals_updates = 0
    for row in vitals_rows:
        record_date = row.get("record_date")
        windows = stage_windows_map.get(record_date) or []
        dt_local = parse_collected_at(str(row.get("collected_at") or ""))
        if not windows or dt_local is None:
            continue
        stage = pick_stage(dt_local, windows)
        if not stage:
            continue
        apply_vitals_rules(row, stage)
        vitals_updates += 1

    if env_updates > 0:
        dump_json(env_path, env_rows)
    if vitals_updates > 0:
        dump_json(vitals_path, vitals_rows)
    return env_updates, vitals_updates


def main():
    health_files = sorted(OUTPUT_DIR.glob("*_health_data.json"))
    if not health_files:
        print("未找到 health_data 文件。")
        return

    total_env = 0
    total_vitals = 0
    for hp in health_files:
        user_id = hp.name.replace("_health_data.json", "")
        env_count, vitals_count = process_user_files(user_id)
        total_env += env_count
        total_vitals += vitals_count
        if env_count or vitals_count:
            print(f"{user_id}: environment={env_count}, vitals={vitals_count}")

    print(f"完成：environment 总更新 {total_env} 条，vitals 总更新 {total_vitals} 条。")


if __name__ == "__main__":
    main()
