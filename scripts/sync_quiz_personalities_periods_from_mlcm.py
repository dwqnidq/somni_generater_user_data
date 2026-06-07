#!/usr/bin/env python3
"""
将 MLCM（mhr_codes = M-L-C-M）的 periods 同步到所有其他 4 位、language=zh、非测试人格。

源文档本身不更新。MLCM 不存在时直接退出。

用法（项目根目录）:
  python scripts/sync_quiz_personalities_periods_from_mlcm.py --dry-run
  python scripts/sync_quiz_personalities_periods_from_mlcm.py
  python scripts/sync_quiz_personalities_periods_from_mlcm.py --no-backup
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
COLLECTION = "quiz_personalities"
LANGUAGE = "zh"
EXCLUDED_MHR_NAMES = frozenset({"测试", "Test"})
SOURCE_CODES = ["M", "L", "C", "M"]
SOURCE_KEY = "M-L-C-M"
DEFAULT_BACKUP = Path(
    "output/quiz_personalities_mhr4_zh_before_mlcm_periods_sync.json"
)


def _extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


def _codes_key(codes: list[Any]) -> str:
    return "-".join(str(c) for c in codes)


def main() -> None:
    os.chdir(PROJECT_ROOT)
    load_dotenv(PROJECT_ROOT / ".env")

    ap = argparse.ArgumentParser(
        description="将 MLCM 的 periods 同步到所有其他 4 位、language=zh、非测试人格"
    )
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写库")
    ap.add_argument(
        "--backup",
        type=Path,
        default=DEFAULT_BACKUP,
        help=f"写库前备份路径（默认 {DEFAULT_BACKUP}）",
    )
    ap.add_argument("--no-backup", action="store_true", help="不写备份文件")
    args = ap.parse_args()

    uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
    if not uri:
        print("请在 .env 中配置 MONGODB_URI（或 MONGO_URI）", file=sys.stderr)
        sys.exit(1)

    db_name = os.getenv("MONGODB_DB") or _extract_db_name_from_uri(uri)

    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    try:
        coll = client[db_name][COLLECTION]
        all_docs = list(
            coll.find(
                {
                    "language": LANGUAGE,
                    "mhr_codes": {"$size": 4},
                    "mhr_name": {"$nin": list(EXCLUDED_MHR_NAMES)},
                }
            )
        )
    finally:
        client.close()

    docs_by_codes: dict[str, dict] = {}
    for doc in all_docs:
        docs_by_codes[_codes_key(doc["mhr_codes"])] = doc

    source_doc = docs_by_codes.get(SOURCE_KEY)
    if not source_doc:
        print(f"源文档 {SOURCE_KEY} 不存在，退出", file=sys.stderr)
        sys.exit(1)

    src_periods = source_doc.get("periods")
    if not src_periods:
        print(f"源文档 {SOURCE_KEY} 的 periods 为空，退出", file=sys.stderr)
        sys.exit(1)

    src_name = source_doc.get("mhr_name", "")
    print(f"源: [{SOURCE_KEY}] {src_name}\n")

    backup_docs: list[dict] = []
    ops: list[UpdateOne] = []
    update_details: list[str] = []

    for tgt_key, tgt_doc in sorted(docs_by_codes.items()):
        if tgt_key == SOURCE_KEY:
            continue
        tgt_name = tgt_doc.get("mhr_name", "")
        backup_docs.append(tgt_doc)
        ops.append(
            UpdateOne(
                {"_id": tgt_doc["_id"]},
                {"$set": {"periods": src_periods}},
            )
        )
        update_details.append(f"  {tgt_key} ({tgt_name}) ← periods from {SOURCE_KEY}")

    for line in update_details:
        print(line)

    print(f"\n汇总: 将更新 {len(ops)} 条文档（不含源 {SOURCE_KEY}）")

    if args.dry_run:
        print("[dry-run] 未写库")
        return

    if not ops:
        print("无目标文档，退出")
        return

    if not args.no_backup:
        backup_path = (
            args.backup if args.backup.is_absolute() else PROJECT_ROOT / args.backup
        )
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(
            json.dumps(backup_docs, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"已备份原始数据 → {backup_path}")

    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    try:
        coll = client[db_name][COLLECTION]
        result = coll.bulk_write(ops, ordered=False)
        print(
            f"已写回: matched={result.matched_count}, "
            f"modified={result.modified_count} → {db_name}.{COLLECTION}"
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
