"""文件作用：用于 insert somni records 相关的数据处理或流程支持。"""

import json
import os
import sys
from pymongo import MongoClient, UpdateOne
from bson import ObjectId
from dotenv import load_dotenv
from datetime import datetime
from urllib.parse import urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
os.chdir(PROJECT_ROOT)

"""
命令
python insert_somni_records.py health_data
python insert_somni_records.py vitals_data
python insert_somni_records.py quiz_result
python insert_somni_records.py quiz_result_record
python insert_somni_records.py survey_data
python insert_somni_records.py schedule_data
python insert_somni_records.py sleep_events
python insert_somni_records.py sleep_report
python insert_somni_records.py environment_data
"""
# 加载.env文件
load_dotenv()

# 数据库配置
DEFAULT_MONGO_URI = "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive"
MONGO_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or DEFAULT_MONGO_URI


def _extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    """从 MongoDB URI 中解析数据库名，解析失败时回退到 fallback。"""
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            # 去掉 query 之前部分，防止出现 /db?xxx 情况
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


DB_NAME = os.getenv("MONGODB_DB") or _extract_db_name_from_uri(MONGO_URI, fallback="Fullive")

# 数据类型配置
aaa = {
    "health_data": {
        "collection": "somni_records",
        "isDate": {
            "create_time": True,
            "update_time": True,
            "timestamp": True,
            "raw_data.bed_time": True,
            "raw_data.sleep_time": True,
            "raw_data.wake_up_time": True,
            "raw_data.wake_time": True,
        }
    },
    "calendar_events": {
        "collection": "somni_schedules",
        "isDate": {
            "create_time": True,
            "update_time": True,
        }
    },
    "intervention_schemes_interv": {
        "collection": "somni_temp_plans",
        "isDate": {
            "create_time": True,
            "update_time": True,
        }
    },
    "vitals_data": {
        "collection": "somni_physiological_data",
        "isDate": {
            "create_time": True,
            "update_time": True,
            "collected_at": True,
        }
    },
    "daily_emotion_steps": {
        "collection": "somni_fusion",
        "isDate": {
            "create_time": True,
            "update_time": True,
        }
    },
    "sleep_art_data": {
        "collection": "somni_dream_universe_assets",
        "isDate": {
            "create_time": True,
            "update_time": True,
        }
    },
    "sleep_district": {
        "collection": "somni_sleep_district",
        "isDate": {
            "create_time": True,
            "update_time": True,
        }
    },
    "sleep_analysis": {
        "collection": "somni_sleep_analysis",
        "isDate": {
            "create_time": True,
            "update_time": True,
        }
    }, 
    "sleep_events": {
        "collection": "somni_events",
        "isDate": {
            "create_time": True,
            "update_time": True,
        }
    },
    "sleep_report": {
        "collection": "somni_reports",
        "isDate": {
            "create_time": True,
            "update_time": True,
        }
    },
    "environment_data": {
        "collection": "somni_environment_data",
        "isDate": {
            "create_time": True,
            "update_time": True,
            "collected_at": True,
        }
    },
    "ai_analysis_14d": {
        "collection": "somni_ai_insights",
        "isDate": {
            "create_time": True,
            "update_time": True,
        }
    }
    # "personality_user_mapping": {
    #     "collection": "somni_personality_user_mapping",
    #     "isDate": {
    #         "create_time": True,
    #         "update_time": True,
    #     }
    # },
}

# 数据文件路径
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")


def process_data(file_path, date_fields):
    """处理数据文件，将指定的时间字符串转换为datetime对象"""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 确定数据是单个对象还是数组
    if isinstance(data, list):
        records = data
    else:
        records = [data]
    
    processed_records = []
    for record in records:
        # 处理_id字段
        if '_id' in record:
            # 处理ObjectId(xxx)格式的字符串
            id_str = record['_id']
            if isinstance(id_str, str) and id_str.startswith('ObjectId(') and id_str.endswith(')'):
                id_str = id_str[9:-1]  # 提取括号内的ObjectId字符串
            record['_id'] = ObjectId(id_str)
        
        # 处理时间字段
        for field_path, is_date in date_fields.items():
            if is_date:
                # 处理嵌套字段路径，如 "raw_data.bed_time"
                field_parts = field_path.split('.')
                current = record
                field_name = field_parts[-1]
                
                # 遍历到嵌套字段的父级
                for part in field_parts[:-1]:
                    if part in current:
                        current = current[part]
                    else:
                        break
                else:
                    # 如果所有父级字段都存在，且目标字段存在，则转换时间
                    if field_name in current:
                        time_str = current[field_name]
                        if time_str:
                            current[field_name] = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
        
        processed_records.append(record)
    
    return processed_records


def insert_to_mongodb(records, collection_name, data_type=None):
    """将处理后的记录插入到MongoDB指定集合"""
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    
    # 检查集合是否存在
    if collection_name not in db.list_collection_names():
        print(f"集合 {collection_name} 不存在，跳过插入")
        client.close()
        return
    
    collection = db[collection_name]
    
    try:
        if data_type == "sleep_report":
            operations = []
            for record in records:
                uid = record.get("uid")
                record_date = record.get("record_date")
                language = record.get("language")
                if not uid or not record_date or language not in ("zh", "en"):
                    continue
                operations.append(
                    UpdateOne(
                        {"uid": uid, "record_date": record_date, "language": language},
                        {"$set": record},
                        upsert=True,
                    )
                )

            if not operations:
                print(f"{collection_name} 没有可写入的 sleep_report 记录")
            else:
                result = collection.bulk_write(operations, ordered=False)
                print(
                    f"成功写入 {collection_name}："
                    f"matched={result.matched_count}, modified={result.modified_count}, upserted={len(result.upserted_ids)}"
                )
        else:
            result = collection.insert_many(records)
            print(f"成功插入 {len(result.inserted_ids)} 条记录到 {collection_name} 集合")
    except Exception as e:
        print(f"插入数据时出错: {e}")
    finally:
        client.close()


def process_data_type(data_type):
    """处理指定类型的数据"""
    if data_type not in aaa:
        print(f"未知的数据类型: {data_type}")
        return
    
    # 获取配置
    config = aaa[data_type]
    collection_name = config["collection"]
    date_fields = config["isDate"]
    
    # 查找对应的数据文件
    data_files = [f for f in os.listdir(OUTPUT_DIR) if f"_{data_type}.json" in f]
    
    if not data_files:
        print(f"未找到{data_type}数据文件")
        return
    
    for file_name in data_files:
        file_path = os.path.join(OUTPUT_DIR, file_name)
        print(f"处理文件: {file_name}")
        
        # 处理数据
        processed_records = process_data(file_path, date_fields)
        if data_type == "sleep_report":
            for record in processed_records:
                language = str(record.get("language", "")).lower()
                if language not in ("zh", "en"):
                    old_lang = str(record.get("lang", "")).lower()
                    if old_lang in ("zh", "en"):
                        record["language"] = old_lang
                    elif language.startswith("en"):
                        record["language"] = "en"
                    else:
                        record["language"] = "zh"
                else:
                    record["language"] = language

                # 兼容历史数据结构，移除 lang，统一使用 language
                if "lang" in record:
                    del record["lang"]
        print(f"处理了 {len(processed_records)} 条记录")
        
        # 插入到数据库
        insert_to_mongodb(processed_records, collection_name, data_type=data_type)


def main():
    """主函数"""
    # 检查命令行参数
    if len(sys.argv) < 2:
        print("请指定要处理的数据类型")
        print("可用的数据类型:", list(aaa.keys()))
        print("使用 'all' 处理所有数据类型")
        return
    
    data_type = sys.argv[1]
    
    if data_type == "all":
        # 处理所有数据类型
        for dt in aaa.keys():
            print(f"\n处理数据类型: {dt}")
            process_data_type(dt)
    else:
        # 处理指定的数据类型
        process_data_type(data_type)


if __name__ == "__main__":
    main()
