#!/usr/bin/env python3
"""将 somni_reports JSON 文件写入 MongoDB 的 somni_reports 集合。

- 插入前移除每条记录的 ``_id``（由 MongoDB 自动生成新 ObjectId，或 upsert 时保留已有 _id）
- ``create_time`` / ``update_time`` 转为 BSON Date（datetime）
- 按 ``uid`` + ``record_date`` + ``language`` upsert（与 insert_somni_records.py sleep_report 一致）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError

DEFAULT_MONGO_URI = "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive"
DEFAULT_COLLECTION = "somni_reports"
DEFAULT_FILE = "somni_reports_69aea6f3af5e6cbf0802796a.json"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))


def extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


def parse_dt(value: Any) -> Any:
    """将时间字符串转为 datetime（入库为 ISODate）。"""
    if value is None or value == "":
        return value
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间字段: {value!r}")


def normalize_record(record: dict) -> dict | None:
    """去掉 _id，转换时间字段，规范化 language。"""
    doc = dict(record)
    doc.pop("_id", None)

    for field in ("create_time", "update_time"):
        if field in doc and doc[field] is not None:
            doc[field] = parse_dt(doc[field])

    language = str(doc.get("language", "")).lower()
    if language not in ("zh", "en"):
        old_lang = str(doc.get("lang", "")).lower()
        if old_lang in ("zh", "en"):
            language = old_lang
        elif language.startswith("en"):
            language = "en"
        else:
            language = "zh"
    doc["language"] = language
    doc.pop("lang", None)

    uid = doc.get("uid")
    record_date = doc.get("record_date")
    if not uid or not record_date:
        return None
    return doc


def load_records(file_path: str) -> list[dict]:
    with open(file_path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    raise ValueError(f"JSON 根节点须为数组或对象: {file_path}")


def build_upsert_operations(records: list[dict]) -> list[UpdateOne]:
    ops: list[UpdateOne] = []
    for record in records:
        doc = normalize_record(record)
        if doc is None:
            continue
        ops.append(
            UpdateOne(
                {
                    "uid": doc["uid"],
                    "record_date": doc["record_date"],
                    "language": doc["language"],
                },
                {"$set": doc},
                upsert=True,
            )
        )
    return ops


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file",
        default=DEFAULT_FILE,
        help=f"somni_reports JSON 路径（默认仓库根目录下 {DEFAULT_FILE}）",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help=f"目标集合名（默认 {DEFAULT_COLLECTION}）",
    )
    parser.add_argument("--uri", default="", help="MongoDB URI（默认读环境变量）")
    parser.add_argument("--db", default="", help="数据库名（默认从 URI 推断）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅解析并打印统计，不写入数据库",
    )
    return parser.parse_args()


def resolve_file_path(file_arg: str) -> str:
    if os.path.isabs(file_arg):
        return file_arg
    candidates = [
        os.path.join(PROJECT_ROOT, file_arg),
        os.path.join(SCRIPT_DIR, file_arg),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[0]


def main() -> int:
    load_dotenv()
    args = parse_args()

    file_path = resolve_file_path(args.file)
    if not os.path.isfile(file_path):
        print(f"文件不存在: {file_path}", file=sys.stderr)
        return 1

    mongo_uri = (
        args.uri
        or os.getenv("MONGODB_URI")
        or os.getenv("MONGO_URI")
        or DEFAULT_MONGO_URI
    )
    db_name = args.db or os.getenv("MONGODB_DB") or extract_db_name_from_uri(mongo_uri)

    raw_records = load_records(file_path)
    operations = build_upsert_operations(raw_records)
    skipped = len(raw_records) - len(operations)

    print(f"读取文件: {file_path}")
    print(f"原始记录: {len(raw_records)}，可写入: {len(operations)}，跳过: {skipped}")

    if args.dry_run:
        if raw_records:
            sample = normalize_record(raw_records[0])
            if sample:
                print(
                    "dry-run 示例:",
                    f"uid={sample.get('uid')}",
                    f"record_date={sample.get('record_date')}",
                    f"create_time={sample.get('create_time')!r}",
                    f"含 _id={'_id' in sample}",
                )
        return 0

    if not operations:
        print("没有可写入的记录", file=sys.stderr)
        return 1

    client = MongoClient(mongo_uri)
    try:
        db = client[db_name]
        if args.collection not in db.list_collection_names():
            print(f"集合 {args.collection!r} 不存在，请先创建集合", file=sys.stderr)
            return 1

        collection = db[args.collection]
        result = collection.bulk_write(operations, ordered=False)
        print(
            f"写入 {args.collection}: "
            f"matched={result.matched_count}, "
            f"modified={result.modified_count}, "
            f"upserted={len(result.upserted_ids)}"
        )
    except BulkWriteError as exc:
        print(f"批量写入部分失败: {exc.details}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"写入失败: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
