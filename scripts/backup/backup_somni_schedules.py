#!/usr/bin/env python3
"""备份 MongoDB somni_schedules 集合。

默认导出为 JSON（按 uid 分文件，全量、不去重），与 backup/{uid}_calendar_events.json 格式一致。

用法（项目根目录）:
  .venv/bin/python scripts/backup/backup_somni_schedules.py
  .venv/bin/python scripts/backup/backup_somni_schedules.py --dry-run
  .venv/bin/python scripts/backup/backup_somni_schedules.py --uid 69aea6d8af5e6cbf08027966
  .venv/bin/python scripts/backup/backup_somni_schedules.py --all-uids
  .venv/bin/python scripts/backup/backup_somni_schedules.py --format mongodump --gzip
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from pymongo import MongoClient

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
INSERT_DATA_DIR = os.path.join(PROJECT_ROOT, "scripts", "insert_data")
if INSERT_DATA_DIR not in sys.path:
    sys.path.insert(0, INSERT_DATA_DIR)

from mongo_persona_output_specs import (  # noqa: E402
    load_persona_uids,
    normalize_doc,
    resolve_mongo_uri,
)

COLLECTION_NAME = "somni_schedules"
CALENDAR_EVENTS_SUFFIX = "calendar_events"
DEFAULT_BACKUP_PARENT = os.path.join(PROJECT_ROOT, "backup")


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _default_json_output_dir() -> str:
    return os.path.join(DEFAULT_BACKUP_PARENT, f"somni_schedules_{_timestamp()}")


def _sort_key(doc: dict[str, Any]) -> tuple[str, str, str]:
    event_date = str(doc.get("event_date") or "")[:10]
    start_time = str(doc.get("start_time") or "")
    event_type = str(doc.get("event_type") or "")
    return (event_date, start_time, event_type)


def _resolve_output_dir(raw: str | None) -> str:
    if raw:
        return raw if os.path.isabs(raw) else os.path.join(PROJECT_ROOT, raw)
    return _default_json_output_dir()


def _discover_uids(collection, persona_uids: list[str], all_uids: bool) -> list[str]:
    if all_uids:
        return sorted({str(u).strip() for u in collection.distinct("uid") if str(u).strip()})
    return persona_uids


def backup_json(
    collection,
    uids: list[str],
    output_dir: str,
    *,
    dry_run: bool,
) -> tuple[int, int]:
    """按 uid 导出 JSON。返回 (文档总数, 用户数)。"""
    total_docs = 0
    if not dry_run:
        os.makedirs(output_dir, exist_ok=True)

    for uid in uids:
        cursor = collection.find({"uid": uid})
        rows = [normalize_doc(doc) for doc in cursor]
        rows.sort(key=_sort_key)
        total_docs += len(rows)

        out_path = os.path.join(output_dir, f"{uid}_{CALENDAR_EVENTS_SUFFIX}.json")
        if dry_run:
            print(f"  [dry-run] {uid[:12]}… → {len(rows)} 条")
            continue

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        print(f"  {uid[:12]}… → {len(rows)} 条 → {out_path}")

    return total_docs, len(uids)


def backup_mongodump(
    mongo_uri: str,
    output_dir: str | None,
    *,
    gzip: bool,
) -> bool:
    if not shutil.which("mongodump"):
        print("错误: 未找到 mongodump，请安装: brew install mongodb-database-tools", file=sys.stderr)
        return False

    base_dir = output_dir or os.path.join(
        DEFAULT_BACKUP_PARENT, f"mongodump_somni_schedules_{_timestamp()}"
    )
    coll_out = os.path.join(base_dir, COLLECTION_NAME)
    cmd = [
        "mongodump",
        "--uri",
        mongo_uri,
        "--collection",
        COLLECTION_NAME,
        "--out",
        coll_out,
    ]
    if gzip:
        cmd.append("--gzip")

    safe_uri = mongo_uri.split("@")[-1] if "@" in mongo_uri else mongo_uri
    print(f"mongodump 集合: {COLLECTION_NAME}")
    print(f"URI: {safe_uri}")
    print(f"输出: {base_dir}")

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        print(f"备份完成: {base_dir}")
        return True
    except subprocess.CalledProcessError as exc:
        print(f"备份失败: {exc.stderr.strip()}", file=sys.stderr)
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="备份 somni_schedules 集合")
    parser.add_argument(
        "--format",
        choices=("json", "mongodump"),
        default="json",
        help="json：按 uid 导出 calendar_events JSON；mongodump：BSON 备份",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="输出目录；json 默认 backup/somni_schedules_{时间戳}/",
    )
    parser.add_argument("--uid", default="", help="仅备份指定 uid")
    parser.add_argument(
        "--all-uids",
        action="store_true",
        help="备份集合内全部 uid（默认仅 config 中八人格）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计条数，不写文件")
    parser.add_argument("--gzip", action="store_true", help="mongodump 时压缩")
    return parser.parse_args()


def main() -> int:
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    args = parse_args()

    if args.format == "mongodump":
        mongo_uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or ""
        if not mongo_uri:
            print("错误: 未配置 MONGODB_URI 或 MONGO_URI（.env）", file=sys.stderr)
            return 2
        out = args.output_dir.strip() or None
        if out and not os.path.isabs(out):
            out = os.path.join(PROJECT_ROOT, out)
        ok = backup_mongodump(mongo_uri, out, gzip=args.gzip)
        return 0 if ok else 1

    try:
        uri, db_name = resolve_mongo_uri()
    except RuntimeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    persona_uids = load_persona_uids()
    if args.uid.strip() and not args.all_uids:
        persona_uids = [u for u in persona_uids if u == args.uid.strip()]
        if not persona_uids:
            print(f"错误: 配置中无 uid={args.uid}", file=sys.stderr)
            return 2

    output_dir = _resolve_output_dir(args.output_dir.strip() or None)
    scope = "集合内全部 uid" if args.all_uids else "配置中八人格"
    if args.uid.strip():
        scope += f"（仅 {args.uid.strip()[:12]}…）"
    print(f"数据库: {db_name}  集合: {COLLECTION_NAME}  范围: {scope}")
    print(f"输出: {output_dir}" + (" (dry-run)" if args.dry_run else ""))

    client = MongoClient(uri, serverSelectionTimeoutMS=15000)
    try:
        client.admin.command("ping")
        db = client[db_name]
        if COLLECTION_NAME not in db.list_collection_names():
            print(f"错误: 集合不存在: {COLLECTION_NAME}", file=sys.stderr)
            return 2
        collection = db[COLLECTION_NAME]
        target_uids = _discover_uids(collection, persona_uids, args.all_uids)
        if args.uid.strip():
            target_uids = [u for u in target_uids if u == args.uid.strip()]
        if not target_uids:
            print("无待备份的 uid", file=sys.stderr)
            return 2

        total_docs, user_count = backup_json(
            collection,
            target_uids,
            output_dir,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"MongoDB 连接或查询失败: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()

    print(f"\n完成: {user_count} 个用户, 共 {total_docs} 条日程")
    return 0


if __name__ == "__main__":
    sys.exit(main())
