#!/usr/bin/env python3
"""将八人格 Mongo 文档的日历日期字段月份统一改为 6 月（方案 B：仅改月份，保留年与日）。

只更新各集合括号内字段，不修改 start_time、create_time、raw_data 内时间等。

集合与字段：
  somni_schedules → event_date
  somni_records → record_date
  somni_reports → record_date
  somni_sleep_analysis → stats_date
  somni_sleep_district → stats_date（无 uid，按 stats_date 全表匹配）
  somni_physiological_data → record_date
  somni_environment_data → record_date
  somni_events → record_date
  somni_fusion → event_date
  somni_dream_universe_assets → record_date
  somni_ai_insights → record_date

用法（项目根目录）:
  .venv/bin/python scripts/insert_data/shift_persona_dates_to_june.py
  .venv/bin/python scripts/insert_data/shift_persona_dates_to_june.py --apply
  .venv/bin/python scripts/insert_data/shift_persona_dates_to_june.py --uid 69aea593af5e6cbf08027964 --apply
"""

from __future__ import annotations

import argparse
import calendar
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

os.chdir(PROJECT_ROOT)

from mongo_persona_output_specs import load_persona_uids, resolve_mongo_uri  # noqa: E402

TARGET_MONTH = 6

# 存在唯一索引的业务键；改后与库内其它文档冲突时删除本条（典型 5/31→6/30 且 5/30 已在 6/30）
UNIQUE_KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "somni_records": ("uid", "record_date"),
    "somni_reports": ("uid", "record_date", "language"),
    "somni_fusion": ("uid", "event_date"),
    "somni_ai_insights": ("uid", "record_date"),
    "somni_sleep_analysis": ("uid", "stats_date", "region.district_code"),
    "somni_sleep_district": ("stats_date", "region.district_code"),
}


@dataclass(frozen=True)
class CollectionDateSpec:
    collection: str
    date_field: str
    uid_field: str | None = "uid"


@dataclass
class _PendingChange:
    doc_id: ObjectId
    doc: dict[str, Any]
    old_val: Any
    new_val: Any


COLLECTION_SPECS: tuple[CollectionDateSpec, ...] = (
    CollectionDateSpec("somni_schedules", "event_date"),
    CollectionDateSpec("somni_records", "record_date"),
    CollectionDateSpec("somni_reports", "record_date"),
    CollectionDateSpec("somni_sleep_analysis", "stats_date"),
    CollectionDateSpec("somni_sleep_district", "stats_date", uid_field=None),
    CollectionDateSpec("somni_physiological_data", "record_date"),
    CollectionDateSpec("somni_environment_data", "record_date"),
    CollectionDateSpec("somni_events", "record_date"),
    CollectionDateSpec("somni_fusion", "event_date"),
    CollectionDateSpec("somni_dream_universe_assets", "record_date"),
    CollectionDateSpec("somni_ai_insights", "record_date"),
)


def _last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _parse_calendar_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.date()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    head = text[:10]
    try:
        return datetime.strptime(head, "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_like_original(old_value: Any, new_cal: date) -> Any:
    if isinstance(old_value, datetime):
        dt = old_value
        if dt.tzinfo is None:
            return datetime(
                new_cal.year,
                new_cal.month,
                new_cal.day,
                dt.hour,
                dt.minute,
                dt.second,
                dt.microsecond,
            )
        utc = dt.astimezone(timezone.utc)
        return datetime(
            new_cal.year,
            new_cal.month,
            new_cal.day,
            utc.hour,
            utc.minute,
            utc.second,
            utc.microsecond,
            tzinfo=timezone.utc,
        )
    if isinstance(old_value, date) and not isinstance(old_value, datetime):
        return new_cal
    text = str(old_value).strip()
    if len(text) > 10 and "T" in text[:20]:
        return f"{new_cal.isoformat()}{text[10:]}"
    return new_cal.isoformat()


def shift_month_to_target(old_value: Any, target_month: int = TARGET_MONTH) -> tuple[Any, bool]:
    """方案 B：将日历日的月份改为 target_month，保留年与日（日超出目标月则压到月末）。"""
    cal = _parse_calendar_date(old_value)
    if cal is None:
        return old_value, False
    if cal.month == target_month:
        return old_value, False
    day = min(cal.day, _last_day_of_month(cal.year, target_month))
    new_cal = date(cal.year, target_month, day)
    return _format_like_original(old_value, new_cal), True


def _nested_get(doc: dict[str, Any], path: str) -> Any:
    cur: Any = doc
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _unique_key_filter(
    spec: CollectionDateSpec,
    doc: dict[str, Any],
    new_date_value: Any,
) -> dict[str, Any] | None:
    fields = UNIQUE_KEY_FIELDS.get(spec.collection)
    if not fields:
        return None
    flt: dict[str, Any] = {}
    for field in fields:
        if field == spec.date_field:
            flt[field] = new_date_value
        elif "." in field:
            flt[field] = _nested_get(doc, field)
        else:
            flt[field] = doc.get(field)
    return flt


def _existing_doc_with_same_key(
    col: Collection,
    spec: CollectionDateSpec,
    doc: dict[str, Any],
    new_date_value: Any,
) -> ObjectId | None:
    flt = _unique_key_filter(spec, doc, new_date_value)
    if not flt:
        return None
    flt["_id"] = {"$ne": doc["_id"]}
    other = col.find_one(flt, {"_id": 1})
    if other:
        return other["_id"]
    return None


def _unique_key_tuple(
    spec: CollectionDateSpec,
    doc: dict[str, Any],
    new_date_value: Any,
) -> tuple[Any, ...] | None:
    flt = _unique_key_filter(spec, doc, new_date_value)
    if not flt:
        return None
    return tuple(sorted(flt.items()))


def _partition_changes(
    col: Collection,
    spec: CollectionDateSpec,
    changes: list[_PendingChange],
) -> tuple[list[_PendingChange], list[ObjectId]]:
    """同批或库内唯一键冲突时，保留源日期最早的一条，其余删除。"""
    pending: list[_PendingChange] = []
    to_delete: list[ObjectId] = []

    by_key: dict[tuple[Any, ...], list[_PendingChange]] = defaultdict(list)
    for item in changes:
        key = _unique_key_tuple(spec, item.doc, item.new_val)
        if key is None:
            pending.append(item)
        else:
            by_key[key].append(item)

    for group in by_key.values():
        group.sort(
            key=lambda c: _parse_calendar_date(c.old_val) or date(9999, 12, 31)
        )
        winner = group[0]
        for loser in group[1:]:
            to_delete.append(loser.doc_id)
        other_id = _existing_doc_with_same_key(col, spec, winner.doc, winner.new_val)
        if other_id is not None:
            to_delete.append(winner.doc_id)
        else:
            pending.append(winner)
    return pending, to_delete


def _build_query(spec: CollectionDateSpec, uids: list[str]) -> dict[str, Any]:
    if spec.uid_field:
        return {spec.uid_field: {"$in": uids}}
    return {}


def process_collection(
    col: Collection,
    spec: CollectionDateSpec,
    uids: list[str],
    *,
    apply: bool,
    sample_limit: int,
) -> tuple[int, int, int]:
    """返回 (扫描条数, 将更新条数, 因唯一键冲突将删除条数)。"""
    query = _build_query(spec, uids)
    projection: dict[str, int] = {"_id": 1, spec.date_field: 1}
    if spec.uid_field:
        projection[spec.uid_field] = 1
    for field in UNIQUE_KEY_FIELDS.get(spec.collection, ()):
        top = field.split(".", 1)[0]
        if top not in projection:
            projection[top] = 1

    scanned = 0
    raw_changes: list[_PendingChange] = []

    for doc in col.find(query, projection):
        scanned += 1
        old_val = doc.get(spec.date_field)
        new_val, changed = shift_month_to_target(old_val)
        if not changed:
            continue
        raw_changes.append(
            _PendingChange(doc["_id"], doc, old_val, new_val)
        )

    pending_changes, to_delete = _partition_changes(col, spec, raw_changes)
    pending = [(c.doc_id, c.new_val, c.old_val) for c in pending_changes]
    overflow_deletes = len(to_delete)
    for i, doc_id in enumerate(to_delete[:3]):
        ch = next(c for c in raw_changes if c.doc_id == doc_id)
        print(
            f"  [唯一键冲突→删除] {ch.old_val!r}→{ch.new_val!r} _id={doc_id}"
        )
    print(f"\n=== {spec.collection}.{spec.date_field} ===")
    print(
        f"  扫描: {scanned}  待更新: {len(pending)}  "
        f"冲突删除: {overflow_deletes}"
    )
    if overflow_deletes > 3:
        print(f"  … 另有 {overflow_deletes - 3} 条冲突删除未列出")
    for i, (doc_id, new_val, old_val) in enumerate(pending[:sample_limit]):
        print(f"  样例 {i + 1}: {old_val!r} → {new_val!r}  (_id={doc_id})")
    if len(pending) > sample_limit:
        print(f"  … 另有 {len(pending) - sample_limit} 条未列出")

    if not apply or (not pending and not to_delete):
        if not apply and (pending or to_delete):
            print("  [dry-run] 未写入")
        return scanned, len(pending), overflow_deletes

    deleted = 0
    if to_delete:
        result_del = col.delete_many({"_id": {"$in": to_delete}})
        deleted = result_del.deleted_count
        print(f"  [apply] 删除冲突文档: {deleted}")

    if pending:
        ops = [
            UpdateOne({"_id": doc_id}, {"$set": {spec.date_field: new_val}})
            for doc_id, new_val, _ in pending
        ]
        result = col.bulk_write(ops, ordered=False)
        print(
            f"  [apply] 更新 matched={result.matched_count} "
            f"modified={result.modified_count}"
        )
    return scanned, len(pending), overflow_deletes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="八人格 Mongo 日期字段月份改为 6 月（仅改括号字段）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="写入 MongoDB（默认仅 dry-run）",
    )
    parser.add_argument(
        "--uid",
        default="",
        help="仅处理指定 uid（须在八人格配置中）",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=5,
        help="每集合最多打印几条变更样例",
    )
    parser.add_argument(
        "--collections",
        default="",
        help="逗号分隔集合名，默认全部",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

    try:
        uri, db_name = resolve_mongo_uri()
    except RuntimeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    uids = load_persona_uids()
    if args.uid.strip():
        want = args.uid.strip()
        if want not in uids:
            print(f"错误: 配置中无 uid={want}", file=sys.stderr)
            return 2
        uids = [want]

    specs = list(COLLECTION_SPECS)
    if args.collections.strip():
        names = {s.strip() for s in args.collections.split(",") if s.strip()}
        specs = [s for s in specs if s.collection in names]
        unknown = names - {s.collection for s in specs}
        if unknown:
            print(f"错误: 未知集合 {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2

    mode = "apply" if args.apply else "dry-run"
    print(f"模式: {mode}  库: {db_name}  人格数: {len(uids)}  目标月: {TARGET_MONTH}")
    print(f"UID: {', '.join(uids)}")

    client = MongoClient(uri, serverSelectionTimeoutMS=15000)
    try:
        client.admin.command("ping")
        db = client[db_name]
    except Exception as exc:
        print(f"MongoDB 连接失败: {exc}", file=sys.stderr)
        return 2

    totals = defaultdict(int)
    for spec in specs:
        if spec.collection not in db.list_collection_names():
            print(f"\n[跳过] 集合不存在: {spec.collection}")
            continue
        scanned, pending, overflow_del = process_collection(
            db[spec.collection],
            spec,
            uids,
            apply=args.apply,
            sample_limit=max(0, args.sample),
        )
        totals["scanned"] += scanned
        totals["pending"] += pending
        totals["overflow_del"] += overflow_del

    client.close()
    print(
        f"\n合计 — 扫描 {totals['scanned']}  待更新 {totals['pending']}  "
        f"冲突删除 {totals['overflow_del']}"
        + ("  (已写入)" if args.apply else "  (dry-run，加 --apply 写入)")
    )
    if totals["overflow_del"] > 0:
        print(
            "提示: 6 月仅 30 天，5/31 与 5/30 同落 6/30 时，"
            "唯一索引集合会删除后写入的冲突条（通常为 5/31）。",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
