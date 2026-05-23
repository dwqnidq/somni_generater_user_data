#!/usr/bin/env python3
"""
从 MongoDB 读取 quiz_personalities（mhr_codes 长度为 4 且 language=zh），
对所有中文字段逐条翻译为英文（每条 3 次翻译 + 模型校验），
输出到 output/quiz_personalities_mhr4_en.json，language 字段改为 en。

用法（在项目根目录）:
  python scripts/translate/translate_quiz_personalities.py
  python scripts/translate/translate_quiz_personalities.py --dry-run
  python scripts/translate/translate_quiz_personalities.py --translate-passes 3
  python scripts/translate/translate_quiz_personalities.py --no-cache
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from scripts.translate._common import (  # noqa: E402
    DEFAULT_CACHE_FILE,
    MAX_VERIFY_ROUNDS,
    MODEL_NAME,
    TRANSLATE_PASSES,
    collect_chinese_strings,
    load_translation_cache,
    save_translation_cache,
    translate_document_list,
)

MONGO_URI = os.getenv(
    "MONGODB_URI",
    "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive",
)
DB_NAME = "Fullive"
COLLECTION = "quiz_personalities"

DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "quiz_personalities_mhr4_en.json"


def fetch_docs() -> list[dict[str, Any]]:
    from pymongo import MongoClient

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
    try:
        coll = client[DB_NAME][COLLECTION]
        cursor = coll.find(
            {
                "$expr": {"$eq": [{"$size": "$mhr_codes"}, 4]},
                "language": "zh",
            }
        )
        docs = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            docs.append(doc)
        return docs
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="翻译 quiz_personalities（mhr_codes=4, language=zh）为英文版",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(DEFAULT_OUTPUT),
        help=f"输出 JSON 路径（默认 {DEFAULT_OUTPUT.relative_to(PROJECT_ROOT)}）",
    )
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=DEFAULT_CACHE_FILE,
        help=f"翻译缓存路径（默认 {DEFAULT_CACHE_FILE.relative_to(PROJECT_ROOT)}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计待翻译字符串，不写文件、不调用 API",
    )
    parser.add_argument(
        "--translate-passes",
        type=int,
        default=3,
        help="每条中文独立翻译次数（默认 3，至少 2）",
    )
    parser.add_argument(
        "--max-verify-rounds",
        type=int,
        default=MAX_VERIFY_ROUNDS,
        help=f"校验失败后的最大重试轮数（默认 {MAX_VERIFY_ROUNDS}）",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="不使用已校验缓存，全部重新翻译+校验",
    )
    args = parser.parse_args()

    translate_passes = max(2, args.translate_passes)
    max_verify_rounds = max(1, args.max_verify_rounds)
    use_cache = not args.no_cache

    # 1. 从 MongoDB 读取
    print(f"正在从 MongoDB 读取 {COLLECTION}（mhr_codes 长度 4, language=zh）...")
    docs = fetch_docs()
    print(f"读取到 {len(docs)} 条记录")

    if not docs:
        print("无数据，退出。")
        return

    # 2. 统计中文字符串
    chinese_strings: list[str] = []
    collect_chinese_strings(docs, chinese_strings)
    unique_chinese = list(dict.fromkeys(chinese_strings))
    print(f"中文字符串 {len(unique_chinese)} 条（去重）")

    if args.dry_run:
        api_per_string = translate_passes + 1
        print(
            f"[dry-run] 每条约 {api_per_string} 次 API 调用，"
            f"最多 {max_verify_rounds} 轮校验"
        )
        print(f"预估总 API 调用: ~{len(unique_chinese) * api_per_string} 次")
        return

    # 3. 加载缓存并翻译
    cache_path = (
        args.cache_file
        if args.cache_file.is_absolute()
        else PROJECT_ROOT / args.cache_file
    )
    cache = load_translation_cache(cache_path)
    print(f"模型: {MODEL_NAME}")
    print(f"每条 {translate_passes} 次翻译 + 最多 {max_verify_rounds} 轮校验")
    print(f"已校验缓存: {cache_path.relative_to(PROJECT_ROOT)}（{len(cache)} 条）")

    en_docs = translate_document_list(
        docs,
        cache,
        target_language="en",
        translate_passes=translate_passes,
        max_verify_rounds=max_verify_rounds,
        use_cache=use_cache,
        translate_template="translate_single_quiz_personality__translate.md",
    )

    # 4. 写入输出
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(en_docs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n已写入 {output_path.relative_to(PROJECT_ROOT)}（{len(en_docs)} 条）")

    # 5. 保存缓存
    if len(unique_chinese) > 0:
        save_translation_cache(cache_path, cache)
        print(f"缓存已更新: {cache_path.relative_to(PROJECT_ROOT)}（{len(cache)} 条）")

    print("\n完成。")


if __name__ == "__main__":
    main()
