"""
将 output/quiz_personalities_zh.json / output/quiz_personalities_en.json
插入到 quiz_personalities 集合。

默认行为：
- 去掉输入记录中的 _id（避免与库中已有 _id 冲突）
- create_time / update_time 若为 ISO 字符串则转为 datetime（入库为 ISODate）
"""
# 文件作用：用于 insert quiz personalities lang 相关的数据处理或流程支持。


from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from pymongo import MongoClient

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

load_dotenv()

MONGO_URI = os.getenv(
    "MONGODB_URI",
    "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive",
)
DB_NAME = "Fullive"
COLLECTION = "quiz_personalities"

ZH_FILE = "output/quiz_personalities_zh.json"
EN_FILE = "output/quiz_personalities_en.json"


def parse_isodate(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def process_record(record: dict[str, Any]) -> dict[str, Any]:
    r = dict(record)
    r.pop("_id", None)
    for field in ("create_time", "update_time"):
        v = r.get(field)
        if isinstance(v, str):
            try:
                r[field] = parse_isodate(v)
            except ValueError:
                pass
    return r


def load_records(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description="插入 quiz_personalities 多语言数据")
    parser.add_argument(
        "--source",
        choices=["zh", "en", "both"],
        default="both",
        help="要插入的数据来源：zh / en / both",
    )
    args = parser.parse_args()

    files: list[str] = []
    if args.source in ("zh", "both"):
        files.append(ZH_FILE)
    if args.source in ("en", "both"):
        files.append(EN_FILE)

    all_records: list[dict[str, Any]] = []
    for path in files:
        if not os.path.exists(path):
            raise FileNotFoundError(f"未找到输入文件: {path}")
        records = load_records(path)
        all_records.extend(process_record(r) for r in records)
        print(f"读取 {path}: {len(records)} 条")

    if not all_records:
        print("没有可插入的数据，退出")
        return

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
    db = client[DB_NAME]
    result = db[COLLECTION].insert_many(all_records)
    client.close()

    print(f"插入完成，集合: {COLLECTION}，成功 {len(result.inserted_ids)} 条")


if __name__ == "__main__":
    main()
