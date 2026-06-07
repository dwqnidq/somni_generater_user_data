#!/usr/bin/env python3
"""按 uid + 日期将 output_regenerated_auditory 的 auditory 回写到 Mongo somni_reports。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError

DEFAULT_MONGO_URI = "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive"
DEFAULT_COLLECTION = "somni_reports"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))


def _load_env_file_fallback(env_path: str) -> None:
    """在 python-dotenv 不可用时，最小化解析 .env 并写入进程环境。"""
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                key = k.strip()
                if not key or key in os.environ:
                    continue
                val = v.strip()
                if (val.startswith('"') and val.endswith('"')) or (
                    val.startswith("'") and val.endswith("'")
                ):
                    val = val[1:-1]
                os.environ[key] = val
    except OSError:
        return


def load_project_env() -> None:
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if load_dotenv is not None:
        load_dotenv(env_path)
    else:
        _load_env_file_fallback(env_path)


@dataclass
class AuditRow:
    uid: str
    record_date: str
    auditory: dict[str, Any]


def extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", required=True, help="目标 uid")
    parser.add_argument("--record-date", default="", help="仅更新单日 YYYY-MM-DD")
    parser.add_argument("--start-date", default="", help="开始日期 YYYY-MM-DD（可选）")
    parser.add_argument("--end-date", default="", help="结束日期 YYYY-MM-DD（可选）")
    parser.add_argument(
        "--source-dir",
        default=os.path.join(PROJECT_ROOT, "output_regenerated_auditory"),
        help="regenerated auditory 文件目录",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help=f"目标集合（默认 {DEFAULT_COLLECTION}）",
    )
    parser.add_argument("--uri", default="", help="Mongo URI（默认读环境变量）")
    parser.add_argument("--db", default="", help="数据库名（默认从 URI 推断）")
    parser.add_argument(
        "--language",
        default="",
        choices=["", "zh", "en"],
        help="可选：仅更新指定语言文档",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅打印将要更新的记录，不写库")
    return parser.parse_args()


def load_rows(source_dir: str, uid: str) -> list[dict]:
    path = os.path.join(source_dir, f"{uid}_sleep_auditory.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict)]


def to_audit_rows(rows: list[dict]) -> list[AuditRow]:
    out: list[AuditRow] = []
    for row in rows:
        uid = str(row.get("uid") or "").strip()
        record_date = str(row.get("record_date") or "").strip()
        auditory = row.get("auditory")
        if not uid or not record_date or not isinstance(auditory, dict):
            continue
        out.append(AuditRow(uid=uid, record_date=record_date, auditory=auditory))
    return out


def apply_date_filters(
    rows: list[AuditRow],
    *,
    record_date: str,
    start_date: str,
    end_date: str,
) -> list[AuditRow]:
    filtered = rows
    if record_date:
        filtered = [r for r in filtered if r.record_date == record_date]
    if start_date:
        filtered = [r for r in filtered if r.record_date >= start_date]
    if end_date:
        filtered = [r for r in filtered if r.record_date <= end_date]
    return sorted(filtered, key=lambda x: x.record_date)


def build_ops(rows: list[AuditRow], language: str) -> list[UpdateOne]:
    ops: list[UpdateOne] = []
    for row in rows:
        flt: dict[str, Any] = {"uid": row.uid, "record_date": row.record_date}
        if language:
            flt["language"] = language
        ops.append(UpdateOne(flt, {"$set": {"auditory": row.auditory}}, upsert=False))
    return ops


def main() -> int:
    load_project_env()
    args = parse_args()

    if args.record_date and (args.start_date or args.end_date):
        print("参数冲突：--record-date 与 --start-date/--end-date 不能同时传", file=sys.stderr)
        return 1

    source_dir = os.path.abspath(args.source_dir)
    uid = str(args.uid).strip()
    if not uid:
        print("uid 不能为空", file=sys.stderr)
        return 1

    try:
        raw_rows = load_rows(source_dir, uid)
    except FileNotFoundError as exc:
        print(f"源文件不存在: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"源文件 JSON 解析失败: {exc}", file=sys.stderr)
        return 1

    parsed_rows = to_audit_rows(raw_rows)
    picked_rows = apply_date_filters(
        parsed_rows,
        record_date=str(args.record_date).strip(),
        start_date=str(args.start_date).strip(),
        end_date=str(args.end_date).strip(),
    )

    if not picked_rows:
        print("没有符合条件的 auditory 数据可更新")
        return 0

    ops = build_ops(picked_rows, str(args.language).strip())
    print(f"准备更新 uid={uid} 共 {len(ops)} 天")
    print(f"日期范围: {picked_rows[0].record_date} ~ {picked_rows[-1].record_date}")
    if args.language:
        print(f"语言过滤: {args.language}")
    else:
        print("语言过滤: 无（将更新匹配 uid+record_date 的所有语言文档）")

    if args.dry_run:
        sample = picked_rows[0]
        print(
            f"dry-run 示例: uid={sample.uid}, date={sample.record_date}, "
            f"auditory_keys={list(sample.auditory.keys())}"
        )
        return 0

    mongo_uri = (
        args.uri
        or os.getenv("MONGODB_URI")
        or os.getenv("MONGO_URI")
        or DEFAULT_MONGO_URI
    )
    db_name = args.db or os.getenv("MONGODB_DB") or extract_db_name_from_uri(mongo_uri)

    client = MongoClient(mongo_uri)
    try:
        db = client[db_name]
        if args.collection not in db.list_collection_names():
            print(f"集合 {args.collection!r} 不存在", file=sys.stderr)
            return 1
        result = db[args.collection].bulk_write(ops, ordered=False)
        print(
            f"更新完成: matched={result.matched_count}, "
            f"modified={result.modified_count}, upserted={len(result.upserted_ids)}"
        )
    except BulkWriteError as exc:
        print(f"批量更新失败: {exc.details}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"更新失败: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
