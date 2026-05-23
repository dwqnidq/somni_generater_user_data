"""原子替换插入脚本。

将 output/ 目录下的 JSON 数据插入 MongoDB，使用原子替换模式：
  1. 插入到带随机后缀的临时集合（如 somni_records_a3f8b2）
  2. drop 旧集合
  3. rename 临时集合为正式集合名

用法：
  python atomic_swap_insert.py all                # 处理所有数据类型
  python atomic_swap_insert.py health_data        # 只处理 health_data
  python atomic_swap_insert.py health_data vitals_data  # 处理多个类型
"""

import json
import os
import secrets
import sys
from datetime import datetime

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient
from urllib.parse import urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
os.chdir(PROJECT_ROOT)

load_dotenv()

DEFAULT_MONGO_URI = "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive"
MONGO_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or DEFAULT_MONGO_URI
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")

# 集合映射：数据类型 → 目标集合名 + 日期字段定义
COLLECTION_MAP = {
    "health_data": {
        "collection": "somni_records",
        "date_fields": {
            "create_time": True,
            "update_time": True,
            "timestamp": True,
            "raw_data.bed_time": True,
            "raw_data.sleep_time": True,
            "raw_data.wake_up_time": True,
            "raw_data.wake_time": True,
        },
    },
    "calendar_events": {
        "collection": "somni_schedules",
        "date_fields": {
            "create_time": True,
            "update_time": True,
        },
    },
    "vitals_data": {
        "collection": "somni_physiological_data",
        "date_fields": {
            "create_time": True,
            "update_time": True,
            "collected_at": True,
        },
    },
    "daily_emotion_steps": {
        "collection": "somni_fusion",
        "date_fields": {
            "create_time": True,
            "update_time": True,
        },
    },
    "sleep_art_data": {
        "collection": "somni_dream_universe_assets",
        "date_fields": {
            "create_time": True,
            "update_time": True,
        },
    },
    "sleep_district": {
        "collection": "somni_sleep_district",
        "date_fields": {
            "create_time": True,
            "update_time": True,
        },
    },
    "sleep_analysis": {
        "collection": "somni_sleep_analysis",
        "date_fields": {
            "create_time": True,
            "update_time": True,
        },
    },
    "sleep_events": {
        "collection": "somni_events",
        "date_fields": {
            "create_time": True,
            "update_time": True,
        },
    },
    "sleep_report": {
        "collection": "somni_reports",
        "date_fields": {
            "create_time": True,
            "update_time": True,
        },
    },
    "environment_data": {
        "collection": "somni_environment_data",
        "date_fields": {
            "create_time": True,
            "update_time": True,
            "collected_at": True,
        },
    },
    "ai_analysis_14d": {
        "collection": "somni_ai_insights",
        "date_fields": {
            "create_time": True,
            "update_time": True,
        },
    },
}


def _extract_db_name(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


DB_NAME = os.getenv("MONGODB_DB") or _extract_db_name(MONGO_URI, fallback="Fullive")


def _convert_dates(record: dict, date_fields: dict) -> dict:
    """将指定字段的 ISO 时间字符串转换为 datetime 对象。"""
    for field_path, is_date in date_fields.items():
        if not is_date:
            continue
        parts = field_path.split(".")
        current = record
        for part in parts[:-1]:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                current = None
                break
        if isinstance(current, dict) and parts[-1] in current:
            val = current[parts[-1]]
            if val and isinstance(val, str):
                try:
                    current[parts[-1]] = datetime.fromisoformat(val.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
    return record


def _convert_objectid(record: dict) -> dict:
    """将 _id 字段中的 ObjectId(xxx) 字符串转换为 ObjectId 对象。"""
    if "_id" in record:
        id_str = record["_id"]
        if isinstance(id_str, str) and id_str.startswith("ObjectId(") and id_str.endswith(")"):
            id_str = id_str[9:-1]
        record["_id"] = ObjectId(id_str)
    return record


def load_and_process(file_path: str, date_fields: dict) -> list[dict]:
    """读取 JSON 文件，处理日期和 ObjectId，返回记录列表。"""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    records = data if isinstance(data, list) else [data]
    return [_convert_dates(_convert_objectid(r), date_fields) for r in records]


def atomic_swap_insert(
    db,
    collection_name: str,
    records: list[dict],
    data_type: str | None = None,
) -> None:
    """原子替换：临时集合插入 → drop 旧集合 → rename 为正式名。

    流程：
      1. 向 {collection_name}_{random_hex} 插入数据
      2. drop 旧的 {collection_name}（如果存在）
      3. rename 临时集合 → {collection_name}
    """
    suffix = secrets.token_hex(3)  # 6 位随机字符
    tmp_name = f"{collection_name}_{suffix}"

    # 1. 插入临时集合
    tmp_col = db[tmp_name]
    tmp_col.insert_many(records)
    print(f"  已插入 {len(records)} 条 → 临时集合 {tmp_name}")

    # 2. drop 旧集合（如果存在）
    if collection_name in db.list_collection_names():
        db[collection_name].drop()
        print(f"  已删除旧集合 {collection_name}")

    # 3. rename 临时集合 → 正式集合名
    db[tmp_name].rename(collection_name)
    print(f"  已重命名 {tmp_name} → {collection_name}")


def process_data_type(data_type: str) -> None:
    """处理指定数据类型：读取所有对应 JSON 文件，原子替换插入 MongoDB。"""
    if data_type not in COLLECTION_MAP:
        print(f"未知的数据类型: {data_type}")
        print(f"可用类型: {list(COLLECTION_MAP.keys())}")
        return

    config = COLLECTION_MAP[data_type]
    collection_name = config["collection"]
    date_fields = config["date_fields"]

    # 查找 output/ 中匹配的文件
    data_files = sorted(
        f for f in os.listdir(OUTPUT_DIR) if f"_{data_type}.json" in f
    )
    if not data_files:
        print(f"[{data_type}] 未找到数据文件，跳过")
        return

    # 汇总所有文件的记录
    all_records: list[dict] = []
    for file_name in data_files:
        file_path = os.path.join(OUTPUT_DIR, file_name)
        print(f"  读取: {file_name}")
        records = load_and_process(file_path, date_fields)
        all_records.extend(records)
        print(f"    → {len(records)} 条")

    if not all_records:
        print(f"[{data_type}] 无有效记录，跳过")
        return

    print(f"[{data_type}] 共 {len(all_records)} 条，目标集合: {collection_name}")

    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    try:
        atomic_swap_insert(db, collection_name, all_records, data_type=data_type)
    except Exception as e:
        print(f"[{data_type}] 插入失败: {e}")
        raise
    finally:
        client.close()


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python atomic_swap_insert.py <数据类型|all> [数据类型2 ...]")
        print(f"可用类型: {list(COLLECTION_MAP.keys())}")
        print("  all  — 处理所有数据类型")
        return

    args = sys.argv[1:]
    if args[0] == "all":
        types = list(COLLECTION_MAP.keys())
    else:
        types = args

    print(f"数据库: {DB_NAME}")
    print(f"数据目录: {OUTPUT_DIR}")
    print(f"处理类型: {types}\n")

    for dt in types:
        print(f"{'=' * 50}")
        process_data_type(dt)
        print()

    print("全部完成。")


if __name__ == "__main__":
    main()
