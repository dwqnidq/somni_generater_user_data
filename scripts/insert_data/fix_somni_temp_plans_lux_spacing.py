#!/usr/bin/env python3
"""将 somni_temp_plans 集合中文案里的光照单位 lux 规范为「 Lux」。

规则 B（智能替换，避免双空格）：
  - 数字或 →、≤ 后紧跟的 lux → Lux（前补空格），如 10→8lux → 10→8 Lux
  - 已有空格的小写 lux → Lux，如 0 lux → 0 Lux
  - 已是 Lux /  Lux 的不改；仅处理小写 lux

默认 dry-run，仅统计；加 --apply 才写库。可选 --backup 导出 JSON 备份。

用法（项目根目录，需 .env 中 MONGODB_URI）:
  python scripts/insert_data/fix_somni_temp_plans_lux_spacing.py
  python scripts/insert_data/fix_somni_temp_plans_lux_spacing.py --apply
  python scripts/insert_data/fix_somni_temp_plans_lux_spacing.py --apply --backup scripts/backup2/somni_temp_plans_lux_backup.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

from pymongo import MongoClient, UpdateOne

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from mongo_persona_output_specs import resolve_mongo_uri  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DEFAULT_COLLECTION = "somni_temp_plans"
SERVER_SELECTION_TIMEOUT_MS = 15_000

# 数字、箭头、≤ 后无空格的小写 lux
_RE_LUX_ATTACHED = re.compile(r"(?<=[\d→≤])lux\b")
# 至少一个空格后的小写 lux
_RE_LUX_SPACED = re.compile(r" +lux\b")


def normalize_lux_in_string(text: str) -> str:
    if "lux" not in text:
        return text
    out = _RE_LUX_ATTACHED.sub(" Lux", text)
    out = _RE_LUX_SPACED.sub(" Lux", out)
    return out


def iter_string_paths(obj: Any, prefix: str = "") -> list[tuple[str, str]]:
    paths: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "_id":
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(iter_string_paths(val, path))
    elif isinstance(obj, list):
        for idx, val in enumerate(obj):
            path = f"{prefix}.{idx}"
            paths.extend(iter_string_paths(val, path))
    elif isinstance(obj, str):
        paths.append((prefix, obj))
    return paths


def build_set_payload(doc: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for path, old in iter_string_paths(doc):
        new = normalize_lux_in_string(old)
        if new != old:
            updates[path] = new
    return updates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--apply", action="store_true", help="写入 MongoDB（默认仅预览）")
    parser.add_argument(
        "--backup",
        default="",
        help="apply 前将全集合导出到该 JSON 路径（相对项目根或绝对路径）",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=5,
        help="dry-run 时最多打印几条字段变更样例（0 为不打印）",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    mode = "[dry-run]" if dry_run else "[apply]"
    print(f"{mode} 集合={args.collection}")

    try:
        uri, db_name = resolve_mongo_uri()
        client = MongoClient(uri, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS)
        client.admin.command("ping")
        collection = client[db_name][args.collection]
    except Exception as exc:
        print(f"MongoDB 连接失败: {exc}", file=sys.stderr)
        return 2

    docs = list(collection.find({}))
    print(f"读取文档数: {len(docs)}")

    if args.backup.strip() and not dry_run:
        backup_path = args.backup.strip()
        if not os.path.isabs(backup_path):
            backup_path = os.path.join(PROJECT_ROOT, backup_path)
        os.makedirs(os.path.dirname(backup_path) or ".", exist_ok=True)
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(docs, f, ensure_ascii=False, indent=2, default=str)
        print(f"已备份到: {backup_path}")

    ops: list[UpdateOne] = []
    docs_with_changes = 0
    field_changes = 0
    samples: list[tuple[str, str, str, str]] = []

    for doc in docs:
        doc_id = doc.get("_id")
        if doc_id is None:
            continue
        payload = build_set_payload(doc)
        if not payload:
            continue
        docs_with_changes += 1
        field_changes += len(payload)
        if args.sample > 0 and len(samples) < args.sample:
            for path, new_val in list(payload.items())[:2]:
                old_val = doc
                for part in path.split("."):
                    if isinstance(old_val, dict):
                        old_val = old_val.get(part)
                    elif isinstance(old_val, list) and part.isdigit():
                        old_val = old_val[int(part)]
                    else:
                        old_val = None
                        break
                samples.append((str(doc_id), path, str(old_val), str(new_val)))

        if not dry_run:
            ops.append(UpdateOne({"_id": doc_id}, {"$set": payload}))

    for doc_id, path, old, new in samples:
        print(f"  样例 _id={doc_id} {path}:")
        print(f"    前: {old[:120]}{'…' if len(old) > 120 else ''}")
        print(f"    后: {new[:120]}{'…' if len(new) > 120 else ''}")

    print(
        f"统计: 总文档={len(docs)}, 需更新文档={docs_with_changes}, "
        f"字段变更数={field_changes}"
    )

    if dry_run:
        print("未写入；确认后加 --apply（建议同时 --backup）。")
        client.close()
        return 0

    if not ops:
        print("无变更，跳过写入。")
        client.close()
        return 0

    try:
        result = collection.bulk_write(ops, ordered=False)
        print(
            f"写入完成: matched={result.matched_count}, "
            f"modified={result.modified_count}"
        )
    except Exception as exc:
        print(f"批量更新失败: {exc}", file=sys.stderr)
        client.close()
        return 1

    client.close()
    print(f"完成时间(UTC): {datetime.now(timezone.utc).isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
