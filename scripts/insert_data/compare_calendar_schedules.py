#!/usr/bin/env python3
"""比对 output/{uid}_calendar_events.json 与 Mongo somni_schedules（仅 6 个字段）。

比对字段：uid, event_date, event_type, start_time, end_time, duration_minutes
配对键（不含 event_name）：event_date, event_type, start_time, end_time, duration_minutes

用法：
  python scripts/insert_data/compare_calendar_schedules.py
  python scripts/insert_data/compare_calendar_schedules.py --uid 69aea6d8af5e6cbf08027966
  python scripts/insert_data/compare_calendar_schedules.py --data-dir output
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from typing import Any

from pymongo import MongoClient

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from mongo_persona_output_specs import (  # noqa: E402
    load_persona_uids,
    resolve_mongo_uri,
)

COMPARE_FIELDS = (
    "uid",
    "event_date",
    "event_type",
    "start_time",
    "end_time",
    "duration_minutes",
)


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


def extract_compare_tuple(doc: dict[str, Any], expected_uid: str) -> tuple[Any, ...]:
    uid = str(doc.get("uid") or expected_uid).strip()
    return (
        uid,
        normalize_event_date(doc.get("event_date")),
        str(doc.get("event_type") or "").strip(),
        normalize_clock_time(doc.get("start_time")),
        normalize_clock_time(doc.get("end_time")),
        normalize_duration_minutes(doc.get("duration_minutes")),
    )


def load_local_events(path: str, uid: str) -> list[tuple[Any, ...]]:
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        raise ValueError(f"期望 JSON 数组: {path}")
    return [extract_compare_tuple(row, uid) for row in rows]


def fetch_mongo_events(collection, uid: str) -> list[tuple[Any, ...]]:
    cursor = collection.find({"uid": uid})
    return [extract_compare_tuple(doc, uid) for doc in cursor]


def print_counter_diff(
    uid: str,
    label: str,
    diff: Counter,
    *,
    limit: int = 20,
) -> int:
    if not diff:
        return 0
    shown = 0
    for item, count in diff.most_common():
        fields = dict(zip(COMPARE_FIELDS, item))
        print(
            f"[异常] uid={uid} {label} x{count}: "
            + ", ".join(f"{k}={fields[k]!r}" for k in COMPARE_FIELDS)
        )
        shown += 1
        if shown >= limit:
            rest = sum(diff.values()) - sum(c for _, c in diff.most_common(limit))
            if rest > 0:
                print(f"[异常] uid={uid} {label}: 另有 {rest} 条未展示")
            break
    return sum(diff.values())


def compare_uid(
    uid: str,
    local_rows: list[tuple[Any, ...]],
    mongo_rows: list[tuple[Any, ...]],
) -> bool:
    local_counter = Counter(local_rows)
    mongo_counter = Counter(mongo_rows)
    ok = True

    if len(local_rows) != len(mongo_rows):
        print(
            f"[异常] uid={uid} 条数不一致: "
            f"本地={len(local_rows)} Mongo={len(mongo_rows)}"
        )
        ok = False

    only_local = local_counter - mongo_counter
    only_mongo = mongo_counter - local_counter

    if only_local:
        ok = False
        print_counter_diff(uid, "仅本地有", only_local)

    if only_mongo:
        ok = False
        print_counter_diff(uid, "仅 Mongo 有", only_mongo)

    if ok and local_counter == mongo_counter:
        print(f"[OK] uid={uid} 一致 ({len(local_rows)} 条)")
    return ok


def calendar_file_path(data_dir: str, uid: str) -> str:
    return os.path.join(data_dir, f"{uid}_calendar_events.json")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="比对 calendar_events 与 somni_schedules（6 字段）"
    )
    parser.add_argument("--uid", default="", help="仅比对指定 uid")
    parser.add_argument(
        "--data-dir",
        default=os.path.join(PROJECT_ROOT, "output"),
        help="含 {uid}_calendar_events.json 的目录，默认 output/",
    )
    args = parser.parse_args()

    uids = load_persona_uids()
    if args.uid.strip():
        uids = [u for u in uids if u == args.uid.strip()]
        if not uids:
            print(f"错误: 配置中无 uid {args.uid}", file=sys.stderr)
            return 2

    try:
        uri, db_name = resolve_mongo_uri()
        client = MongoClient(uri, serverSelectionTimeoutMS=15000)
        client.admin.command("ping")
        collection = client[db_name]["somni_schedules"]
    except Exception as exc:
        print(f"MongoDB 连接失败: {exc}", file=sys.stderr)
        return 2

    all_ok = True
    for uid in uids:
        path = calendar_file_path(args.data_dir, uid)
        if not os.path.isfile(path):
            print(f"[异常] uid={uid} 本地文件不存在: {path}")
            all_ok = False
            continue
        try:
            local_rows = load_local_events(path, uid)
            mongo_rows = fetch_mongo_events(collection, uid)
        except (OSError, ValueError, TypeError) as exc:
            print(f"[异常] uid={uid} 读取/解析失败: {exc}")
            all_ok = False
            continue
        if not compare_uid(uid, local_rows, mongo_rows):
            all_ok = False

    client.close()
    if all_ok:
        print("\n全部 8 用户一致")
        return 0
    print("\n存在不一致，见上方 [异常]")
    return 1


if __name__ == "__main__":
    sys.exit(main())
