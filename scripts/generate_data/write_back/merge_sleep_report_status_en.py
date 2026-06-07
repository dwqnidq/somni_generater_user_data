#!/usr/bin/env python3
"""sleep_report_en：睡眠结构与环境 summary 的 status 中文→英文单词（映射表）。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from _common import (  # noqa: E402
    default_output_dir,
    ensure_sys_path,
    load_json_list,
    record_date_in_range,
)
from _en_status_maps import (  # noqa: E402
    ENVIRONMENT_SUMMARY_DIM_KEYS,
    SLEEP_STRUCTURE_STAGE_KEYS,
    SUFFIX_SLEEP_REPORT_EN,
    map_status_to_en,
)

ensure_sys_path()
from utils import atomic_write_json  # noqa: E402


def _patch_status_block(block: dict[str, Any]) -> int:
    if not isinstance(block.get("status"), str):
        return 0
    mapped, changed = map_status_to_en(block["status"])
    if changed:
        block["status"] = mapped
        return 1
    return 0


def _patch_row_status_fields(row: dict[str, Any]) -> int:
    n = 0
    qa = row.get("quality_analysis")
    if isinstance(qa, dict):
        structure = qa.get("sleep_structure")
        if isinstance(structure, dict):
            for key in SLEEP_STRUCTURE_STAGE_KEYS:
                stage = structure.get(key)
                if isinstance(stage, dict):
                    n += _patch_status_block(stage)
    pain = row.get("pain_point_analysis")
    if isinstance(pain, dict):
        env = pain.get("environment_summary")
        if isinstance(env, dict):
            for key in ENVIRONMENT_SUMMARY_DIM_KEYS:
                dim = env.get(key)
                if isinstance(dim, dict):
                    n += _patch_status_block(dim)
    return n


def merge_sleep_report_status_en(
    uid: str,
    output_dir: str,
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    dry_run: bool = False,
) -> tuple[int, int]:
    """返回 (patched_status_count, report_days_unchanged)。"""
    output_dir = os.path.abspath(output_dir)
    rep_path = os.path.join(output_dir, f"{uid}{SUFFIX_SLEEP_REPORT_EN}")
    report_rows = load_json_list(rep_path)
    if not report_rows:
        return 0, 0
    patched = 0
    unchanged_days = 0
    changed = False
    for row in report_rows:
        if not isinstance(row, dict):
            continue
        rd = str(row.get("record_date") or "")
        if not rd or not record_date_in_range(rd, start_date, end_date):
            continue
        n = _patch_row_status_fields(row)
        if n:
            patched += n
            changed = True
        else:
            unchanged_days += 1
    if changed and not dry_run:
        atomic_write_json(rep_path, report_rows, ensure_ascii=False, indent=2)
    return patched, unchanged_days


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uid", required=True)
    p.add_argument("--output-dir", default=default_output_dir())
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    patched, unchanged = merge_sleep_report_status_en(
        args.uid.strip(),
        args.output_dir,
        start_date=args.start_date.strip() or None,
        end_date=args.end_date.strip() or None,
        dry_run=args.dry_run,
    )
    print(
        json.dumps(
            {
                "status_fields_patched": patched,
                "report_days_unchanged": unchanged,
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
