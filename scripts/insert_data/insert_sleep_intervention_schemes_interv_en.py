#!/usr/bin/env python3
"""
将英文「干预专用」睡眠方案 JSON 写入 MongoDB 集合 somni_temp_plans。

- 默认数据文件：output/sleep_intervention_schemes_interv_en.json。
- 连接：项目根 .env 中 MONGODB_URI（或 MONGO_URI）；不设代码内默认密钥。
- 按 mhr_codes + language + type upsert，避免覆盖中文或其它 type 方案。
- create_time、update_time 从 ISO 字符串转为 BSON Date（UTC naive datetime）。

用法（在项目根目录）:
  python scripts/insert_data/insert_sleep_intervention_schemes_interv_en.py
  python scripts/insert_data/insert_sleep_intervention_schemes_interv_en.py --file output/sleep_intervention_schemes_interv_en.json
  python scripts/insert_data/insert_sleep_intervention_schemes_interv_en.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_JSON = PROJECT_ROOT / "output" / "sleep_intervention_schemes_interv_en.json"
DEFAULT_COLLECTION = "somni_temp_plans"
DEFAULT_LANGUAGE = "en"


def _extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


def _to_mongo_date(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, str) and value.strip():
        s = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    return None


def _normalize_record(doc: dict) -> dict:
    out = dict(doc)
    for key in ("create_time", "update_time"):
        if key not in out:
            continue
        parsed = _to_mongo_date(out[key])
        if parsed is None:
            raise ValueError(f"无法将 {key!r} 转为日期: {out[key]!r}")
        out[key] = parsed
    return out


def _upsert_filter(rec: dict) -> dict:
    filt: dict = {
        "mhr_codes": rec["mhr_codes"],
        "language": rec.get("language") or DEFAULT_LANGUAGE,
    }
    doc_type = rec.get("type")
    if doc_type:
        filt["type"] = doc_type
    return filt


def load_records(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [_normalize_record(x) for x in raw]
    return [_normalize_record(raw)]


def main() -> None:
    os.chdir(PROJECT_ROOT)
    load_dotenv(PROJECT_ROOT / ".env")

    ap = argparse.ArgumentParser(description="插入英文干预版睡眠方案（interv）到 MongoDB")
    ap.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_JSON,
        help=f"JSON 路径（默认 {DEFAULT_JSON.relative_to(PROJECT_ROOT)}）",
    )
    ap.add_argument(
        "--collection",
        default=os.getenv("SLEEP_INTERVENTION_COLLECTION", DEFAULT_COLLECTION),
        help=f"集合名（默认 {DEFAULT_COLLECTION}）",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只解析文件并打印条数，不写库",
    )
    ns = ap.parse_args()

    uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
    if not uri:
        print("请在 .env 中配置 MONGODB_URI（或 MONGO_URI）", file=sys.stderr)
        sys.exit(1)

    db_name = os.getenv("MONGODB_DB") or _extract_db_name_from_uri(uri)
    path = ns.file if ns.file.is_absolute() else PROJECT_ROOT / ns.file
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        sys.exit(1)

    records = load_records(path)
    for i, rec in enumerate(records):
        codes = rec.get("mhr_codes")
        if not isinstance(codes, list) or len(codes) != 4:
            print(f"第 {i} 条缺少有效 mhr_codes: {codes!r}", file=sys.stderr)
            sys.exit(1)
        lang = rec.get("language")
        if lang and lang != DEFAULT_LANGUAGE:
            print(
                f"第 {i} 条 language={lang!r}，本脚本默认处理 {DEFAULT_LANGUAGE!r}",
                file=sys.stderr,
            )
            sys.exit(1)

    if ns.dry_run:
        print(f"[dry-run] 将写入 {len(records)} 条 → {db_name}.{ns.collection}")
        return

    client = MongoClient(uri)
    try:
        db = client[db_name]
        if ns.collection not in db.list_collection_names():
            print(f"集合不存在，跳过: {ns.collection}", file=sys.stderr)
            sys.exit(2)

        coll = db[ns.collection]
        ops = [
            UpdateOne(_upsert_filter(rec), {"$set": rec}, upsert=True)
            for rec in records
        ]
        result = coll.bulk_write(ops, ordered=False)
        print(
            f"{db_name}.{ns.collection}: "
            f"matched={result.matched_count}, modified={result.modified_count}, "
            f"upserted={len(result.upserted_ids)}（共 {len(records)} 条）"
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
