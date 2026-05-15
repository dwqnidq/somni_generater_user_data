"""
从 quiz_personalities 文档中递归收集所有 material_id，检查 audio_materials 是否存在 _id 匹配的记录。

默认仅处理 mhr_codes 长度为 4 的文档（与空 URL 检查脚本一致）。缺失则在控制台打印（中文汇总）。

依赖：环境变量 MONGODB_URI（或 MONGO_URI），可选 MONGODB_DB。
"""
# 文件作用：只读校验 quiz_personalities 引用的 material_id 是否在 audio_materials 中存在。

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Iterator
from urllib.parse import urlparse

from bson import ObjectId
from bson.errors import InvalidId
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
QP_COLL = "quiz_personalities"
AUDIO_COLL = "audio_materials"


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


def iter_material_id_values(obj: Any) -> Iterator[Any]:
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "material_id":
                yield val
            if isinstance(val, (dict, list)):
                yield from iter_material_id_values(val)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                yield from iter_material_id_values(item)


def _normalize_to_object_ids(raw_values: list[Any]) -> tuple[list[ObjectId], list[str]]:
    """返回 (去重后的 ObjectId 列表, 无法解析为 ObjectId 的原始值说明)。"""
    oids: list[ObjectId] = []
    seen: set[str] = set()
    bad: list[str] = []

    for v in raw_values:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        oid: ObjectId | None = None
        if isinstance(v, ObjectId):
            oid = v
        elif isinstance(v, str):
            try:
                oid = ObjectId(v.strip())
            except InvalidId:
                bad.append(f"字符串（非法 ObjectId）: {v!r}")
                continue
        else:
            bad.append(f"非 ObjectId/字符串: {type(v).__name__} = {v!r}")
            continue
        key = str(oid)
        if key not in seen:
            seen.add(key)
            oids.append(oid)
    return oids, bad


def _fetch_existing_ids(coll: Any, oids: list[ObjectId], batch_size: int = 500) -> set[str]:
    existing: set[str] = set()
    for i in range(0, len(oids), batch_size):
        chunk = oids[i : i + batch_size]
        for doc in coll.find({"_id": {"$in": chunk}}, projection={"_id": 1}):
            existing.add(str(doc["_id"]))
    return existing


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从 quiz_personalities 收集 material_id，检查 audio_materials 中是否存在对应文档。"
    )
    parser.add_argument(
        "--language",
        choices=("zh", "en"),
        default=None,
        help="若集合含 language 字段，可只查 zh 或 en；不传则不过滤语言。",
    )
    parser.add_argument(
        "--all-personalities",
        action="store_true",
        help="不设 mhr_codes 长度过滤，扫描 quiz_personalities 全表。",
    )
    args = parser.parse_args()

    query: dict[str, Any] = {}
    if not args.all_personalities:
        query["$expr"] = {"$eq": [{"$size": "$mhr_codes"}, 4]}
    if args.language:
        query["language"] = args.language

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
    db = client[DB_NAME]
    qp = db[QP_COLL]
    audio = db[AUDIO_COLL]

    all_raw: list[Any] = []
    doc_count = 0
    for doc in qp.find(query):
        doc_count += 1
        all_raw.extend(iter_material_id_values(doc))

    oids, bad = _normalize_to_object_ids(all_raw)
    existing = _fetch_existing_ids(audio, oids)
    missing = [o for o in oids if str(o) not in existing]

    print("—" * 60)
    print(f"已扫描 quiz_personalities 文档数: {doc_count}")
    print(f"收集到的 material_id 原始出现次数: {len(all_raw)}")
    print(f"去重后合法 ObjectId 数量: {len(oids)}")
    print(f"audio_materials 中已存在: {len(existing)}")
    if bad:
        print(f"\n以下值无法作为 ObjectId 使用（共 {len(bad)} 条）:")
        for line in bad:
            print(f"  - {line}")
    if missing:
        print(f"\n以下 material_id 在 audio_materials 中不存在（共 {len(missing)} 条）:")
        for o in missing:
            print(f"  - {o}")
    else:
        print("\n所有合法 material_id 在 audio_materials 中均有对应文档。")
    print("—" * 60)


if __name__ == "__main__":
    main()
