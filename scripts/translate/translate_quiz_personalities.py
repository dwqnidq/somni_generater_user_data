#!/usr/bin/env python3
"""
从 MongoDB 读取 quiz_personalities（mhr_codes 长度为 4、language=zh、排除测试人格），
对所有中文字段逐条翻译为英文（多轮翻译 + 模型校验；未通过则继续翻译再校验），
终检仍含中文时自动补翻，全部通过后才写入 output/quiz_personalities_mhr4_en.json。

用法（在项目根目录）:
  python scripts/translate/translate_quiz_personalities.py --dry-run
  python scripts/translate/translate_quiz_personalities.py
  python scripts/translate/translate_quiz_personalities.py --translate-passes 3
  python scripts/translate/translate_quiz_personalities.py --max-verify-rounds 0
  python scripts/translate/translate_quiz_personalities.py --no-cache
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from scripts.translate._common import (  # noqa: E402
    DEFAULT_CACHE_FILE,
    DEFAULT_OUTPUT_REPAIR_PASSES,
    MODEL_NAME,
    VERIFY_SAFETY_MAX_ROUNDS,
    assert_no_chinese_in_docs,
    collect_chinese_strings,
    load_translation_cache,
    repair_remaining_chinese,
    save_translation_cache,
    translate_document_list,
)

MONGO_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
COLLECTION = "quiz_personalities"
EXCLUDED_MHR_NAMES = frozenset({"测试", "Test"})
DEFAULT_MAX_VERIFY_ROUNDS = 0

DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "quiz_personalities_mhr4_en.json"


def _db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        name = (parsed.path or "").lstrip("/").split("?")[0]
        return name or fallback
    except Exception:
        return fallback


def fetch_docs() -> list[dict[str, Any]]:
    if not MONGO_URI:
        raise ValueError("请在 .env 中配置 MONGODB_URI（或 MONGO_URI）")

    from pymongo import MongoClient

    db_name = os.getenv("MONGODB_DB") or _db_name_from_uri(MONGO_URI)
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
    try:
        coll = client[db_name][COLLECTION]
        cursor = coll.find(
            {
                "mhr_codes": {"$size": 4},
                "language": "zh",
                "mhr_name": {"$nin": list(EXCLUDED_MHR_NAMES)},
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
        description=(
            "翻译 quiz_personalities（mhr_codes=4, language=zh, 排除测试）为英文版；"
            "校验未通过会继续翻译再校验"
        ),
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
        default=DEFAULT_MAX_VERIFY_ROUNDS,
        help=(
            f"单条校验失败后的最大重试轮数（默认 {DEFAULT_MAX_VERIFY_ROUNDS}=持续重试至"
            f" {VERIFY_SAFETY_MAX_ROUNDS} 轮上限；设为正整数可限制轮数）"
        ),
    )
    parser.add_argument(
        "--max-output-repair-passes",
        type=int,
        default=DEFAULT_OUTPUT_REPAIR_PASSES,
        help=f"终检仍含中文时的补翻轮数（默认 {DEFAULT_OUTPUT_REPAIR_PASSES}）",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="不使用已校验缓存，全部重新翻译+校验",
    )
    args = parser.parse_args()

    translate_passes = max(2, args.translate_passes)
    max_verify_rounds = args.max_verify_rounds
    max_output_repair = max(1, args.max_output_repair_passes)
    use_cache = not args.no_cache

    print(
        f"正在从 MongoDB 读取 {COLLECTION} "
        f"（mhr_codes 长度 4, language=zh, mhr_name 非测试）..."
    )
    docs = fetch_docs()
    print(f"读取到 {len(docs)} 条记录")

    if not docs:
        print("无数据，退出。")
        return

    chinese_strings: list[str] = []
    collect_chinese_strings(docs, chinese_strings)
    unique_chinese = list(dict.fromkeys(chinese_strings))
    print(f"中文字符串 {len(unique_chinese)} 条（去重）")

    if args.dry_run:
        api_per_string = translate_passes + 1
        verify_desc = (
            f"持续重试（上限 {VERIFY_SAFETY_MAX_ROUNDS} 轮）"
            if max_verify_rounds <= 0
            else f"最多 {max(1, max_verify_rounds)} 轮"
        )
        print(
            f"[dry-run] 每条约 {api_per_string} 次 API + 校验 {verify_desc}；"
            f"终检补翻最多 {max_output_repair} 轮"
        )
        print(f"预估总 API 调用: ~{len(unique_chinese) * api_per_string} 次（未计重试）")
        return

    cache_path = (
        args.cache_file
        if args.cache_file.is_absolute()
        else PROJECT_ROOT / args.cache_file
    )
    cache = load_translation_cache(cache_path)
    verify_desc = (
        f"持续重试（上限 {VERIFY_SAFETY_MAX_ROUNDS} 轮）"
        if max_verify_rounds <= 0
        else f"最多 {max(1, max_verify_rounds)} 轮"
    )
    print(f"模型: {MODEL_NAME}")
    print(f"每条 {translate_passes} 次翻译 + 校验 {verify_desc}")
    print(f"终检补翻最多 {max_output_repair} 轮")
    print(f"已校验缓存: {cache_path.relative_to(PROJECT_ROOT)}（{len(cache)} 条）")

    template = "translate_single_quiz_personality__translate.md"
    en_docs = translate_document_list(
        docs,
        cache,
        target_language="en",
        translate_passes=translate_passes,
        max_verify_rounds=max_verify_rounds,
        use_cache=use_cache,
        translate_template=template,
    )

    print("\n=== 终检：补翻残留中文 ===")
    en_docs = repair_remaining_chinese(
        en_docs,
        cache,
        target_language="en",
        translate_passes=translate_passes,
        max_verify_rounds=max_verify_rounds,
        max_repair_passes=max_output_repair,
        use_cache=use_cache,
        translate_template=template,
    )
    assert_no_chinese_in_docs(en_docs)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(en_docs, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    tmp.replace(output_path)
    print(f"\n校验通过，已写入 {output_path.relative_to(PROJECT_ROOT)}（{len(en_docs)} 条）")

    if unique_chinese:
        save_translation_cache(cache_path, cache)
        print(f"缓存已更新: {cache_path.relative_to(PROJECT_ROOT)}（{len(cache)} 条）")

    print("\n完成。")


if __name__ == "__main__":
    main()
