"""生成与 radar vitals_data.json 相同结构的数据。"""

import argparse
import json
import os
import random
from datetime import date, datetime, time, timedelta, timezone


UTC_PLUS_8 = timezone(timedelta(hours=8))


def parse_args():
    parser = argparse.ArgumentParser(description="生成 radar 体征数据 JSON")
    parser.add_argument("--uid", required=True, help="用户 uid")
    parser.add_argument("--start-date", default="2026-03-01", help="开始日期，格式 YYYY-MM-DD")
    parser.add_argument("--end-date", default="2026-03-01", help="结束日期，格式 YYYY-MM-DD")
    parser.add_argument("--records-per-day", type=int, default=36, help="每天生成记录条数")
    parser.add_argument("--seed", type=int, default=20260423, help="随机种子")
    parser.add_argument("--data-source", default="radar", help="数据来源字段值")
    parser.add_argument("--device-id", default="", help="device_id 字段值")
    parser.add_argument("--session-id", default="", help="session_id 字段值")
    parser.add_argument(
        "--output",
        default="output/{uid}_vitals_data.json",
        help="输出文件路径，支持 {uid} 占位符",
    )
    return parser.parse_args()


def random_collected_local_dt(record_day: date) -> datetime:
    second_of_day = random.randint(0, 24 * 60 * 60 - 1)
    return datetime.combine(record_day, time.min, tzinfo=UTC_PLUS_8) + timedelta(seconds=second_of_day)


def to_collected_at_utc_z(local_dt: datetime) -> str:
    return local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def random_metrics() -> dict:
    systolic = random.randint(95, 135)
    diastolic = random.randint(58, 90)
    if systolic <= diastolic:
        systolic = diastolic + random.randint(10, 25)

    return {
        "respiration_rate": random.randint(8, 32),
        "heart_rate": round(random.uniform(40, 120), 6),
        "body_motion_level": random.randint(0, 25),
        "blood_oxygen": random.randint(92, 100),
        "blood_pressure_systolic": systolic,
        "blood_pressure_diastolic": diastolic,
        "hrv": round(random.uniform(1.0, 3.5), 3),
    }


def generate_records(
    uid: str,
    start_day: date,
    end_day: date,
    records_per_day: int,
    data_source: str,
    device_id: str,
    session_id: str,
) -> list:
    records = []
    current_day = start_day
    while current_day <= end_day:
        for _ in range(max(1, records_per_day)):
            collected_local_dt = random_collected_local_dt(current_day)
            create_local_dt = collected_local_dt + timedelta(seconds=1)
            create_text = create_local_dt.strftime("%Y-%m-%d %H:%M:%S")

            records.append(
                {
                    "uid": uid,
                    "record_date": current_day.strftime("%Y-%m-%d"),
                    "collected_at": to_collected_at_utc_z(collected_local_dt),
                    "data_source": data_source,
                    "metrics": random_metrics(),
                    "device_id": device_id,
                    "create_time": create_text,
                    "update_time": create_text,
                    "session_id": session_id,
                }
            )
        current_day += timedelta(days=1)

    records.sort(key=lambda item: item["collected_at"])
    return records


def main():
    args = parse_args()
    random.seed(args.seed)

    start_day = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end_day = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    if end_day < start_day:
        raise ValueError("end-date 不能早于 start-date")

    records = generate_records(
        uid=args.uid,
        start_day=start_day,
        end_day=end_day,
        records_per_day=args.records_per_day,
        data_source=args.data_source,
        device_id=args.device_id,
        session_id=args.session_id,
    )

    output_path = args.output.format(uid=args.uid)
    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"已生成: {output_path}")
    print(f"总记录数: {len(records)}")
    print("respiration_rate 范围: 8-32")
    print("heart_rate 范围: 40-120")


if __name__ == "__main__":
    main()
