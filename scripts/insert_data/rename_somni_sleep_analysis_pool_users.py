#!/usr/bin/env python3
"""将 somni_sleep_analysis 中非八人格用户的 user_name 改为全市统一的「用户一、用户二…」。

规则：
  - 查询 uid 不在八人格列表中的文档（不修改 uid 及其它字段）
  - 按 MongoDB 游标返回顺序，对每个 uid 首次出现时分配序号（全市统一）
  - 八人格 user_name 保持不变（查询时已排除，写库时不触及）

用法（项目根目录）:
  python scripts/insert_data/rename_somni_sleep_analysis_pool_users.py --dry-run
  python scripts/insert_data/rename_somni_sleep_analysis_pool_users.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient, UpdateMany

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from utils import format_sleep_map_pool_user_name  # noqa: E402

load_dotenv()

COLLECTION = "somni_sleep_analysis"

# 与 scripts/generate_data/adjust_ranking_for_personas.py 一致
PERSONA_UIDS = frozenset({
    "69aea593af5e6cbf08027964",
    "69aea63eaf5e6cbf08027965",
    "69aea6d8af5e6cbf08027966",
    "69aea6e3af5e6cbf08027967",
    "69aea6e8af5e6cbf08027968",
    "69aea6eeaf5e6cbf08027969",
    "69aea6f3af5e6cbf0802796a",
    "69aea6f8af5e6cbf0802796b",
})

DEFAULT_MONGO_URI = "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive"
MONGO_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or DEFAULT_MONGO_URI


def _extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


DB_NAME = os.getenv("MONGODB_DB") or _extract_db_name_from_uri(MONGO_URI, fallback="Fullive")


def _uid_order_from_cursor(col) -> list[str]:
    """按 find 游标顺序，对每个 uid 取首次出现次序（全市统一）。"""
    seen: set[str] = set()
    order: list[str] = []
    query = {"uid": {"$nin": list(PERSONA_UIDS)}}
    for doc in col.find(query, {"uid": 1}):
        uid = doc.get("uid")
        if not uid or uid in PERSONA_UIDS:
            continue
        if uid not in seen:
            seen.add(uid)
            order.append(str(uid))
    return order


def build_uid_to_name(col) -> dict[str, str]:
    order = _uid_order_from_cursor(col)
    return {
        uid: format_sleep_map_pool_user_name(idx)
        for idx, uid in enumerate(order, start=1)
    }


def _print_mapping(uid_to_name: dict[str, str], col, limit: int = 30) -> None:
    print(f"非人格 uid 数量: {len(uid_to_name)}")
    for i, (uid, name) in enumerate(uid_to_name.items()):
        if i >= limit:
            print(f"  … 另有 {len(uid_to_name) - limit} 个 uid 未列出")
            break
        sample = col.find_one({"uid": uid}, {"user_name": 1})
        old = (sample or {}).get("user_name", "")
        print(f"  {i + 1:4d}. {uid}  {old!r} → {name!r}")

    doc_count = col.count_documents({"uid": {"$nin": list(PERSONA_UIDS)}})
    persona_count = col.count_documents({"uid": {"$in": list(PERSONA_UIDS)}})
    print(f"将影响的文档条数（非人格）: {doc_count}")
    print(f"不修改的文档条数（八人格）: {persona_count}")


def apply_updates(col, uid_to_name: dict[str, str], dry_run: bool) -> None:
    operations = []
    for uid, name in uid_to_name.items():
        operations.append(
            UpdateMany({"uid": uid}, {"$set": {"user_name": name}})
        )

    if not operations:
        print("没有需要更新的非人格用户")
        return

    if dry_run:
        print(f"[dry-run] 将执行 {len(operations)} 次 UpdateMany（仅 $set user_name）")
        return

    result = col.bulk_write(operations, ordered=False)
    print(
        f"已写库 {COLLECTION}: matched={result.matched_count}, "
        f"modified={result.modified_count}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="只预览映射，不写库")
    mode.add_argument("--apply", action="store_true", help="执行 bulk_write 更新")
    parser.add_argument("--show", type=int, default=30, help="dry-run 时最多打印几条 uid 映射")
    args = parser.parse_args()

    client = MongoClient(MONGO_URI)
    try:
        col = client[DB_NAME][COLLECTION]
        if COLLECTION not in client[DB_NAME].list_collection_names():
            print(f"集合 {COLLECTION} 不存在，退出")
            sys.exit(1)

        uid_to_name = build_uid_to_name(col)
        _print_mapping(uid_to_name, col, limit=args.show)
        apply_updates(col, uid_to_name, dry_run=args.dry_run)
        if args.dry_run:
            print("[dry-run] 未写库；确认后请加 --apply")
    finally:
        client.close()


if __name__ == "__main__":
    main()
