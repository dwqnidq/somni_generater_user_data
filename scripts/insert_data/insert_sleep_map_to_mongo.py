#!/usr/bin/env python3
"""
将睡眠地图聚合 JSON 写入 MongoDB。

- output/somni_sleep_analysis.json  → somni_sleep_analysis
- output/somni_sleep_district.json  → somni_sleep_district

连接：项目根 .env 中 MONGODB_URI（或 MONGO_URI）；不设代码内默认密钥。
插入前将 _id 转为 ObjectId，create_time / update_time 转为 BSON Date。

用法（在项目根目录）:
  python scripts/insert_data/insert_sleep_map_to_mongo.py --dry-run
  python scripts/insert_data/insert_sleep_map_to_mongo.py
  python scripts/insert_data/insert_sleep_map_to_mongo.py --only analysis
  python scripts/insert_data/insert_sleep_map_to_mongo.py --only district --batch-size 500
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import BulkWriteError

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent

DEFAULT_ANALYSIS_JSON = PROJECT_ROOT / "output" / "somni_sleep_analysis.json"
DEFAULT_DISTRICT_JSON = PROJECT_ROOT / "output" / "somni_sleep_district.json"
COLLECTION_ANALYSIS = "somni_sleep_analysis"
COLLECTION_DISTRICT = "somni_sleep_district"
DATE_FIELDS = ("create_time", "update_time")
DEFAULT_BATCH_SIZE = 1000


def _extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


def _to_mongo_date(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, str) and value.strip():
        s = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    return None


def _normalize_object_id(raw_id: object) -> ObjectId:
    if isinstance(raw_id, ObjectId):
        return raw_id
    id_str = str(raw_id)
    if id_str.startswith("ObjectId(") and id_str.endswith(")"):
        id_str = id_str[9:-1]
    return ObjectId(id_str)


def normalize_record(record: dict) -> dict:
    out = dict(record)
    if "_id" in out:
        out["_id"] = _normalize_object_id(out["_id"])
    for key in DATE_FIELDS:
        if key not in out:
            continue
        parsed = _to_mongo_date(out[key])
        if parsed is None and out[key] is not None:
            raise ValueError(f"无法将 {key!r} 转为日期: {out[key]!r}")
        if parsed is not None:
            out[key] = parsed
    return out


def load_records(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else [raw]
    return [normalize_record(item) for item in items]


def chunked(items: list[dict], size: int):
    step = max(1, size)
    for i in range(0, len(items), step):
        yield items[i : i + step]


def insert_batches(collection, records: list[dict], batch_size: int) -> int:
    inserted_total = 0
    for batch in chunked(records, batch_size):
        try:
            result = collection.insert_many(batch, ordered=False)
            inserted_total += len(result.inserted_ids)
        except BulkWriteError as exc:
            details = exc.details or {}
            inserted_total += int(details.get("nInserted", 0))
            write_errors = details.get("writeErrors", [])
            print(
                f"  批次部分写入：已插入 {details.get('nInserted', 0)} 条，"
                f"错误 {len(write_errors)} 条（常见为 _id 重复）",
                file=sys.stderr,
            )
            if write_errors:
                first = write_errors[0]
                print(f"  首条错误: {first.get('errmsg', first)}", file=sys.stderr)
    return inserted_total


def run_insert(
    *,
    label: str,
    path: Path,
    collection_name: str,
    db,
    batch_size: int,
    dry_run: bool,
) -> None:
    if not path.is_file():
        print(f"[{label}] 文件不存在: {path}", file=sys.stderr)
        sys.exit(1)

    records = load_records(path)
    print(f"[{label}] 文件: {path.name}，记录数: {len(records)}，集合: {collection_name}")

    if dry_run:
        return

    if collection_name not in db.list_collection_names():
        print(f"[{label}] 集合不存在，跳过: {collection_name}", file=sys.stderr)
        sys.exit(2)

    inserted = insert_batches(db[collection_name], records, batch_size)
    print(f"[{label}] 成功插入: {inserted} / {len(records)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将 somni_sleep_analysis / somni_sleep_district 聚合 JSON 插入 MongoDB",
    )
    parser.add_argument(
        "--only",
        choices=("analysis", "district", "both"),
        default="both",
        help="仅插入个人分析、区级聚合或两者（默认 both）",
    )
    parser.add_argument(
        "--analysis-file",
        type=Path,
        default=DEFAULT_ANALYSIS_JSON,
        help="somni_sleep_analysis.json 路径",
    )
    parser.add_argument(
        "--district-file",
        type=Path,
        default=DEFAULT_DISTRICT_JSON,
        help="somni_sleep_district.json 路径",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="批量插入大小")
    parser.add_argument("--dry-run", action="store_true", help="只解析并统计，不写库")
    return parser.parse_args()


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def main() -> None:
    os.chdir(PROJECT_ROOT)
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()

    uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
    if not uri:
        print("请在 .env 中配置 MONGODB_URI（或 MONGO_URI）", file=sys.stderr)
        sys.exit(1)

    db_name = os.getenv("MONGODB_DB") or _extract_db_name_from_uri(uri)
    analysis_path = resolve_path(args.analysis_file)
    district_path = resolve_path(args.district_file)

    if args.dry_run:
        print(f"[dry-run] 目标库: {db_name}")
        if args.only in ("analysis", "both"):
            run_insert(
                label="analysis",
                path=analysis_path,
                collection_name=COLLECTION_ANALYSIS,
                db=None,
                batch_size=args.batch_size,
                dry_run=True,
            )
        if args.only in ("district", "both"):
            run_insert(
                label="district",
                path=district_path,
                collection_name=COLLECTION_DISTRICT,
                db=None,
                batch_size=args.batch_size,
                dry_run=True,
            )
        return

    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    try:
        db = client[db_name]
        print(f"目标数据库: {db_name}")
        if args.only in ("analysis", "both"):
            run_insert(
                label="analysis",
                path=analysis_path,
                collection_name=COLLECTION_ANALYSIS,
                db=db,
                batch_size=args.batch_size,
                dry_run=False,
            )
        if args.only in ("district", "both"):
            run_insert(
                label="district",
                path=district_path,
                collection_name=COLLECTION_DISTRICT,
                db=db,
                batch_size=args.batch_size,
                dry_run=False,
            )
    finally:
        client.close()


if __name__ == "__main__":
    main()
