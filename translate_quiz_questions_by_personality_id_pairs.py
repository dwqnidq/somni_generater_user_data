"""
按 personality / 题目与英文人格 _id 的对应关系，从 quiz_questions 拉取中文数据，
经大模型翻译后输出 JSON；每条结果的 _id 使用常量中的「值」（元组第二项）。

查询顺序：_id == 键 → personality_id == 键（ObjectId 或同 hex 字符串）。
环境变量 PAIR_LOOKUP_KEY_INDEX=1 时对调「查库键 / 输出 _id」（两端挂反时用）。
"""
from __future__ import annotations

import json
import os

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient

from translate_quiz_questions import (
    API_KEY,
    COLLECTION,
    DB_NAME,
    MONGO_URI,
    QUIZ_PERSONALITY_OBJECT_ID_PAIRS,
    batch_translate,
    collect_chinese_strings,
    replace_chinese_strings,
    set_language_en,
)

load_dotenv()

BATCH_SIZE = 50

# 0：用元组第一项查库、第二项作为输出 _id；1：对调（适用于题目关联的是 PAIRS 中另一端 ObjectId）
PAIR_LOOKUP_KEY_INDEX = int(os.getenv("PAIR_LOOKUP_KEY_INDEX", "0"))

OUTPUT_FILE = os.getenv(
    "QUIZ_QUESTIONS_MAPPED_TRANSLATE_OUTPUT",
    "quiz_questions_translated_by_personality_map.json",
)


def _find_doc(coll, key_hex: str):
    oid = ObjectId(key_hex)
    doc = coll.find_one({"_id": oid})
    if doc:
        return doc
    doc = coll.find_one({"personality_id": oid})
    if doc:
        return doc
    doc = coll.find_one({"personality_id": key_hex})
    if doc:
        return doc
    return None


def main() -> None:
    if not API_KEY:
        raise ValueError("DOUBAO_API_KEY 未设置")

    client = MongoClient(MONGO_URI)
    coll = client[DB_NAME][COLLECTION]

    rows: list[tuple[str, str, dict]] = []
    missing_keys: list[str] = []
    print(f"QUIZ_PERSONALITY_OBJECT_ID_PAIRS: {QUIZ_PERSONALITY_OBJECT_ID_PAIRS}")
    for a, b in QUIZ_PERSONALITY_OBJECT_ID_PAIRS:
        key_hex, value_hex = (a, b) if PAIR_LOOKUP_KEY_INDEX == 0 else (b, a)
        doc = _find_doc(coll, key_hex)
        if not doc:
            missing_keys.append(key_hex)
            continue
        rows.append((key_hex, value_hex, doc))

    client.close()

    if missing_keys:
        print(f"警告：以下键在 {COLLECTION} 中未匹配到文档（已跳过 {len(missing_keys)} 条）：")
        for k in missing_keys:
            print(f"  - {k}")

    if not rows:
        print("没有可翻译的数据，退出")
        return

    # 转为可 JSON 序列化结构（时间等转为字符串）
    payloads: list[tuple[str, str, dict]] = []
    for key_hex, value_hex, doc in rows:
        as_dict = json.loads(json.dumps(doc, default=str))
        payloads.append((key_hex, value_hex, as_dict))

    # 汇总去重中文字符串，批量翻译
    chinese_strings: list[str] = []
    for _, _, d in payloads:
        collect_chinese_strings(d, chinese_strings)
    unique_chinese = list(dict.fromkeys(chinese_strings))
    print(f"共 {len(payloads)} 条文档，合并后需翻译的中文字符串 {len(unique_chinese)} 条")

    translation_map: dict[str, str] = {}
    for i in range(0, len(unique_chinese), BATCH_SIZE):
        batch = unique_chinese[i : i + BATCH_SIZE]
        print(f"  翻译第 {i + 1}-{i + len(batch)} 条...")
        translated = batch_translate(batch)
        for orig, trans in zip(batch, translated):
            translation_map[orig] = trans

    out_docs: list[dict] = []
    for key_hex, value_hex, d in payloads:
        t = replace_chinese_strings(d, translation_map)
        t = set_language_en(t)
        t["_id"] = value_hex
        out_docs.append(t)
        print(f"  已处理：键 {key_hex} → 输出 _id {value_hex}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out_docs, f, ensure_ascii=False, indent=2)

    print(f"\n完成，共输出 {len(out_docs)} 条到 {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
