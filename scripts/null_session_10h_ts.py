#!/usr/bin/env python3
"""从 session_10h 环境/体征 JSON 中每条记录删除 ts 字段。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILES = (
    REPO_ROOT / "output" / "session_10h_environment.json",
    REPO_ROOT / "output" / "session_10h_vitals.json",
)


def remove_ts_in_file(path: Path, *, dry_run: bool) -> int:
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")

    with path.open(encoding="utf-8") as f:
        records = json.load(f)

    if not isinstance(records, list):
        raise ValueError(f"期望顶层为数组: {path}")

    updated = 0
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(f"期望数组元素为对象: {path}")
        if "ts" in record:
            del record["ts"]
            updated += 1

    if dry_run:
        print(f"[dry-run] {path}: 将删除 {updated} 条记录的 ts 字段")
        return updated

    with path.open("w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"{path}: 已删除 {updated} 条记录的 ts 字段")
    return updated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从指定 JSON 文件中删除每条记录的 ts 字段（默认处理 10h session 两个输出文件）。"
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        default=list(DEFAULT_FILES),
        help="待处理的 JSON 文件路径（默认: output/session_10h_environment.json 与 vitals）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅统计将更新的条数，不写回文件",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    total = 0
    for path in args.files:
        total += remove_ts_in_file(path.resolve(), dry_run=args.dry_run)
    print(f"合计: {total} 条")


if __name__ == "__main__":
    main()
