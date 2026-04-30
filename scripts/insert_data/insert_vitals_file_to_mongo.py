"""将指定 vitals_data.json 文件写入 somni_physiological_data 集合。"""

import argparse
import json
import os
from datetime import datetime
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import BulkWriteError


DEFAULT_MONGO_URI = "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive"
DEFAULT_COLLECTION = "somni_physiological_data"


def extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将 vitals JSON 文件插入 MongoDB")
    parser.add_argument(
        "--file",
        default="69aea6d8af5e6cbf08027966_vitals_data.json",
        help="要插入的 vitals JSON 文件路径",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help="目标集合名，默认 somni_physiological_data",
    )
    parser.add_argument("--uri", default="", help="MongoDB URI（为空时读取环境变量）")
    parser.add_argument("--db", default="", help="数据库名（为空时从 URI 推断）")
    parser.add_argument("--batch-size", type=int, default=1000, help="批量插入大小")
    return parser.parse_args()


def parse_dt(value):
    if not value:
        return value
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return value
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            # 兼容 "YYYY-MM-DD HH:MM:SS" 这类格式
            try:
                return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return value
    return value


def normalize_record(record: dict) -> dict:
    new_record = dict(record)
    new_record["collected_at"] = parse_dt(new_record.get("collected_at"))
    new_record["create_time"] = parse_dt(new_record.get("create_time"))
    new_record["update_time"] = parse_dt(new_record.get("update_time"))
    return new_record


def chunked(items, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def main():
    load_dotenv()
    args = parse_args()

    mongo_uri = args.uri or os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or DEFAULT_MONGO_URI
    db_name = args.db or os.getenv("MONGODB_DB") or extract_db_name_from_uri(mongo_uri, fallback="Fullive")

    file_path = args.file
    if not os.path.isabs(file_path):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(script_dir))
        file_path = os.path.join(project_root, file_path)

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        records = data
    else:
        records = [data]

    processed = [normalize_record(item) for item in records]
    if not processed:
        print("文件中没有可插入的数据。")
        return

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=8000)
    inserted_total = 0
    try:
        db = client[db_name]
        collection = db[args.collection]

        for batch in chunked(processed, max(1, args.batch_size)):
            try:
                result = collection.insert_many(batch, ordered=False)
                inserted_total += len(result.inserted_ids)
            except BulkWriteError as e:
                details = e.details or {}
                inserted = details.get("nInserted", 0)
                inserted_total += inserted
                write_errors = details.get("writeErrors", [])
                print(f"批次部分写入：已插入 {inserted} 条，错误 {len(write_errors)} 条")

        print(f"目标数据库: {db_name}")
        print(f"目标集合: {args.collection}")
        print(f"总记录数: {len(processed)}")
        print(f"成功插入: {inserted_total}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
