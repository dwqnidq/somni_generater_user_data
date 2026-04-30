"""文件作用：用于 insert quiz surveys 相关的数据处理或流程支持。"""

import json
import os
import sys
from datetime import datetime

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
COLLECTION = "quiz_surveys"
INPUT_FILES = [
    "output/generated_somni_001_en.json",
    "output/generated_somni_vip_en.json",
]


def parse_isodate(value):
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def normalize_doc(doc):
    for field in ("create_time", "update_time"):
        if field in doc:
            doc[field] = parse_isodate(doc[field])
    return doc


def main():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    db = client[DB_NAME]
    collection = db[COLLECTION]

    total_matched = 0
    total_modified = 0
    total_upserted = 0

    for path in INPUT_FILES:
        with open(path, "r", encoding="utf-8") as f:
            doc = normalize_doc(json.load(f))

        filt = {"code": doc.get("code"), "language": doc.get("language")}
        result = collection.update_one(filt, {"$set": doc}, upsert=True)

        total_matched += result.matched_count
        total_modified += result.modified_count
        if result.upserted_id is not None:
            total_upserted += 1

        print(
            f"{path} => code={doc.get('code')}, language={doc.get('language')}, "
            f"matched={result.matched_count}, modified={result.modified_count}, "
            f"upserted_id={result.upserted_id}"
        )

    client.close()
    print(
        f"DONE: matched={total_matched}, modified={total_modified}, "
        f"upserted={total_upserted}"
    )


if __name__ == "__main__":
    main()
