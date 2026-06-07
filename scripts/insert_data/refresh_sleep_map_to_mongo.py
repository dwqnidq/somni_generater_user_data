#!/usr/bin/env python3
"""
清空并重新导入睡眠地图 Mongo 集合。

步骤：
  1. 整表删除 somni_sleep_analysis、somni_sleep_district 全部文档
  2. 插入 output/ 下数据（插入前去掉每条记录的 _id，由 MongoDB 生成新 _id）：
     - output/*_somni_sleep_analysis.json（八人格等 per-uid）
     - output/somni_sleep_analysis.json
     - output/somni_sleep_district.json

连接：项目根 .env 中 MONGODB_URI（或 MONGO_URI）。

用法（在项目根目录）:
  python scripts/insert_data/refresh_sleep_map_to_mongo.py --dry-run
  python scripts/insert_data/refresh_sleep_map_to_mongo.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from delete_sleep_map_collections import (  # noqa: E402
    COLLECTION_ANALYSIS,
    COLLECTION_DISTRICT,
    TARGET_COLLECTIONS,
    _count_documents,
    _delete_all_documents,
    _extract_db_name_from_uri,
)
from insert_sleep_map_to_mongo import (  # noqa: E402
    DEFAULT_ANALYSIS_JSON,
    DEFAULT_DISTRICT_JSON,
    insert_batches,
    normalize_record,
)

SERVER_SELECTION_TIMEOUT_MS = 8000
PER_UID_GLOB = "*_somni_sleep_analysis.json"
DEFAULT_BATCH_SIZE = 1000


def _resolve_output_dir(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _load_json_records(path: Path, *, strip_id: bool) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else [raw]
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if strip_id:
            row.pop("_id", None)
        out.append(normalize_record(row))
    return out


def _discover_per_uid_analysis_files(output_dir: Path) -> list[Path]:
    files = sorted(output_dir.glob(PER_UID_GLOB))
    return [p for p in files if p.is_file()]


def _collect_insert_plan(output_dir: Path) -> list[tuple[str, Path, str]]:
    """返回 (label, path, collection_name) 列表，顺序即插入顺序。"""
    plan: list[tuple[str, Path, str]] = []
    for path in _discover_per_uid_analysis_files(output_dir):
        plan.append((f"per-uid:{path.name}", path, COLLECTION_ANALYSIS))
    aggregated = output_dir / DEFAULT_ANALYSIS_JSON.name
    if aggregated.is_file():
        plan.append(("aggregated-analysis", aggregated, COLLECTION_ANALYSIS))
    district = output_dir / DEFAULT_DISTRICT_JSON.name
    if district.is_file():
        plan.append(("district", district, COLLECTION_DISTRICT))
    return plan


def _insert_records(
    db,
    *,
    label: str,
    path: Path,
    collection_name: str,
    batch_size: int,
) -> int:
    records = _load_json_records(path, strip_id=True)
    print(f"[insert] {label}: {path.name} → {collection_name}，{len(records)} 条")
    if not records:
        return 0
    if collection_name not in db.list_collection_names():
        raise RuntimeError(f"集合不存在: {collection_name}")
    return insert_batches(db[collection_name], records, batch_size)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="仅统计，不写库")
    mode.add_argument("--apply", action="store_true", help="执行删除并插入")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "output",
        help="含 somni_sleep_* JSON 的目录（默认 output/）",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--uri", default="")
    parser.add_argument("--db", default="")
    return parser.parse_args()


def main() -> int:
    os.chdir(PROJECT_ROOT)
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()
    output_dir = _resolve_output_dir(args.output_dir)

    mongo_uri = args.uri or os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
    if not mongo_uri:
        print("请在 .env 中配置 MONGODB_URI（或 MONGO_URI），或使用 --uri", file=sys.stderr)
        return 1

    db_name = args.db or os.getenv("MONGODB_DB") or _extract_db_name_from_uri(mongo_uri)
    plan = _collect_insert_plan(output_dir)
    if not plan:
        print(f"未在 {output_dir} 找到可导入的 somni_sleep JSON 文件", file=sys.stderr)
        return 1

    total_insert = 0
    for label, path, _coll in plan:
        n = len(_load_json_records(path, strip_id=True))
        total_insert += n
        print(f"  [{label}] {path.name}: {n} 条（插入时将去掉 _id）")

    print(f"\n计划插入合计: {total_insert} 条")

    if args.dry_run:
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS)
        try:
            client.admin.command("ping")
            counts = _count_documents(client[db_name], TARGET_COLLECTIONS)
            print(f"\n[dry-run] 数据库: {db_name}")
            for name in TARGET_COLLECTIONS:
                print(f"  当前 {name}: {counts[name]:,} 条")
            print("[dry-run] 未删除、未插入。确认后请加 --apply。")
        finally:
            client.close()
        return 0

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS)
    try:
        client.admin.command("ping")
        db = client[db_name]
        print(f"目标数据库: {db_name}")

        counts = _count_documents(db, TARGET_COLLECTIONS)
        print("删除前文档数:")
        for name in TARGET_COLLECTIONS:
            print(f"  {name}: {counts[name]:,} 条")

        if sum(counts.values()) > 0:
            deleted = _delete_all_documents(db, TARGET_COLLECTIONS)
            print("已删除:")
            for name in TARGET_COLLECTIONS:
                print(f"  {name}: {deleted[name]:,} 条")
        else:
            print("目标集合已为空，跳过删除。")

        inserted_total = 0
        for label, path, collection_name in plan:
            n = _insert_records(
                db,
                label=label,
                path=path,
                collection_name=collection_name,
                batch_size=args.batch_size,
            )
            inserted_total += n

        print(f"\n完成。共插入 {inserted_total} 条。")
        for name in TARGET_COLLECTIONS:
            print(f"  {name}: {db[name].count_documents({}):,} 条")
        return 0
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
