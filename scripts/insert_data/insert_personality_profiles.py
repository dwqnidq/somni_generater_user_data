"""
将 output/all_personality_profiles.json 插入到 quiz_personalities 集合
- 去掉 _id 字段（让 MongoDB 自动生成）
- create_time / update_time 转为 datetime 对象（存入后为 ISODate 类型）
"""
# 文件作用：用于 insert personality profiles 相关的数据处理或流程支持。

import json
import os
import sys
from datetime import datetime, timezone
from pymongo import MongoClient
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

load_dotenv()

MONGO_URI = os.getenv('MONGODB_URI', 'mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive')
DB_NAME = 'Fullive'
COLLECTION = 'quiz_personalities'
INPUT_FILE = 'output/all_personality_profiles.json'


def parse_isodate(s):
    """将 ISO 字符串转为带时区的 datetime（ISODate）"""
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def process(record):
    """处理单条记录：去掉 _id，时间字段转 datetime"""
    record.pop('_id', None)
    for field in ('create_time', 'update_time'):
        if field in record and isinstance(record[field], str):
            record[field] = parse_isodate(record[field])
    return record


def main():
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    records = [process(r) for r in data]
    print(f'准备插入 {len(records)} 条数据到 {COLLECTION}...')

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
    db = client[DB_NAME]
    result = db[COLLECTION].insert_many(records)
    client.close()

    print(f'插入成功，共 {len(result.inserted_ids)} 条')


if __name__ == '__main__':
    main()
