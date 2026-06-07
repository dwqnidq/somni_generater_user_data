#!/usr/bin/env python3
"""从 MongoDB somni_schedules 按 event_date 拉取日程，写入 JSON 文件。

默认拉取 2026-05-29、2026-05-30；默认仅八人格配置中的 uid。
兼容 event_date 为字符串（YYYY-MM-DD）或 datetime 的存储格式。

用法：
  .venv/bin/python scripts/insert_data/pull_somni_schedules_by_dates.py
  .venv/bin/python scripts/insert_data/pull_somni_schedules_by_dates.py --dates 2026-05-29,2026-05-30
  .venv/bin/python scripts/insert_data/pull_somni_schedules_by_dates.py --output-dir output/somni_schedules_pull
  .venv/bin/python scripts/insert_data/pull_somni_schedules_by_dates.py --merge-one
  .venv/bin/python scripts/insert_data/pull_somni_schedules_by_dates.py --all-uids
  .venv/bin/python scripts/insert_data/pull_somni_schedules_by_dates.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Any

from pymongo import MongoClient

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from mongo_persona_output_specs import (  # noqa: E402
    load_persona_uids,
    normalize_doc,
    resolve_mongo_uri,
)

COLLECTION_NAME = "somni_schedules"
DEFAULT_DATES = ("2026-05-29", "2026-05-30")
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output", "somni_schedules_pull")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_dates(raw: str) -> list[str]:
    dates = [d.strip() for d in raw.split(",") if d.strip()]
    if not dates:
        raise ValueError("至少指定一个日期（YYYY-MM-DD）")
    for d in dates:
        if not DATE_PATTERN.match(d):
            raise ValueError(f"非法日期格式: {d!r}，应为 YYYY-MM-DD")
    return dates


def build_event_date_filter(dates: list[str]) -> dict[str, Any]:
    """构造兼容字符串与 datetime 的 event_date 查询条件。"""
    variants: list[Any] = []
    for d in dates:
        variants.append(d)
        variants.append(datetime.strptime(d, "%Y-%m-%d"))
    return {"event_date": {"$in": variants}}


def sort_key(doc: dict[str, Any]) -> tuple[str, str, str]:
    ed = doc.get("event_date")
    if isinstance(ed, datetime):
        date_s = ed.strftime("%Y-%m-%d")
    else:
        date_s = str(ed)[:10]
    return (
        str(doc.get("uid") or ""),
        date_s,
        str(doc.get("start_time") or ""),
    )


def write_json(path: str, rows: list[dict[str, Any]], *, dry_run: bool) -> None:
    if dry_run:
        print(f"  [dry-run] 将写入 {len(rows)} 条 → {path}")
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"  已写入 {len(rows)} 条 → {path}")


def fetch_rows(
    collection,
    dates: list[str],
    uids: list[str] | None,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = build_event_date_filter(dates)
    if uids:
        query["uid"] = {"$in": uids}
    cursor = collection.find(query)
    rows = [normalize_doc(doc) for doc in cursor]
    rows.sort(key=sort_key)
    return rows


def group_by_date(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for doc in rows:
        ed = doc.get("event_date")
        if isinstance(ed, datetime):
            key = ed.strftime("%Y-%m-%d")
        else:
            key = str(ed)[:10]
        grouped.setdefault(key, []).append(doc)
    return grouped


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dates",
        default=",".join(DEFAULT_DATES),
        help=f"逗号分隔日期，默认 {','.join(DEFAULT_DATES)}",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出目录，默认 {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--merge-one",
        action="store_true",
        help="除按日拆分外，额外写一份合并文件 somni_schedules_{dates}.json",
    )
    parser.add_argument(
        "--per-uid",
        action="store_true",
        help="按 uid 各写一份 {uid}_calendar_events_{date_range}.json",
    )
    parser.add_argument(
        "--all-uids",
        action="store_true",
        help="不过滤 uid，拉取集合内所有用户对应日期数据",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    args = parser.parse_args()

    try:
        dates = parse_dates(args.dates)
        uri, db_name = resolve_mongo_uri()
    except (ValueError, RuntimeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    uids: list[str] | None = None if args.all_uids else load_persona_uids()
    out_dir = os.path.abspath(args.output_dir)
    date_slug = "_".join(dates)

    print(f"集合: {COLLECTION_NAME}  数据库: {db_name}")
    print(f"日期: {', '.join(dates)}  uid 过滤: {'关闭' if args.all_uids else len(uids or [])} 人")
    print(f"输出目录: {out_dir}")

    client = MongoClient(uri, serverSelectionTimeoutMS=15000)
    try:
        client.admin.command("ping")
        collection = client[db_name][COLLECTION_NAME]
        rows = fetch_rows(collection, dates, uids)
    except Exception as exc:
        print(f"MongoDB 失败: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()

    print(f"\n共拉取 {len(rows)} 条")
    by_date = group_by_date(rows)
    for d in dates:
        print(f"  {d}: {len(by_date.get(d, []))} 条")

    if args.dry_run:
        print("\n[dry-run] 未写入文件")
        return 0

    for d in dates:
        day_rows = by_date.get(d, [])
        day_path = os.path.join(out_dir, f"somni_schedules_{d}.json")
        write_json(day_path, day_rows, dry_run=False)

    if args.merge_one or len(dates) > 1:
        merge_path = os.path.join(out_dir, f"somni_schedules_{date_slug}.json")
        write_json(merge_path, rows, dry_run=False)

    if args.per_uid and uids:
        by_uid: dict[str, list[dict[str, Any]]] = {}
        for doc in rows:
            uid = str(doc.get("uid") or "")
            by_uid.setdefault(uid, []).append(doc)
        for uid in uids:
            uid_rows = by_uid.get(uid, [])
            uid_path = os.path.join(
                out_dir, f"{uid}_calendar_events_{date_slug}.json"
            )
            write_json(uid_path, uid_rows, dry_run=False)

    print("\n完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
