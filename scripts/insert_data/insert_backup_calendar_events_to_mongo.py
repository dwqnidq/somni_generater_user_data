"""将 backup/ 目录下每个用户的 calendar_events JSON 插入 somni_schedules 集合。

流程：
  1. 扫描 backup 目录，识别所有 {uid}_calendar_events.json 文件
  2. 对每个 uid，先删除 somni_schedules 中该用户的现有数据
  3. 读取 JSON 文件，将时间字符串转为 datetime 对象
  4. 批量插入 somni_schedules

用法：
  python scripts/insert_data/insert_backup_calendar_events_to_mongo.py
  python scripts/insert_data/insert_backup_calendar_events_to_mongo.py --data-dir /path/to/backup
  python scripts/insert_data/insert_backup_calendar_events_to_mongo.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import BulkWriteError

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DATA_DIR = os.path.join(ROOT, "backup")
DEFAULT_MONGO_URI = "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive"
DB_NAME = "Fullive"
COLLECTION_NAME = "somni_schedules"
CALENDAR_EVENTS_PATTERN = re.compile(r"^([a-f0-9]+)_calendar_events\.json$")


def parse_dt(value: str | None):
    """将 ISO 时间字符串转为 datetime 对象。"""
    if not value:
        return value
    text = value.strip()
    if not text:
        return value
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return value


def scan_calendar_events_files(data_dir: str) -> dict[str, str]:
    """扫描目录，返回 {uid: filepath} 字典。"""
    result: dict[str, str] = {}
    for filename in sorted(os.listdir(data_dir)):
        m = CALENDAR_EVENTS_PATTERN.match(filename)
        if m:
            uid = m.group(1)
            result[uid] = os.path.join(data_dir, filename)
    return result


def load_events(filepath: str) -> list[dict]:
    """读取日程 JSON 文件，转换日期字段。"""
    with open(filepath, "r", encoding="utf-8") as f:
        events = json.load(f)
    for ev in events:
        ev["create_time"] = parse_dt(ev.get("create_time"))
        ev["update_time"] = parse_dt(ev.get("update_time"))
    return events


def chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i: i + size]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="backup 目录路径")
    parser.add_argument("--uri", default="", help="MongoDB URI（为空时读取环境变量）")
    parser.add_argument("--batch-size", type=int, default=500, help="批量插入大小")
    parser.add_argument("--dry-run", action="store_true", help="仅读取并打印统计，不实际写入 MongoDB")
    args = parser.parse_args()

    load_dotenv()
    mongo_uri = args.uri or os.getenv("MONGODB_URI") or DEFAULT_MONGO_URI

    # 扫描 calendar_events 文件
    print(f"扫描目录: {args.data_dir}")
    uid_files = scan_calendar_events_files(args.data_dir)
    if not uid_files:
        print("未找到 *_calendar_events.json 文件，退出。")
        sys.exit(1)
    print(f"发现 {len(uid_files)} 个用户: {list(uid_files.keys())}")

    # 读取所有日程数据
    print()
    all_events: list[dict] = []
    uid_counts: dict[str, int] = {}
    for uid, filepath in uid_files.items():
        events = load_events(filepath)
        uid_counts[uid] = len(events)
        all_events.extend(events)
        print(f"  {uid}: {len(events)} 条")
    print(f"总事件数: {len(all_events)}")

    if not all_events:
        print("无数据可插入，退出。")
        return

    if args.dry_run:
        print("\n[dry-run] 未写入 MongoDB。")
        return

    # 连接 MongoDB
    print(f"\n连接 MongoDB: {mongo_uri[:40]}...")
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=8000)
    try:
        db = client[DB_NAME]
        collection = db[COLLECTION_NAME]

        # Step 1: 删除这些 uid 的现有数据
        print(f"\n--- Step 1: 删除 {COLLECTION_NAME} 中 {len(uid_files)} 个用户的数据 ---")
        total_deleted = 0
        for uid in uid_files:
            result = collection.delete_many({"uid": uid})
            deleted = result.deleted_count
            total_deleted += deleted
            if deleted:
                print(f"  {uid}: 删除 {deleted} 条")
        print(f"  共删除 {total_deleted} 条")

        # Step 2: 插入新数据
        print(f"\n--- Step 2: 插入 {len(all_events)} 条日程数据 ---")
        inserted_total = 0
        for batch in chunked(all_events, max(1, args.batch_size)):
            try:
                result = collection.insert_many(batch, ordered=False)
                inserted_total += len(result.inserted_ids)
            except BulkWriteError as e:
                details = e.details or {}
                inserted = details.get("nInserted", 0)
                inserted_total += inserted
                write_errors = details.get("writeErrors", [])
                print(f"  批次部分写入: 已插入 {inserted} 条，错误 {len(write_errors)} 条")

        print(f"\n完成。")
        print(f"  目标集合: {COLLECTION_NAME}")
        print(f"  删除旧数据: {total_deleted} 条")
        print(f"  插入新数据: {inserted_total} 条")
    finally:
        client.close()
        print("MongoDB 连接已关闭。")


if __name__ == "__main__":
    main()
