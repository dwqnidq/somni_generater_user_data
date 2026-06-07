#!/usr/bin/env python3
"""将指定用户的 health_data JSON 插入 MongoDB somni_records 集合。

默认用户：69aea63eaf5e6cbf08027965
默认数据文件：output/{uid}_health_data.json

连接：项目根 .env 中 MONGODB_URI（或 MONGO_URI）；不设代码内默认密钥。

用法（在项目根目录）:
  python scripts/insert_data/insert_somni_records_by_uid.py --dry-run
  python scripts/insert_data/insert_somni_records_by_uid.py --apply
  python scripts/insert_data/insert_somni_records_by_uid.py --apply --uid 69aea63eaf5e6cbf08027965
"""

from __future__ import annotations

import argparse
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import insert_somni_records as isr  # noqa: E402

DEFAULT_UID = "69aea63eaf5e6cbf08027965"
COLLECTION_NAME = "somni_records"
DATA_TYPE = "health_data"
UID_PATTERN = re.compile(r"^[a-f0-9]{24}$")


def _validate_uid(uid: str) -> str:
    if not UID_PATTERN.match(uid):
        raise ValueError(f"非法 uid（须为 24 位十六进制）: {uid}")
    return uid


def _health_data_path(uid: str) -> str:
    file_name = f"{uid}_health_data.json"
    path = os.path.join(isr.OUTPUT_DIR, file_name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"未找到数据文件: {path}")
    return path


def _ensure_uid_on_records(records: list[dict], uid: str) -> None:
    for index, record in enumerate(records):
        record_uid = record.get("uid")
        if not record_uid:
            record["uid"] = uid
        elif record_uid != uid:
            raise ValueError(
                f"第 {index + 1} 条记录 uid={record_uid} 与目标 uid={uid} 不一致"
            )
        if not record.get("record_date"):
            raise ValueError(f"第 {index + 1} 条记录缺少 record_date")


def _load_records(uid: str) -> list[dict]:
    config = isr.aaa[DATA_TYPE]
    file_path = _health_data_path(uid)
    records = isr.process_data(file_path, config["isDate"])
    _ensure_uid_on_records(records, uid)
    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"将 output/{{uid}}_health_data.json 插入 {COLLECTION_NAME}",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="仅加载并校验数据，统计条数，不写入数据库",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="执行 insert_many 写入 somni_records（重复执行可能产生重复文档）",
    )
    parser.add_argument(
        "--uid",
        default=DEFAULT_UID,
        help=f"用户 ID（默认 {DEFAULT_UID}）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        uid = _validate_uid(args.uid.strip())
        records = _load_records(uid)
        file_path = _health_data_path(uid)

        print(f"数据文件: {file_path}")
        print(f"目标集合: {COLLECTION_NAME}")
        print(f"用户 uid: {uid}")
        print(f"记录条数: {len(records):,}")

        if args.dry_run:
            print("\n[dry-run] 未写入数据库。确认后请加 --apply 执行插入。")
            return 0

        if not records:
            print("\n无记录可插入。")
            return 0

        isr.insert_to_mongodb(records, COLLECTION_NAME, data_type=None)
        print("\n插入流程结束。")
        return 0
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
