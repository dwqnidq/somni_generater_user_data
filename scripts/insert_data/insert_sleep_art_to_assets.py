#!/usr/bin/env python3
"""
将 output/sleep_art_data 目录中的 sleep_art JSON 写入 MongoDB:
- 目标集合: somni_dream_universe_assets
- 写入策略: 按 uid 先删除旧数据，再批量插入新数据

用法（项目根目录）:
  python scripts/insert_data/insert_sleep_art_to_assets.py --dry-run
  python scripts/insert_data/insert_sleep_art_to_assets.py
  python scripts/insert_data/insert_sleep_art_to_assets.py --uid 69aea6d8af5e6cbf08027966
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
SOURCE_DIR = PROJECT_ROOT / "output" / "sleep_art_data"
TARGET_COLLECTION = "somni_dream_universe_assets"
SERVER_SELECTION_TIMEOUT_MS = 8000
DATE_FIELDS = ("create_time", "update_time")


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
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    return None


def _parse_uid_from_filename(path: Path) -> str:
    suffix = "_sleep_art.json"
    name = path.name
    if not name.endswith(suffix):
        raise ValueError(f"文件名不符合约定: {name}")
    uid = name[: -len(suffix)].strip()
    if not uid:
        raise ValueError(f"无法从文件名提取 uid: {name}")
    return uid


def _normalize_record(record: dict) -> dict:
    output = dict(record)
    for key in DATE_FIELDS:
        if key not in output:
            continue
        parsed = _to_mongo_date(output[key])
        if parsed is None and output[key] is not None:
            raise ValueError(f"字段 {key} 不是有效日期: {output[key]!r}")
        if parsed is not None:
            output[key] = parsed
    return output


def load_uid_records(path: Path) -> tuple[str, list[dict]]:
    uid = _parse_uid_from_filename(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"文件内容必须是数组: {path}")

    normalized: list[dict] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"第 {idx + 1} 条不是对象: {path}")
        rec_uid = str(item.get("uid", "")).strip()
        if rec_uid and rec_uid != uid:
            raise ValueError(f"文件 uid 与记录 uid 不一致: {path} ({uid} != {rec_uid})")
        item["uid"] = uid
        normalized.append(_normalize_record(item))
    return uid, normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将 sleep_art_data 导入 somni_dream_universe_assets（先删后插）")
    parser.add_argument("--dir", type=Path, default=SOURCE_DIR, help="源目录（默认 output/sleep_art_data）")
    parser.add_argument("--uid", default="", help="仅处理指定 uid")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写入数据库")
    return parser.parse_args()


def resolve_env() -> tuple[str, str]:
    load_dotenv(PROJECT_ROOT / ".env")
    uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
    if not uri:
        raise RuntimeError("请在 .env 中配置 MONGODB_URI（或 MONGO_URI）")
    db_name = os.getenv("MONGODB_DB") or _extract_db_name_from_uri(uri)
    return uri, db_name


def collect_source_files(source_dir: Path, uid_filter: str) -> list[Path]:
    if not source_dir.is_dir():
        raise FileNotFoundError(f"目录不存在: {source_dir}")
    paths = sorted(source_dir.glob("*_sleep_art.json"))
    if uid_filter:
        expected = source_dir / f"{uid_filter}_sleep_art.json"
        if not expected.is_file():
            raise FileNotFoundError(f"指定 uid 文件不存在: {expected}")
        return [expected]
    return paths


def main() -> None:
    os.chdir(PROJECT_ROOT)
    args = parse_args()

    source_dir = args.dir if args.dir.is_absolute() else PROJECT_ROOT / args.dir
    uid_filter = args.uid.strip()
    source_files = collect_source_files(source_dir, uid_filter)
    if not source_files:
        print(f"未找到待处理文件: {source_dir}")
        return

    uid_to_records: dict[str, list[dict]] = {}
    total_records = 0
    for path in source_files:
        uid, records = load_uid_records(path)
        uid_to_records[uid] = records
        total_records += len(records)
        print(f"读取文件: {path.name}  uid={uid}  记录数={len(records)}")

    print(f"总用户数={len(uid_to_records)}  总记录数={total_records}")
    if args.dry_run:
        print("[dry-run] 未执行数据库写入")
        return

    uri, db_name = resolve_env()
    client = MongoClient(uri, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS)
    try:
        db = client[db_name]
        collection = db[TARGET_COLLECTION]
        print(f"目标数据库: {db_name}  集合: {TARGET_COLLECTION}")

        deleted_total = 0
        inserted_total = 0
        for uid, records in uid_to_records.items():
            delete_result = collection.delete_many({"uid": uid})
            deleted_total += int(delete_result.deleted_count)

            if records:
                insert_result = collection.insert_many(records, ordered=False)
                inserted_count = len(insert_result.inserted_ids)
            else:
                inserted_count = 0
            inserted_total += inserted_count
            print(
                f"uid={uid} 删除={delete_result.deleted_count} 插入={inserted_count} 源记录={len(records)}"
            )

        print(f"完成: 删除总数={deleted_total} 插入总数={inserted_total}")
    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"执行失败: {exc}", file=sys.stderr)
        sys.exit(1)
