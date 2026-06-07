#!/usr/bin/env python3
"""从 MongoDB 拉取八人格各集合数据，落盘到 output/_mongo_pull/{uid}_{类型}.json。

映射见 mongo_persona_output_specs.OUTPUT_TYPE_SPECS（与 insert_somni_records.py 一致）。

用法：
  .venv/bin/python scripts/insert_data/pull_mongo_persona_data.py
  .venv/bin/python scripts/insert_data/pull_mongo_persona_data.py --uid 69aea593af5e6cbf08027964
  .venv/bin/python scripts/insert_data/pull_mongo_persona_data.py --types health_data,sleep_events
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from pymongo import MongoClient

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from mongo_persona_output_specs import (  # noqa: E402
    MONGO_PULL_DIR,
    OutputTypeSpec,
    build_query,
    load_persona_uids,
    normalize_doc,
    parse_type_names,
    pull_file_path,
    resolve_mongo_uri,
)


def pull_one(
    collection,
    uid: str,
    spec: OutputTypeSpec,
    *,
    dry_run: bool,
) -> int:
    query = build_query(uid, spec)
    cursor = collection.find(query)
    rows = [normalize_doc(doc) for doc in cursor]
    rows.sort(key=lambda r: str(r.get(spec.compare_key) or r.get("_id") or ""))
    if dry_run:
        print(f"  [dry-run] {spec.collection} uid={uid[:12]}… → {len(rows)} 条")
        return len(rows)
    out_path = pull_file_path(uid, spec)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"  {spec.output_suffix}: {len(rows)} 条 → {out_path}")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="拉取八人格 Mongo 数据到 output/_mongo_pull")
    parser.add_argument("--uid", default="", help="仅拉取指定 uid")
    parser.add_argument("--types", default="", help="逗号分隔 output 后缀，默认全部")
    parser.add_argument("--dry-run", action="store_true", help="只统计条数不写文件")
    args = parser.parse_args()

    try:
        specs = parse_type_names(args.types)
        uri, db_name = resolve_mongo_uri()
    except (ValueError, RuntimeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    uids = load_persona_uids()
    if args.uid.strip():
        uids = [u for u in uids if u == args.uid.strip()]
        if not uids:
            print(f"错误: 配置中无 uid={args.uid}", file=sys.stderr)
            return 2

    print(f"数据库: {db_name}  输出: {MONGO_PULL_DIR}  人格数: {len(uids)}  类型: {len(specs)}")
    client = MongoClient(uri, serverSelectionTimeoutMS=15000)
    try:
        client.admin.command("ping")
        db = client[db_name]
    except Exception as exc:
        print(f"MongoDB 连接失败: {exc}", file=sys.stderr)
        return 2

    total = 0
    for uid in uids:
        print(f"\n[{uid[:12]}…]")
        for spec in specs:
            if spec.collection not in db.list_collection_names():
                print(f"  [跳过] 集合不存在: {spec.collection}")
                continue
            total += pull_one(db[spec.collection], uid, spec, dry_run=args.dry_run)

    client.close()
    print(f"\n完成，共拉取 {total} 条记录" + (" (dry-run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
