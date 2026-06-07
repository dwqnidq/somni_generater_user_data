#!/usr/bin/env python3
"""将 output/{uid}_ai_analysis_14d.json 写入 MongoDB somni_ai_insights。

每条记录：先按 uid + record_date 删除旧文档，再插入新文档。
默认处理 config 中八人格，无需传 uid。

用法（项目根目录）:
  # 正式写库（八人格，无需 uid）
  python scripts/insert_data/insert_ai_analysis_14d_to_mongo.py
  # 试运行（同上，不写库）
  bash scripts/insert_data/try_insert_ai_analysis_14d.sh
  python scripts/insert_data/insert_ai_analysis_14d_to_mongo.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.collection import Collection

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "output"
TARGET_COLLECTION = "somni_ai_insights"
DATA_TYPE = "ai_analysis_14d"
FILE_SUFFIX = f"_{DATA_TYPE}.json"
UID_PATTERN = re.compile(r"^[a-f0-9]{24}$")
SERVER_SELECTION_TIMEOUT_MS = 8000

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import insert_somni_records as isr  # noqa: E402
from mongo_persona_output_specs import load_persona_uids  # noqa: E402


def _extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


def _validate_uid(uid: str) -> str:
    if not UID_PATTERN.match(uid):
        raise ValueError(f"非法 uid（须为 24 位十六进制）: {uid}")
    return uid


def _parse_uid_from_filename(path: Path) -> str:
    name = path.name
    if not name.endswith(FILE_SUFFIX):
        raise ValueError(f"文件名不符合约定: {name}")
    uid = name[: -len(FILE_SUFFIX)].strip()
    return _validate_uid(uid)


def _ensure_record_keys(record: dict, index: int) -> tuple[str, str]:
    uid = str(record.get("uid") or "").strip()
    record_date = str(record.get("record_date") or "").strip()
    if not uid:
        raise ValueError(f"第 {index + 1} 条记录缺少 uid")
    if not record_date:
        raise ValueError(f"第 {index + 1} 条记录缺少 record_date")
    _validate_uid(uid)
    return uid, record_date


def load_records(path: Path) -> list[dict]:
    file_uid = _parse_uid_from_filename(path)
    config = isr.aaa[DATA_TYPE]
    records = isr.process_data(str(path), config["isDate"])
    seen: set[tuple[str, str]] = set()
    for index, record in enumerate(records):
        uid, record_date = _ensure_record_keys(record, index)
        if uid != file_uid:
            raise ValueError(
                f"第 {index + 1} 条 uid={uid} 与文件名 uid={file_uid} 不一致"
            )
        key = (uid, record_date)
        if key in seen:
            raise ValueError(
                f"文件内重复的 uid+record_date: uid={uid}, record_date={record_date}"
            )
        seen.add(key)
        record["uid"] = uid
        record["record_date"] = record_date
    return records


def resolve_target_uids(uid_filter: str) -> list[str]:
    if uid_filter:
        return [_validate_uid(uid_filter)]
    uids = load_persona_uids()
    if not uids:
        raise RuntimeError("配置中未找到人格 uid（health_data_personas_config.json）")
    return uids


def collect_source_files(source_dir: Path, uids: list[str]) -> tuple[list[Path], list[str]]:
    if not source_dir.is_dir():
        raise FileNotFoundError(f"目录不存在: {source_dir}")
    paths: list[Path] = []
    missing: list[str] = []
    for uid in uids:
        path = source_dir / f"{uid}{FILE_SUFFIX}"
        if path.is_file():
            paths.append(path)
        else:
            missing.append(uid)
    return paths, missing


def resolve_env(cli_uri: str, cli_db: str) -> tuple[str, str]:
    load_dotenv(PROJECT_ROOT / ".env")
    uri = cli_uri or os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or ""
    if not uri:
        raise RuntimeError("请在 .env 中配置 MONGODB_URI（或 MONGO_URI），或传 --uri")
    db_name = cli_db or os.getenv("MONGODB_DB") or _extract_db_name_from_uri(uri)
    return uri, db_name


def sync_records(
    collection: Collection,
    records: list[dict],
    *,
    dry_run: bool,
) -> tuple[int, int]:
    """按 uid+record_date 先删后插，返回 (deleted_count, inserted_count)。"""
    deleted_total = 0
    inserted_total = 0
    for record in records:
        flt = {"uid": record["uid"], "record_date": record["record_date"]}
        if dry_run:
            would_delete = collection.count_documents(flt)
            deleted_total += would_delete
            inserted_total += 1
            continue
        delete_result = collection.delete_many(flt)
        deleted_total += int(delete_result.deleted_count)
        insert_result = collection.insert_one(record)
        if insert_result.inserted_id:
            inserted_total += 1
    return deleted_total, inserted_total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "将八人格 ai_analysis_14d 导入 somni_ai_insights（uid+record_date 先删后插）；"
            "无参数即写库"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不写库")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help="JSON 目录（默认 output/）",
    )
    parser.add_argument("--uid", default="", help="仅处理指定 uid（可选）")
    parser.add_argument("--uri", default="", help="MongoDB URI（默认读环境变量）")
    parser.add_argument("--db", default="", help="数据库名（默认从 URI 推断）")
    return parser.parse_args()


def main() -> int:
    os.chdir(PROJECT_ROOT)
    args = parse_args()
    uid_filter = args.uid.strip()
    if uid_filter:
        uid_filter = _validate_uid(uid_filter)

    source_dir = args.source_dir
    if not source_dir.is_absolute():
        source_dir = PROJECT_ROOT / source_dir

    try:
        target_uids = resolve_target_uids(uid_filter)
        source_files, missing_uids = collect_source_files(source_dir, target_uids)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    if missing_uids:
        print(f"跳过（无 JSON 文件）: {', '.join(missing_uids)}")

    if not source_files:
        print(f"未找到可导入的 *{FILE_SUFFIX} 文件: {source_dir}")
        return 1 if missing_uids and uid_filter else 0

    if not uid_filter:
        print(f"八人格待导入: {len(source_files)} 个文件（配置共 {len(target_uids)} 人）")

    file_records: list[tuple[Path, list[dict]]] = []
    total_records = 0
    for path in source_files:
        try:
            records = load_records(path)
        except (ValueError, KeyError) as exc:
            print(f"错误: {path.name}: {exc}", file=sys.stderr)
            return 1
        file_records.append((path, records))
        total_records += len(records)
        print(f"读取: {path.name}  记录数={len(records)}")

    print(f"文件数={len(file_records)}  总记录数={total_records}")
    dry_run = args.dry_run

    if dry_run:
        print("[dry-run] 将按 uid+record_date 统计删除并模拟插入（不写库）")

    try:
        uri, db_name = resolve_env(str(args.uri).strip(), str(args.db).strip())
    except RuntimeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    client = MongoClient(uri, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS)
    try:
        db = client[db_name]
        if TARGET_COLLECTION not in db.list_collection_names():
            print(f"错误: 集合 {TARGET_COLLECTION!r} 不存在", file=sys.stderr)
            return 1
        collection = db[TARGET_COLLECTION]
        print(f"目标: {db_name}.{TARGET_COLLECTION}")

        deleted_grand = 0
        inserted_grand = 0
        for path, records in file_records:
            if not records:
                print(f"  {path.name}: 无记录，跳过")
                continue
            deleted, inserted = sync_records(
                collection, records, dry_run=dry_run
            )
            deleted_grand += deleted
            inserted_grand += inserted
            action = "将删除" if dry_run else "已删除"
            print(
                f"  {path.name}: {action}={deleted} "
                f"{'将插入' if dry_run else '已插入'}={inserted}"
            )

        if dry_run:
            print(
                f"\n[dry-run] 合计将删除约 {deleted_grand} 条，将插入 {inserted_grand} 条。"
                "确认后请去掉 --dry-run 直接执行本脚本。"
            )
        else:
            print(f"\n完成: 删除 {deleted_grand} 条，插入 {inserted_grand} 条。")
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
