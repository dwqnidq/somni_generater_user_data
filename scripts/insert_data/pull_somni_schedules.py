#!/usr/bin/env python3
"""从 MongoDB somni_schedules 拉取八人格日程，按业务键去重后落盘。

去重键（与 compare_calendar_schedules.py 一致）：
  event_date, event_type, start_time, end_time, duration_minutes
同键多条时优先保留 language=zh，其次 update_time 较新，再其次 _id 字典序较大。

用法（项目根目录）：
  .venv/bin/python scripts/insert_data/pull_somni_schedules.py
  .venv/bin/python scripts/insert_data/pull_somni_schedules.py --dry-run
  .venv/bin/python scripts/insert_data/pull_somni_schedules.py --uid 69aea6d8af5e6cbf08027966
  .venv/bin/python scripts/insert_data/pull_somni_schedules.py --language ""
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

from pymongo import MongoClient

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from mongo_persona_output_specs import (  # noqa: E402
    PROJECT_ROOT,
    load_persona_uids,
    normalize_doc,
    resolve_mongo_uri,
)

COLLECTION_NAME = "somni_schedules"
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output", "somni_schedules_pull")


def normalize_event_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if "T" in text:
        return text.split("T", 1)[0]
    return text[:10] if len(text) >= 10 else text


def normalize_clock_time(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    text = str(value).strip()
    if "T" in text:
        part = text.split("T", 1)[1]
        return part[:5] if len(part) >= 5 else part
    if len(text) >= 5 and text[2] == ":":
        return text[:5]
    return text


def normalize_duration_minutes(value: Any) -> int | None:
    if value is None:
        return None
    return int(float(value))


def dedup_key(doc: dict[str, Any], uid: str) -> tuple[Any, ...]:
    return (
        str(doc.get("uid") or uid).strip(),
        normalize_event_date(doc.get("event_date")),
        str(doc.get("event_type") or "").strip(),
        normalize_clock_time(doc.get("start_time")),
        normalize_clock_time(doc.get("end_time")),
        normalize_duration_minutes(doc.get("duration_minutes")),
    )


def _sortable_id(doc: dict[str, Any]) -> str:
    oid = doc.get("_id")
    return str(oid) if oid is not None else ""


def _update_time_sort_key(doc: dict[str, Any]) -> str:
    value = doc.get("update_time")
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def pick_preferred(existing: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """同业务键两条记录时，选择应保留的一条。"""
    existing_zh = str(existing.get("language") or "").lower() == "zh"
    candidate_zh = str(candidate.get("language") or "").lower() == "zh"
    if candidate_zh and not existing_zh:
        return candidate
    if existing_zh and not candidate_zh:
        return existing

    existing_ut = _update_time_sort_key(existing)
    candidate_ut = _update_time_sort_key(candidate)
    if candidate_ut > existing_ut:
        return candidate
    if candidate_ut < existing_ut:
        return existing

    if _sortable_id(candidate) > _sortable_id(existing):
        return candidate
    return existing


def dedupe_schedules(rows: list[dict[str, Any]], uid: str) -> tuple[list[dict[str, Any]], int]:
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    removed = 0
    for row in rows:
        key = dedup_key(row, uid)
        if key in by_key:
            removed += 1
            by_key[key] = pick_preferred(by_key[key], row)
        else:
            by_key[key] = row
    deduped = list(by_key.values())
    deduped.sort(
        key=lambda r: (
            normalize_event_date(r.get("event_date")),
            normalize_clock_time(r.get("start_time")),
            str(r.get("event_type") or ""),
        )
    )
    return deduped, removed


def build_query(uid: str, language: str | None) -> dict[str, Any]:
    query: dict[str, Any] = {"uid": uid}
    if language is not None:
        query["language"] = language
    return query


def pull_uid(
    collection,
    uid: str,
    *,
    language: str | None,
    output_dir: str,
    dry_run: bool,
) -> tuple[int, int]:
    cursor = collection.find(build_query(uid, language))
    raw_rows = [normalize_doc(doc) for doc in cursor]
    rows, removed = dedupe_schedules(raw_rows, uid)

    out_path = os.path.join(output_dir, f"{uid}_calendar_events.json")
    if dry_run:
        print(
            f"  [dry-run] uid={uid[:12]}…  原始={len(raw_rows)}  去重后={len(rows)}  剔除={removed}"
        )
        return len(rows), removed

    os.makedirs(output_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(
        f"  {uid[:12]}… → {len(rows)} 条（原始 {len(raw_rows)}，去重剔除 {removed}）→ {out_path}"
    )
    return len(rows), removed


def parse_language_arg(raw: str) -> str | None:
    """--language 默认 zh；传空字符串表示不过滤 language。"""
    if raw == "":
        return None
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(
        description="拉取 somni_schedules 八人格日程（去重）"
    )
    parser.add_argument("--uid", default="", help="仅拉取指定 uid")
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出目录，默认 {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--language",
        default="zh",
        help='Mongo 查询 language 过滤，默认 zh；传 "" 表示不过滤',
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    args = parser.parse_args()

    language_filter = parse_language_arg(args.language)

    try:
        uri, db_name = resolve_mongo_uri()
    except RuntimeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    uids = load_persona_uids()
    if args.uid.strip():
        uids = [u for u in uids if u == args.uid.strip()]
        if not uids:
            print(f"错误: 配置中无 uid={args.uid}", file=sys.stderr)
            return 2

    output_dir = args.output_dir
    if not os.path.isabs(output_dir):
        output_dir = os.path.join(PROJECT_ROOT, output_dir)

    lang_label = language_filter if language_filter is not None else "(全部 language)"
    print(
        f"数据库: {db_name}  集合: {COLLECTION_NAME}  language: {lang_label}\n"
        f"人格数: {len(uids)}  输出: {output_dir}"
    )

    client = MongoClient(uri, serverSelectionTimeoutMS=15000)
    try:
        client.admin.command("ping")
        db = client[db_name]
        if COLLECTION_NAME not in db.list_collection_names():
            print(f"错误: 集合不存在: {COLLECTION_NAME}", file=sys.stderr)
            return 2
        collection = db[COLLECTION_NAME]
    except Exception as exc:
        print(f"MongoDB 连接失败: {exc}", file=sys.stderr)
        return 2

    total_rows = 0
    total_removed = 0
    for uid in uids:
        rows, removed = pull_uid(
            collection,
            uid,
            language=language_filter,
            output_dir=output_dir,
            dry_run=args.dry_run,
        )
        total_rows += rows
        total_removed += removed

    client.close()
    suffix = " (dry-run)" if args.dry_run else ""
    print(
        f"\n完成：{len(uids)} 个用户，共 {total_rows} 条日程，"
        f"去重剔除 {total_removed} 条{suffix}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
