#!/usr/bin/env python3
"""统计 somni_physiological_data 中 device_id 匹配的用户（按 uid 去重，有一条即算）。

默认在八人格 uid 范围内统计；加 --all-users 则统计全库。

用法（项目根目录）:
  python scripts/query_physiological_users_by_device_id.py
  python scripts/query_physiological_users_by_device_id.py --device-id somni_device_009
  python scripts/query_physiological_users_by_device_id.py --all-users
"""

from __future__ import annotations

import argparse
import os
import sys

from pymongo import MongoClient

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INSERT_DATA_DIR = os.path.join(SCRIPT_DIR, "insert_data")
if INSERT_DATA_DIR not in sys.path:
    sys.path.insert(0, INSERT_DATA_DIR)

from mongo_persona_output_specs import load_persona_uids, resolve_mongo_uri  # noqa: E402

COLLECTION_NAME = "somni_physiological_data"
DEFAULT_DEVICE_ID = "somni_device_009"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="统计 somni_physiological_data 中指定 device_id 的去重 uid 数量。"
    )
    parser.add_argument(
        "--device-id",
        default=DEFAULT_DEVICE_ID,
        help=f"要查询的 device_id，默认 {DEFAULT_DEVICE_ID}",
    )
    parser.add_argument(
        "--all-users",
        action="store_true",
        help="统计全库所有 uid；默认仅统计八人格 uid",
    )
    return parser.parse_args()


def find_matching_uids(db, device_id: str, scope_uids: list[str] | None) -> list[str]:
    match_filter: dict = {"device_id": device_id}
    if scope_uids is not None:
        match_filter["uid"] = {"$in": scope_uids}

    pipeline = [
        {"$match": match_filter},
        {"$group": {"_id": "$uid"}},
        {"$sort": {"_id": 1}},
    ]
    return [doc["_id"] for doc in db[COLLECTION_NAME].aggregate(pipeline, allowDiskUse=True)]


def main() -> int:
    os.chdir(os.path.dirname(SCRIPT_DIR))
    args = parse_args()

    try:
        uri, db_name = resolve_mongo_uri()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    persona_uids = load_persona_uids()
    scope_uids = None if args.all_users else persona_uids
    scope_label = "全库" if args.all_users else f"八人格（共 {len(persona_uids)} 人）"

    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    try:
        client.admin.command("ping")
        db = client[db_name]
        matching_uids = find_matching_uids(db, args.device_id, scope_uids)
    except Exception as exc:
        print(f"MongoDB 查询失败: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()

    print(f"集合: {COLLECTION_NAME}")
    print(f"device_id: {args.device_id}")
    print(f"统计范围: {scope_label}")
    print(f"匹配用户数: {len(matching_uids)}")
    if matching_uids:
        print("匹配 uid 列表:")
        for uid in matching_uids:
            print(f"  - {uid}")
    else:
        print("匹配 uid 列表: （无）")

    if not args.all_users:
        missing = [uid for uid in persona_uids if uid not in matching_uids]
        print(f"八人格中未匹配: {len(missing)} 人")
        for uid in missing:
            print(f"  - {uid}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
