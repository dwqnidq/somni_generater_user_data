#!/usr/bin/env python3
"""
将 8 个指定源人格的 periods 数据同步给同组（前 3 位 mhr_codes 相同）的其他 4 位人格。

源人格：MLRM, MHCM, MHRS, EHRM, EHCM, MLCM, ELRM, ELCM
规则：只修改 mhr_codes 长度为 4 的文档，跳过长度为 3 的文档。

用法（项目根目录）:
  python scripts/sync_quiz_personalities_periods_by_group.py --dry-run
  python scripts/sync_quiz_personalities_periods_by_group.py
  python scripts/sync_quiz_personalities_periods_by_group.py --no-backup
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
DEFAULT_BACKUP = Path("output/quiz_personalities_mhr4_zh_before_periods_sync.json")

# 8 个源人格的完整编码
SOURCE_CODES = [
    ["M", "L", "R", "M"],
    ["M", "H", "C", "M"],
    ["M", "H", "R", "S"],
    ["E", "H", "R", "M"],
    ["E", "H", "C", "M"],
    ["M", "L", "C", "M"],
    ["E", "L", "R", "M"],
    ["E", "L", "C", "M"],
]


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

    ap = argparse.ArgumentParser(description="同步 8 个源人格的 periods 到同组其他 4 位人格")
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
        # 只查询 language=zh 的 4 位文档
        all_docs = list(coll.find({
            "language": LANGUAGE,
            "mhr_codes": {"$size": 4},
            "mhr_name": {"$nin": list(EXCLUDED_MHR_NAMES)},
        }))
    finally:
        client.close()

    # 建索引：完整编码 → 文档
    docs_by_codes: dict[str, dict] = {}
    for doc in all_docs:
        key = _codes_key(doc["mhr_codes"])
        docs_by_codes[key] = doc

    # 确认 8 个源文档都存在
    source_docs: list[tuple[str, dict]] = []
    for codes in SOURCE_CODES:
        key = _codes_key(codes)
        doc = docs_by_codes.get(key)
        if not doc:
            print(f"[警告] 源文档 {key} 不存在，跳过", file=sys.stderr)
            continue
        source_docs.append((key, doc))

    print(f"找到 {len(source_docs)} 个源文档\n")

    # 备份目标文档（被覆盖的文档）
    backup_docs: list[dict] = []
    ops: list[UpdateOne] = []
    update_details: list[str] = []

    for src_key, src_doc in source_docs:
        src_codes = src_doc["mhr_codes"]
        prefix = "-".join(src_codes[:3])
        src_name = src_doc.get("mhr_name", "")
        src_periods = src_doc.get("periods")

        if not src_periods:
            print(f"  [{src_key}] {src_name}: periods 为空，跳过该组")
            continue

        # 找同组的其他 4 位文档
        targets = [
            (k, d) for k, d in docs_by_codes.items()
            if k != src_key and "-".join(d["mhr_codes"][:3]) == prefix
        ]

        if not targets:
            print(f"  [{src_key}] {src_name}: 无同组目标文档")
            continue

        print(f"  [{src_key}] {src_name} → 同组 {len(targets)} 个目标:")
        for tgt_key, tgt_doc in targets:
            tgt_name = tgt_doc.get("mhr_name", "")
            backup_docs.append(tgt_doc)
            ops.append(UpdateOne(
                {"_id": tgt_doc["_id"]},
                {"$set": {"periods": src_periods}},
            ))
            update_details.append(f"    {tgt_key} ({tgt_name}) ← periods from {src_key}")

    print()
    for line in update_details:
        print(line)

    print(f"\n汇总: {len(source_docs)} 个源, 将更新 {len(ops)} 条文档")

    if args.dry_run:
        print("[dry-run] 未写库")
        return

    if not ops:
        print("无变更，退出")
        return

    if not args.no_backup:
        backup_path = args.backup if args.backup.is_absolute() else PROJECT_ROOT / args.backup
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
