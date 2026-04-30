"""
将 output/quiz_questions_translated_by_id_pairs.json 插入到 quiz_questions 集合
- 保留 _id 字段，并将字符串 _id 转为 ObjectId
- create_time / update_time 转为 datetime 对象（存入后为 ISODate 类型）
"""
# 文件作用：用于 insert quiz questions 相关的数据处理或流程支持。

import json
import os
import sys
from datetime import datetime
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

load_dotenv()

MONGO_URI = os.getenv('MONGODB_URI', 'mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive')
DB_NAME = 'Fullive'
COLLECTION = 'quiz_questions'
INPUT_FILE = 'output/quiz_questions_translated_by_id_pairs.json'


def parse_datetime(s):
    """将时间字符串转为 datetime：支持 ISO（含 Z）与 'YYYY-MM-DD HH:MM:SS.ffffff'"""
    if not isinstance(s, str):
        return s
    s = s.strip()
    if s.endswith('Z') or 'T' in s:
        return datetime.fromisoformat(s.replace('Z', '+00:00'))
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(s)


def process(record):
    """处理单条记录：_id 转 ObjectId，时间字段转 datetime"""
    if '_id' in record:
        if isinstance(record['_id'], str):
            record['_id'] = ObjectId(record['_id'])
    for field in ('create_time', 'update_time'):
        if field in record and isinstance(record[field], str):
            record[field] = parse_datetime(record[field])
    return record


def main():
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 兼容单个对象或数组
    records = data if isinstance(data, list) else [data]
    records = [process(r) for r in records]
    print(f'准备插入 {len(records)} 条数据到 {COLLECTION}...')

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
    db = client[DB_NAME]
    result = db[COLLECTION].insert_many(records)
    client.close()

    print(f'插入成功，共 {len(result.inserted_ids)} 条')


if __name__ == '__main__':
    main()
