#!/usr/bin/env python3
"""只读备份八人格 Mongo 数据到 backup/somni_all_data_YYYYMMDD_HHMM/。

本脚本对 MongoDB 仅使用 find / count_documents / list_collection_names / ping，
禁止 delete、update、insert、drop、bulk_write 等任何写库操作。
导出时以库内 count_documents 为准，find 条数不足会自动重试拉取。

映射与 insert_somni_records.aaa 一致（key = 文件后缀 = {uid}_{key}.json）。
sleep_report、ai_analysis_14d 仅导出 language=zh。

特殊文件：
  sleep_district.json         — somni_sleep_district 全表
  sleep_analysis_pool.json    — somni_sleep_analysis 非八人格 uid

用法（项目根目录）:
  .venv/bin/python scripts/backup/backup_somni_all_persona_data.py
  .venv/bin/python scripts/backup/backup_somni_all_persona_data.py --dry-run
  .venv/bin/python scripts/backup/backup_somni_all_persona_data.py --uid 69aea593af5e6cbf08027964
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

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

DEFAULT_BACKUP_PARENT = os.path.join(PROJECT_ROOT, "backup")
META_FILENAME = "_backup_meta.json"

# 与 insert_somni_records.aaa 对齐：data_type(key) → collection
AAA_COLLECTION_MAP: dict[str, str] = {
    "health_data": "somni_records",
    "calendar_events": "somni_schedules",
    "vitals_data": "somni_physiological_data",
    "daily_emotion_steps": "somni_fusion",
    "sleep_art": "somni_dream_universe_assets",
    "sleep_district": "somni_sleep_district",
    "sleep_analysis": "somni_sleep_analysis",
    "sleep_events": "somni_events",
    "sleep_report": "somni_reports",
    "environment_data": "somni_environment_data",
    "ai_analysis_14d": "somni_ai_insights",
}

SORT_KEY_BY_TYPE: dict[str, str] = {
    "health_data": "record_date",
    "environment_data": "record_date",
    "vitals_data": "record_date",
    "sleep_events": "record_date",
    "sleep_report": "record_date",
    "sleep_analysis": "stats_date",
    "sleep_art": "record_date",
    "daily_emotion_steps": "event_date",
    "calendar_events": "event_date",
    "ai_analysis_14d": "record_date",
    "sleep_district": "stats_date",
}

ZH_ONLY_TYPES = frozenset({"sleep_report", "ai_analysis_14d"})
PER_UID_TYPES = tuple(k for k in AAA_COLLECTION_MAP if k not in ("sleep_district",))
SLEEP_ANALYSIS_TYPE = "sleep_analysis"
SLEEP_ANALYSIS_POOL_FILE = "sleep_analysis_pool.json"
SLEEP_DISTRICT_TYPE = "sleep_district"
MAX_FETCH_RETRIES = 3


def _timestamp_dir_name() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M")


def _default_output_dir() -> str:
    return os.path.join(DEFAULT_BACKUP_PARENT, f"somni_all_data_{_timestamp_dir_name()}")


def _resolve_output_dir(raw: str | None) -> str:
    if raw:
        return raw if os.path.isabs(raw) else os.path.join(PROJECT_ROOT, raw)
    return _default_output_dir()


def _sort_rows(rows: list[dict[str, Any]], sort_key: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: str(row.get(sort_key) or row.get("_id") or ""))


def _write_json(path: str, rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def _build_uid_query(data_type: str, uid: str) -> dict[str, Any]:
    query: dict[str, Any] = {"uid": uid}
    if data_type in ZH_ONLY_TYPES:
        query["language"] = "zh"
    return query


def _pool_sleep_analysis_query(persona_uids: list[str]) -> dict[str, Any]:
    return {
        "$or": [
            {"uid": {"$nin": persona_uids}},
            {"uid": {"$exists": False}},
            {"uid": None},
            {"uid": ""},
        ]
    }


def _fetch_db_rows(
    collection,
    query: dict[str, Any],
    sort_key: str,
) -> tuple[list[dict[str, Any]], int]:
    """以库内 count_documents 为准拉取；find 条数不足时重试。"""
    for attempt in range(MAX_FETCH_RETRIES):
        expected = collection.count_documents(query)
        rows = _sort_rows(
            [normalize_doc(doc) for doc in collection.find(query)],
            sort_key,
        )
        if len(rows) == expected:
            return rows, expected
        if attempt < MAX_FETCH_RETRIES - 1:
            print(
                f"  [重试 {attempt + 2}/{MAX_FETCH_RETRIES}] "
                f"库内 {expected} 条, find 得 {len(rows)} 条, 重新拉取…"
            )

    expected = collection.count_documents(query)
    rows = _sort_rows(
        [normalize_doc(doc) for doc in collection.find(query)],
        sort_key,
    )
    if len(rows) != expected:
        print(
            f"  [警告] 库内 {expected} 条, find 仍得 {len(rows)} 条, 以本次 find 结果写入",
            file=sys.stderr,
        )
        return rows, expected
    return rows, expected


def _export_uid_file(
    collection,
    data_type: str,
    uid: str,
    output_dir: str,
    *,
    dry_run: bool,
) -> tuple[str, int]:
    filename = f"{uid}_{data_type}.json"
    query = _build_uid_query(data_type, uid)
    if dry_run:
        count = collection.count_documents(query)
        print(f"  [dry-run] {filename} → {count} 条")
        return filename, count

    rows, db_count = _fetch_db_rows(collection, query, SORT_KEY_BY_TYPE[data_type])
    out_path = os.path.join(output_dir, filename)
    _write_json(out_path, rows)
    print(f"  {filename} → {len(rows)} 条 (库内 {db_count})")
    return filename, len(rows)


def _export_full_table(
    collection,
    data_type: str,
    output_dir: str,
    *,
    dry_run: bool,
) -> tuple[str, int]:
    filename = f"{data_type}.json"
    if dry_run:
        count = collection.count_documents({})
        print(f"  [dry-run] {filename} → {count} 条")
        return filename, count

    rows, db_count = _fetch_db_rows(collection, {}, SORT_KEY_BY_TYPE[data_type])
    out_path = os.path.join(output_dir, filename)
    _write_json(out_path, rows)
    print(f"  {filename} → {len(rows)} 条 (库内 {db_count})")
    return filename, len(rows)


def _export_sleep_analysis_pool(
    collection,
    persona_uids: list[str],
    output_dir: str,
    *,
    dry_run: bool,
) -> tuple[str, int]:
    query = _pool_sleep_analysis_query(persona_uids)
    if dry_run:
        count = collection.count_documents(query)
        print(f"  [dry-run] {SLEEP_ANALYSIS_POOL_FILE} → {count} 条")
        return SLEEP_ANALYSIS_POOL_FILE, count

    rows, db_count = _fetch_db_rows(
        collection, query, SORT_KEY_BY_TYPE[SLEEP_ANALYSIS_TYPE]
    )
    out_path = os.path.join(output_dir, SLEEP_ANALYSIS_POOL_FILE)
    _write_json(out_path, rows)
    print(f"  {SLEEP_ANALYSIS_POOL_FILE} → {len(rows)} 条 (库内 {db_count})")
    return SLEEP_ANALYSIS_POOL_FILE, len(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="只读备份八人格 Mongo 11 集合到 backup/somni_all_data_YYYYMMDD_HHMM/",
    )
    parser.add_argument("--uid", default="", help="仅备份指定 uid（须在八人格配置中）")
    parser.add_argument("--output-dir", default="", help="输出目录")
    parser.add_argument("--dry-run", action="store_true", help="只统计条数，不写文件")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        uri, db_name = resolve_mongo_uri()
    except RuntimeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    all_persona_uids = load_persona_uids()
    target_uids = all_persona_uids
    if args.uid.strip():
        want = args.uid.strip()
        if want not in all_persona_uids:
            print(f"错误: 配置中无 uid={want}", file=sys.stderr)
            return 2
        target_uids = [want]

    output_dir = _resolve_output_dir(args.output_dir.strip() or None)
    mode = "dry-run" if args.dry_run else "write"

    print(f"数据库: {db_name}  模式: {mode}  人格数: {len(target_uids)}")
    print(f"输出: {output_dir}")
    print("约束: 只读查询，不修改 MongoDB")

    client = MongoClient(uri, serverSelectionTimeoutMS=15000)
    file_counts: dict[str, int] = {}

    try:
        client.admin.command("ping")
        db = client[db_name]
        if not args.dry_run:
            os.makedirs(output_dir, exist_ok=True)

        coll_exported: dict[str, int] = {}

        for data_type in PER_UID_TYPES:
            collection_name = AAA_COLLECTION_MAP[data_type]
            if collection_name not in db.list_collection_names():
                print(f"\n[跳过] 集合不存在: {collection_name}")
                continue

            collection = db[collection_name]
            print(f"\n=== {data_type} ({collection_name}) ===")
            type_total = 0

            for uid in target_uids:
                filename, count = _export_uid_file(
                    collection, data_type, uid, output_dir, dry_run=args.dry_run
                )
                file_counts[filename] = count
                type_total += count

            coll_exported[data_type] = type_total

        analysis_coll = AAA_COLLECTION_MAP[SLEEP_ANALYSIS_TYPE]
        if analysis_coll in db.list_collection_names():
            print(f"\n=== {SLEEP_ANALYSIS_POOL_FILE} ({analysis_coll}) ===")
            pool_name, pool_count = _export_sleep_analysis_pool(
                db[analysis_coll],
                all_persona_uids,
                output_dir,
                dry_run=args.dry_run,
            )
            file_counts[pool_name] = pool_count
        else:
            print(f"\n[跳过] 集合不存在: {analysis_coll}")

        district_coll = AAA_COLLECTION_MAP[SLEEP_DISTRICT_TYPE]
        if district_coll in db.list_collection_names():
            print(f"\n=== {SLEEP_DISTRICT_TYPE} ({district_coll}, 全表) ===")
            district_name, district_count = _export_full_table(
                db[district_coll],
                SLEEP_DISTRICT_TYPE,
                output_dir,
                dry_run=args.dry_run,
            )
            file_counts[district_name] = district_count
        else:
            print(f"\n[跳过] 集合不存在: {district_coll}")

    except Exception as exc:
        print(f"失败: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()

    if not args.dry_run:
        meta = {
            "backed_up_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "db": db_name,
            "persona_uids": all_persona_uids,
            "exported_uids": target_uids,
            "output_dir": output_dir,
            "read_only": True,
            "file_counts": file_counts,
            "collection_export_totals": coll_exported,
            "complete": True,
        }
        meta_path = os.path.join(output_dir, META_FILENAME)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(f"\n元数据 → {meta_path}")

    total_docs = sum(file_counts.values())
    suffix = " (dry-run)" if args.dry_run else ""
    print(f"\n完成: {len(file_counts)} 个文件, 共 {total_docs} 条{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
