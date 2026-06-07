#!/usr/bin/env python3
"""将 output/{uid}_calendar_events.json 中的 event_name 同步到 Mongo somni_schedules。

仅按 uid + event_date + event_type + start_time + end_time 匹配并更新 event_name；
不删除、不新增记录。

用法：
  python scripts/insert_data/update_calendar_event_names_in_mongo.py --dry-run
  python scripts/insert_data/update_calendar_event_names_in_mongo.py
  python scripts/insert_data/update_calendar_event_names_in_mongo.py --uid 69aea593af5e6cbf08027964
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from pymongo import MongoClient, UpdateOne

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from mongo_persona_output_specs import (  # noqa: E402
    load_persona_uids,
    resolve_mongo_uri,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DEFAULT_DATA_DIR = os.path.join(PROJECT_ROOT, "output")
COLLECTION_NAME = "somni_schedules"
MATCH_FIELDS = ("uid", "event_date", "event_type", "start_time", "end_time")


def calendar_path(data_dir: str, uid: str) -> str:
    return os.path.join(data_dir, f"{uid}_calendar_events.json")


def build_filter(row: dict) -> dict:
    return {key: row[key] for key in MATCH_FIELDS}


def sync_uid(
    collection,
    uid: str,
    data_dir: str,
    *,
    dry_run: bool,
) -> dict[str, int]:
    path = calendar_path(data_dir, uid)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    with open(path, encoding="utf-8") as f:
        rows = json.load(f)

    stats = {
        "local": len(rows),
        "matched": 0,
        "updated": 0,
        "already_ok": 0,
        "not_found": 0,
        "ambiguous": 0,
    }
    ops: list[UpdateOne] = []

    for row in rows:
        filt = build_filter(row)
        new_name = row["event_name"]
        cursor = list(
            collection.find(filt, {"_id": 1, "event_name": 1}).limit(2)
        )
        if not cursor:
            stats["not_found"] += 1
            continue
        if len(cursor) > 1:
            stats["ambiguous"] += 1
            continue

        stats["matched"] += 1
        old_name = cursor[0].get("event_name")
        if old_name == new_name:
            stats["already_ok"] += 1
            continue

        stats["updated"] += 1
        if not dry_run:
            ops.append(
                UpdateOne(
                    {"_id": cursor[0]["_id"]},
                    {"$set": {"event_name": new_name}},
                )
            )

    if ops and not dry_run:
        for i in range(0, len(ops), 500):
            collection.bulk_write(ops[i : i + 500], ordered=False)

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--uid", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    uids = load_persona_uids()
    if args.uid.strip():
        uids = [u for u in uids if u == args.uid.strip()]

    try:
        uri, db_name = resolve_mongo_uri()
        client = MongoClient(uri, serverSelectionTimeoutMS=15000)
        client.admin.command("ping")
        collection = client[db_name][COLLECTION_NAME]
    except Exception as exc:
        print(f"MongoDB 连接失败: {exc}", file=sys.stderr)
        return 2

    mode = "[dry-run]" if args.dry_run else "[apply]"
    print(f"{mode} 数据库={db_name} 集合={COLLECTION_NAME}")
    print(f"数据目录={args.data_dir}\n")

    all_ok = True
    for uid in uids:
        try:
            stats = sync_uid(
                collection, uid, args.data_dir, dry_run=args.dry_run
            )
        except FileNotFoundError as exc:
            print(f"[错误] {uid}: {exc}")
            all_ok = False
            continue

        tag = "OK"
        if stats["not_found"] or stats["ambiguous"]:
            tag = "WARN"
            all_ok = False
        print(
            f"[{tag}] {uid}: 本地={stats['local']} "
            f"匹配={stats['matched']} 将更新={stats['updated']} "
            f"已一致={stats['already_ok']} 未找到={stats['not_found']} "
            f"重复匹配={stats['ambiguous']}"
        )

    client.close()
    if args.dry_run:
        print("\n未写入；去掉 --dry-run 后执行更新。")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
