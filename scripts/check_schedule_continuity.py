"""
查询 somni_schedules 集合中 8 个人格在下周四 (2026-05-28) 的日程数据，
检查时间点是否连贯（前一个事件的 end_time == 后一个事件的 start_time）。
"""

import os
from datetime import datetime
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or \
    "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive"

TARGET_DATE = "2026-05-28"

PERSONAS = [
    ("69aea593af5e6cbf08027964", "完美主义百灵鸟 (M-H-R)"),
    ("69aea63eaf5e6cbf08027965", "敏感的晨间鹿 (M-H-C)"),
    ("69aea6d8af5e6cbf08027966", "效率至上考拉 (M-L-R)"),
    ("69aea6e3af5e6cbf08027967", "阳光漫步者 (M-L-C)"),
    ("69aea6e8af5e6cbf08027968", "深夜灵感守望者 (E-H-R)"),
    ("69aea6eeaf5e6cbf08027969", "深海独奏家 (E-H-C)"),
    ("69aea6f3af5e6cbf0802796a", "创意夜猫子 (E-L-R)"),
    ("69aea6f8af5e6cbf0802796b", "月光冲浪者 (E-L-C)"),
]


def time_to_minutes(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def check_continuity(events: list) -> list:
    """检查事件列表的时间连贯性，返回所有断点"""
    gaps = []
    for i in range(len(events) - 1):
        curr_end = events[i]["end_time"]
        next_start = events[i + 1]["start_time"]
        if curr_end != next_start:
            gap_min = time_to_minutes(next_start) - time_to_minutes(curr_end)
            gaps.append({
                "between": f"{events[i]['event_name']} ({curr_end}) → {events[i+1]['event_name']} ({next_start})",
                "gap_minutes": gap_min,
            })
    return gaps


def main():
    client = MongoClient(MONGO_URI)
    db = client.get_default_database()
    collection = db["somni_schedules"]

    print(f"查询日期: {TARGET_DATE}")
    print(f"集合: somni_schedules")
    print("=" * 80)

    for uid, label in PERSONAS:
        print(f"\n{'─' * 60}")
        print(f"人格: {label}  uid: {uid}")
        print(f"{'─' * 60}")

        docs = list(collection.find({
            "uid": uid,
            "event_date": TARGET_DATE,
            "language": "zh",
        }).sort("start_time", 1))

        if not docs:
            print("  ⚠ 无数据")
            continue

        print(f"  共 {len(docs)} 个事件:\n")
        for d in docs:
            print(f"  {d['start_time']} - {d['end_time']}  "
                  f"[{d['event_type']}] {d['event_name']}  "
                  f"({d['duration_minutes']}min)")

        gaps = check_continuity(docs)
        print()
        if not gaps:
            print("  ✓ 时间完全连贯，无断点")
        else:
            print(f"  ✗ 发现 {len(gaps)} 个时间断点:")
            for g in gaps:
                sign = "+" if g["gap_minutes"] > 0 else ""
                print(f"    - {g['between']}  间隔: {sign}{g['gap_minutes']} 分钟")

    client.close()


if __name__ == "__main__":
    main()
