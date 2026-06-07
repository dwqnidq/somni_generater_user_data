"""将 output/calendar_events/ 下 8 个人格的日程数据插入 somni_schedules 集合。

流程：
  1. 删除 somni_schedules 中这 8 个 uid 的现有数据
  2. 读取 JSON 文件，将时间字符串转为 datetime 对象
  3. 批量插入 somni_schedules

用法：
  python scripts/insert_data/insert_calendar_events_to_mongo.py
  python scripts/insert_data/insert_calendar_events_to_mongo.py --data-dir output/calendar_events
  python scripts/insert_data/insert_calendar_events_to_mongo.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import BulkWriteError

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DATA_DIR = os.path.join(ROOT, "output", "calendar_events")
DEFAULT_CONFIG = os.path.join(ROOT, "config", "health_data_personas_config.json")
DEFAULT_MONGO_URI = "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive"
DB_NAME = "Fullive"
COLLECTION_NAME = "somni_schedules"


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


def load_persona_uids(config_path: str) -> list[str]:
    """从人格配置中读取 8 个 user_id。"""
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    personas = cfg.get("personas") or []
    return [str(p["user_id"]) for p in personas if p.get("user_id")]


def load_events(data_dir: str, uids: list[str]) -> list[dict]:
    """读取所有日程 JSON 文件，转换日期字段。"""
    all_events: list[dict] = []
    for uid in uids:
        path = os.path.join(data_dir, f"{uid}_calendar_events.json")
        if not os.path.isfile(path):
            print(f"  [跳过] 文件不存在: {path}")
            continue
        with open(path, "r", encoding="utf-8") as f:
            events = json.load(f)
        for ev in events:
            ev["create_time"] = parse_dt(ev.get("create_time"))
            ev["update_time"] = parse_dt(ev.get("update_time"))
        all_events.extend(events)
        print(f"  {uid}: {len(events)} 条")
    return all_events


def chunked(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i: i + size]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help="日程 JSON 文件目录")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="人格配置文件路径")
    parser.add_argument("--uri", default="", help="MongoDB URI（为空时读取环境变量）")
    parser.add_argument("--batch-size", type=int, default=500, help="批量插入大小")
    parser.add_argument("--dry-run", action="store_true", help="仅读取并打印统计，不实际写入 MongoDB")
    args = parser.parse_args()

    load_dotenv()
    mongo_uri = args.uri or os.getenv("MONGODB_URI") or DEFAULT_MONGO_URI

    # 读取人格 uid
    print(f"读取人格配置: {args.config}")
    uids = load_persona_uids(args.config)
    print(f"人格列表 ({len(uids)} 个): {uids}")

    # 读取日程数据
    print(f"\n读取日程文件: {args.data_dir}")
    events = load_events(args.data_dir, uids)
    print(f"总事件数: {len(events)}")

    if not events:
        print("无数据可插入，退出。")
        return

    # 统计每个 uid 的事件数
    uid_counts: dict[str, int] = {}
    for ev in events:
        uid = ev.get("uid", "")
        uid_counts[uid] = uid_counts.get(uid, 0) + 1
    print("\n各人格事件数:")
    for uid, count in uid_counts.items():
        print(f"  {uid}: {count} 条")

    if args.dry_run:
        print("\n[dry-run] 未写入 MongoDB。")
        return

    # 连接 MongoDB
    print(f"\n连接 MongoDB: {mongo_uri[:40]}...")
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=8000)
    try:
        db = client[DB_NAME]
        collection = db[COLLECTION_NAME]

        # Step 1: 删除这 8 个 uid 的现有数据
        print(f"\n--- Step 1: 删除 {COLLECTION_NAME} 中 8 个用户的数据 ---")
        total_deleted = 0
        for uid in uids:
            result = collection.delete_many({"uid": uid})
            deleted = result.deleted_count
            total_deleted += deleted
            if deleted:
                print(f"  {uid}: 删除 {deleted} 条")
        print(f"  共删除 {total_deleted} 条")

        # Step 2: 插入新数据
        print(f"\n--- Step 2: 插入 {len(events)} 条日程数据 ---")
        inserted_total = 0
        for batch in chunked(events, max(1, args.batch_size)):
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
