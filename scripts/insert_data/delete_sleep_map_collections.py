#!/usr/bin/env python3
"""清空 MongoDB 中睡眠地图相关集合的全部文档。

目标集合：
  - somni_sleep_analysis
  - somni_sleep_district

连接：项目根 .env 中 MONGODB_URI（或 MONGO_URI）；不设代码内默认密钥。

用法（在项目根目录）:
  python scripts/insert_data/delete_sleep_map_collections.py --dry-run
  python scripts/insert_data/delete_sleep_map_collections.py --apply
  python scripts/insert_data/delete_sleep_map_collections.py --apply --only analysis
"""

from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
os.chdir(PROJECT_ROOT)
load_dotenv()

COLLECTION_ANALYSIS = "somni_sleep_analysis"
COLLECTION_DISTRICT = "somni_sleep_district"
# 白名单：脚本只会 touch 下列集合，不会删除库内任何其它集合
ALLOWED_COLLECTIONS = frozenset({COLLECTION_ANALYSIS, COLLECTION_DISTRICT})
TARGET_COLLECTIONS = (COLLECTION_ANALYSIS, COLLECTION_DISTRICT)
SERVER_SELECTION_TIMEOUT_MS = 8000


def _extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


def _resolve_collections(only: str | None) -> tuple[str, ...]:
    if only is None:
        return TARGET_COLLECTIONS
    if only == "analysis":
        return (COLLECTION_ANALYSIS,)
    if only == "district":
        return (COLLECTION_DISTRICT,)
    raise ValueError(f"未知 --only 值: {only}")


def _assert_whitelisted(collection_names: tuple[str, ...]) -> None:
    unknown = [n for n in collection_names if n not in ALLOWED_COLLECTIONS]
    if unknown:
        raise RuntimeError(f"拒绝操作非白名单集合: {unknown}")


def _count_documents(db, collection_names: tuple[str, ...]) -> dict[str, int]:
    _assert_whitelisted(collection_names)
    counts: dict[str, int] = {}
    for name in collection_names:
        counts[name] = db[name].count_documents({})
    return counts


def _delete_all_documents(db, collection_names: tuple[str, ...]) -> dict[str, int]:
    _assert_whitelisted(collection_names)
    deleted: dict[str, int] = {}
    for name in collection_names:
        result = db[name].delete_many({})
        deleted[name] = result.deleted_count
        remaining = db[name].count_documents({})
        if remaining != 0:
            raise RuntimeError(
                f"集合 {name} 删除后仍有 {remaining} 条文档（已删 {deleted[name]} 条）"
            )
    return deleted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="删除 somni_sleep_analysis / somni_sleep_district 集合中的全部文档",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="仅统计各集合文档数，不执行删除",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="执行 delete_many({}) 清空目标集合（不可恢复）",
    )
    parser.add_argument(
        "--only",
        choices=("analysis", "district"),
        default=None,
        help="仅处理指定集合：analysis → somni_sleep_analysis，district → somni_sleep_district",
    )
    parser.add_argument("--uri", default="", help="MongoDB URI（为空时读取环境变量）")
    parser.add_argument("--db", default="", help="数据库名（为空时从 URI 或 MONGODB_DB 解析）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mongo_uri = args.uri or os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
    if not mongo_uri:
        print("请在 .env 中配置 MONGODB_URI（或 MONGO_URI），或使用 --uri", file=sys.stderr)
        return 1

    db_name = (
        args.db
        or os.getenv("MONGODB_DB")
        or _extract_db_name_from_uri(mongo_uri)
    )
    collection_names = _resolve_collections(args.only)

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS)
    try:
        client.admin.command("ping")
        db = client[db_name]
        counts = _count_documents(db, collection_names)

        print(f"数据库: {db_name}")
        print("仅操作以下集合（其它集合不受影响）:")
        for name in collection_names:
            print(f"  - {name}")
        print()
        for name in collection_names:
            print(f"  {name}: {counts[name]:,} 条")

        if args.dry_run:
            print("\n[dry-run] 未删除任何文档。确认后请加 --apply 执行。")
            return 0

        if sum(counts.values()) == 0:
            print("\n目标集合已为空，无需删除。")
            return 0

        deleted = _delete_all_documents(db, collection_names)
        print("\n已删除:")
        for name in collection_names:
            print(f"  {name}: {deleted[name]:,} 条")
        print("删除完成，各集合文档数均为 0。")
        return 0
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
