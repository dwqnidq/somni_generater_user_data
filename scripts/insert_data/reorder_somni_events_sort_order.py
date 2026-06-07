#!/usr/bin/env python3
"""重排 somni_events 的 sort_order（按 uid+record_date 分组）。

特性：
- 默认先备份将更新的文档，再执行更新
- 支持 --dry-run 仅预览不写库
- 仅更新 sort_order 字段，不删除文档
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from bson.json_util import dumps
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne


DEFAULT_COLLECTION = "somni_events"
DEFAULT_RECORDS_COLLECTION = "somni_records"
DEFAULT_DB_FALLBACK = "Fullive"
LOCAL_TZ_OFFSET_HOURS = 8


def resolve_mongo() -> tuple[MongoClient, str]:
    load_dotenv(".env")
    uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
    if not uri:
        raise RuntimeError("缺少 MONGODB_URI/MONGO_URI（请在 .env 配置）")
    db_name = (
        os.getenv("MONGODB_DB")
        or (urlparse(uri).path or "").lstrip("/").split("?")[0]
        or DEFAULT_DB_FALLBACK
    )
    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    return client, db_name


def build_group_pipeline(uid: str | None) -> list[dict]:
    match_query: dict = {
        "uid": {"$exists": True},
        "record_date": {"$exists": True},
        "sort_order": {"$exists": True},
    }
    if uid:
        match_query["uid"] = uid
    return [
        {"$match": match_query},
        {"$group": {"_id": {"uid": "$uid", "record_date": "$record_date"}}},
        {"$sort": {"_id.uid": 1, "_id.record_date": 1}},
    ]


def parse_utc_iso_to_local_dt(value: str) -> datetime | None:
    value = str(value or "").strip()
    if not value:
        return None
    try:
        utc_dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        return utc_dt + timedelta(hours=LOCAL_TZ_OFFSET_HOURS)
    except ValueError:
        pass
    # 兼容 Mongo 中常见格式：YYYY-MM-DD HH:MM:SS（按 UTC 存储）
    try:
        utc_dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return utc_dt + timedelta(hours=LOCAL_TZ_OFFSET_HOURS)
    except ValueError:
        pass
    # 兼容毫秒但无时区格式
    try:
        utc_dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f")
        return utc_dt + timedelta(hours=LOCAL_TZ_OFFSET_HOURS)
    except ValueError:
        return None


def sleep_local_window_bounds_from_record(sleep_data: dict) -> tuple[datetime | None, datetime | None]:
    raw = sleep_data.get("raw_data", {})
    sleep_time_str = str(raw.get("sleep_time") or "")
    wake_up_time_str = str(raw.get("wake_up_time") or "")
    wake_time_str = str(raw.get("wake_time") or "")
    if not sleep_time_str or (not wake_time_str and not wake_up_time_str):
        return None, None

    sleep_time = parse_utc_iso_to_local_dt(sleep_time_str)
    window_end = parse_utc_iso_to_local_dt(wake_time_str) or parse_utc_iso_to_local_dt(wake_up_time_str)
    if not sleep_time or not window_end:
        return None, None
    if window_end < sleep_time:
        window_end += timedelta(days=1)
    return sleep_time, window_end


def parse_sleep_event_timestamp_to_dt(event: dict) -> datetime:
    ts = str(event.get("event_timestamp") or "").strip()
    rd = str(event.get("record_date") or "")
    if len(ts) >= 16 and ts[4] == "-" and (" " in ts or "T" in ts):
        try:
            return datetime.strptime(ts.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    if len(ts) == 5 and ts[2] == ":":
        try:
            return datetime.strptime(f"{rd} {ts}", "%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return datetime.min


def session_anchor_event_local_dt(
    event: dict,
    sleep_start: datetime | None = None,
    window_end: datetime | None = None,
) -> datetime:
    ts = str(event.get("event_timestamp") or "").strip()
    rd = str(event.get("record_date") or "")
    if len(ts) >= 16 and ts[4] == "-" and (" " in ts or "T" in ts):
        try:
            return datetime.strptime(ts.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    if len(ts) != 5 or ts[2] != ":" or not rd:
        return parse_sleep_event_timestamp_to_dt(event)

    try:
        tpart = datetime.strptime(ts, "%H:%M").time()
        d0 = datetime.strptime(rd[:10], "%Y-%m-%d").date()
        c0 = datetime.combine(d0, tpart)
    except ValueError:
        return parse_sleep_event_timestamp_to_dt(event)

    if sleep_start is None or window_end is None:
        return c0

    candidates = [c0, c0 + timedelta(days=1), c0 - timedelta(days=1)]
    in_win = [c for c in candidates if sleep_start <= c <= window_end]
    if len(in_win) == 1:
        return in_win[0]
    if len(in_win) > 1:
        return min(in_win)
    return min(candidates, key=lambda c: abs((c - sleep_start).total_seconds()))


def get_sleep_window(records_col, uid: str, record_date: str, cache: dict) -> tuple[datetime | None, datetime | None]:
    key = f"{uid}|{record_date}"
    if key in cache:
        return cache[key]
    rec = records_col.find_one(
        {"uid": uid, "record_date": record_date},
        {"raw_data.sleep_time": 1, "raw_data.wake_time": 1, "raw_data.wake_up_time": 1},
    )
    if not rec:
        cache[key] = (None, None)
        return cache[key]
    cache[key] = sleep_local_window_bounds_from_record(rec)
    return cache[key]


def compute_updates(col, records_col, uid: str | None) -> tuple[list[UpdateOne], set, dict]:
    groups = list(col.aggregate(build_group_pipeline(uid), allowDiskUse=True))
    ops: list[UpdateOne] = []
    update_ids: set = set()
    sleep_window_cache: dict = {}
    stats = {
        "groups_total": len(groups),
        "groups_affected": 0,
        "docs_scanned": 0,
        "docs_to_update": 0,
    }

    for group in groups:
        g_uid = group["_id"]["uid"]
        g_date = group["_id"]["record_date"]
        docs = list(
            col.find(
                {"uid": g_uid, "record_date": g_date},
                {"record_date": 1, "event_timestamp": 1, "sort_order": 1},
            )
        )
        if not docs:
            continue

        stats["docs_scanned"] += len(docs)
        sleep_start, window_end = get_sleep_window(records_col, g_uid, g_date, sleep_window_cache)
        docs_sorted = sorted(
            docs,
            key=lambda d: (
                session_anchor_event_local_dt(d, sleep_start, window_end),
                str(d.get("_id")),
            ),
        )

        group_changed = 0
        for idx, doc in enumerate(docs_sorted):
            if doc.get("sort_order") == idx:
                continue
            doc_id = doc["_id"]
            update_ids.add(doc_id)
            ops.append(UpdateOne({"_id": doc_id}, {"$set": {"sort_order": idx}}))
            group_changed += 1

        if group_changed > 0:
            stats["groups_affected"] += 1

    stats["docs_to_update"] = len(ops)
    return ops, update_ids, stats


def backup_docs(col, update_ids: set, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"somni_events_sort_order_before_{ts}.jsonl"
    ids = list(update_ids)
    batch_size = 1000
    backup_count = 0

    with backup_path.open("w", encoding="utf-8") as f:
        for i in range(0, len(ids), batch_size):
            chunk = ids[i : i + batch_size]
            cursor = col.find({"_id": {"$in": chunk}})
            for doc in cursor:
                f.write(dumps(doc, ensure_ascii=False) + "\n")
                backup_count += 1

    print(f"[BACKUP] file={backup_path} docs={backup_count}")
    return backup_path


def verify_duplicates(col, uid: str | None) -> tuple[int, int]:
    match_query: dict = {}
    if uid:
        match_query["uid"] = uid
    pipeline = []
    if match_query:
        pipeline.append({"$match": match_query})
    pipeline.extend(
        [
            {
                "$group": {
                    "_id": {
                        "uid": "$uid",
                        "record_date": "$record_date",
                        "sort_order": "$sort_order",
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$match": {"count": {"$gt": 1}}},
            {
                "$group": {
                    "_id": {"uid": "$_id.uid", "record_date": "$_id.record_date"},
                    "extra_rows": {"$sum": {"$subtract": ["$count", 1]}},
                }
            },
        ]
    )
    rows = list(col.aggregate(pipeline, allowDiskUse=True))
    dup_days = len(rows)
    dup_extra_rows = sum(r.get("extra_rows", 0) for r in rows)
    return dup_days, dup_extra_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="重排 somni_events.sort_order（按日期递增）")
    parser.add_argument("--uid", default=None, help="仅处理指定 uid")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="集合名，默认 somni_events")
    parser.add_argument(
        "--records-collection",
        default=DEFAULT_RECORDS_COLLECTION,
        help="睡眠记录集合名（用于跨天锚定），默认 somni_records",
    )
    parser.add_argument(
        "--backup-dir",
        default="backup/mongo_sort_order_backup",
        help="备份目录（默认 backup/mongo_sort_order_backup）",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写库")
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="跳过备份（不建议）",
    )
    args = parser.parse_args()

    client, db_name = resolve_mongo()
    col = client[db_name][args.collection]
    records_col = client[db_name][args.records_collection]

    ops, update_ids, stats = compute_updates(col, records_col, args.uid)
    print(
        "[DRY-RUN] "
        f"groups_total={stats['groups_total']} "
        f"groups_affected={stats['groups_affected']} "
        f"docs_scanned={stats['docs_scanned']} "
        f"docs_to_update={stats['docs_to_update']}"
    )

    if not ops:
        print("无需更新，结束。")
        return 0
    if args.dry_run:
        print("已开启 --dry-run，未写库。")
        return 0

    if args.no_backup:
        print("[WARN] 已跳过备份（--no-backup）")
    else:
        backup_docs(col, update_ids, Path(args.backup_dir))

    result = col.bulk_write(ops, ordered=False)
    print(f"[UPDATE] matched={result.matched_count} modified={result.modified_count}")

    dup_days, dup_extra_rows = verify_duplicates(col, args.uid)
    print(f"[VERIFY] duplicate_day_groups={dup_days} duplicate_extra_rows={dup_extra_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
