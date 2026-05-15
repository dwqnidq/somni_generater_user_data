"""
查询 quiz_personalities 中 mhr_codes 长度为 4 的文档，递归检查所有 URL 类字段是否为空。

空定义：值为 None 或空字符串 ""。非空不输出。

依赖：环境变量 MONGODB_URI（或 MONGO_URI），可选 MONGODB_DB。
"""
# 文件作用：只读检查 quiz_personalities 四位人格文档中的空 URL 字段。

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Iterator
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

load_dotenv()

DEFAULT_MONGO_URI = "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive"
MONGO_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or DEFAULT_MONGO_URI
COLLECTION = "quiz_personalities"


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


def _is_url_field_key(key: str) -> bool:
    return key.lower() == "url" or key.endswith("_url")


def _url_value_is_empty(value: Any) -> bool:
    return value is None or value == ""


def iter_empty_url_paths(obj: Any, path_prefix: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(obj, dict):
        for key, val in obj.items():
            seg = f"{path_prefix}.{key}" if path_prefix else key
            if _is_url_field_key(key):
                if _url_value_is_empty(val):
                    yield seg, val
                elif isinstance(val, (dict, list)):
                    yield from iter_empty_url_paths(val, seg)
            elif isinstance(val, (dict, list)):
                yield from iter_empty_url_paths(val, seg)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            seg = f"{path_prefix}[{i}]"
            if isinstance(item, (dict, list)):
                yield from iter_empty_url_paths(item, seg)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="查询 quiz_personalities 中四位 mhr_codes 文档，列出值为空的 URL 字段路径。"
    )
    parser.add_argument(
        "--language",
        choices=("zh", "en"),
        default=None,
        help="若集合含 language 字段，可只查 zh 或 en；不传则不过滤语言。",
    )
    args = parser.parse_args()

    query: dict[str, Any] = {"$expr": {"$eq": [{"$size": "$mhr_codes"}, 4]}}
    if args.language:
        query["language"] = args.language

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
    coll = client[DB_NAME][COLLECTION]

    total = 0
    docs_with_empty = 0
    empty_field_count = 0

    cursor = coll.find(query, projection=None)
    for doc in cursor:
        total += 1
        paths = list(iter_empty_url_paths(doc))
        if not paths:
            continue
        docs_with_empty += 1
        empty_field_count += len(paths)
        codes = doc.get("mhr_codes")
        oid = doc.get("_id")
        name = doc.get("mhr_name") or doc.get("title") or ""
        print("—" * 60)
        print(f"文档 _id: {oid}")
        print(f"mhr_codes: {codes}")
        if name:
            print(f"名称: {name}")
        for p, val in paths:
            disp = "（空字符串）" if val == "" else "（None）"
            print(f"  空 URL 字段: {p}  {disp}")

    print("—" * 60)
    print(f"共扫描文档数: {total}")
    print(f"存在空 URL 的文档数: {docs_with_empty}")
    print(f"空 URL 字段总次数: {empty_field_count}")
    if docs_with_empty == 0 and total > 0:
        print("未发现空 URL 字段。")
    elif total == 0:
        print("没有符合条件的文档（请检查库名、集合名或查询条件）。")


if __name__ == "__main__":
    main()
