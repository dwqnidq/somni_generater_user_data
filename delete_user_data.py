"""文件作用：用于 delete user data 相关的数据处理或流程支持。"""

# MongoDB脚本：根据用户ID删除指定集合的数据

import pymongo
import os
import argparse
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()

# 固定用户ID列表（不再从配置文件读取）
user_ids = [
    # "69aea593af5e6cbf08027964",
    # "69aea63eaf5e6cbf08027965",
    # "69aea6d8af5e6cbf08027966",
    "69aea6e3af5e6cbf08027967",
    "69aea6e8af5e6cbf08027968",
    # "69aea6eeaf5e6cbf08027969",
    # "69aea6f3af5e6cbf0802796a",
    # "69aea6f8af5e6cbf0802796b",
]
print(f"Using fixed {len(user_ids)} user IDs: {user_ids}")

# 需要删除数据的集合列表
collections = [
    "somni_reports",
    "somni_physiological_data",
    "somni_events",
    "somni_environment_data",
    "somni_records",
    "somni_schedules",
    "somni_ai_insights"
]

# MongoDB连接信息
mongo_uri = os.environ.get('MONGODB_URI', 'mongodb://localhost:27017')
db_name = "Fullive"  # 从连接字符串中提取或硬编码

def parse_args():
    parser = argparse.ArgumentParser(
        description="根据用户ID删除 MongoDB 指定集合的数据，可选按 language 过滤。"
    )
    parser.add_argument(
        "--language",
        choices=["zh", "en"],
        default=None,
        help="仅删除指定语言的数据（匹配字段 language）。不传则按 uid 删除全部语言数据。",
    )
    return parser.parse_args()


def delete_user_data(language: str | None = None):
    try:
        # 连接到MongoDB
        client = pymongo.MongoClient(mongo_uri)
        print("Connected to MongoDB")
        
        db = client[db_name]
        
        # 遍历每个用户ID
        for user_id in user_ids:
            print(f"\nProcessing user ID: {user_id}")
            # 遍历每个集合，删除对应用户的数据
            for collection_name in collections:
                collection = db[collection_name]
                delete_filter = {"uid": user_id}
                if language is not None:
                    delete_filter["language"] = language
                result = collection.delete_many(delete_filter)
                print(f"Deleted {result.deleted_count} documents from {collection_name}")
        
        print("\nAll user data deletion completed successfully")
    except Exception as error:
        print(f"Error deleting user data: {error}")
    finally:
        if 'client' in locals():
            client.close()
            print("MongoDB connection closed")

if __name__ == "__main__":
    args = parse_args()
    delete_user_data(language=args.language)

# 注意：在运行此脚本之前，请确保：
# 1. 已安装pymongo库：pip install pymongo
# 2. 修改了正确的MongoDB连接字符串和数据库名称
# 3. 确认要删除的数据，因为此操作不可恢复