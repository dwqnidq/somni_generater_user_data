#!/usr/bin/env python3
"""sleep_pattern_commonality_en + insight_en → sleep_report_en.hidden_discovery（整对象替换）。"""

from __future__ import annotations

import argparse
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from _common import (  # noqa: E402
    default_output_dir,
    ensure_sys_path,
    index_by_record_date,
    load_json_list,
    record_date_in_range,
)
from _en_status_maps import (  # noqa: E402
    SUFFIX_SLEEP_PATTERN_COMMONALITY_EN,
    SUFFIX_SLEEP_PATTERN_COMMONALITY_INSIGHT_EN,
    SUFFIX_SLEEP_REPORT_EN,
)
from merge_hidden_discovery import _build_hidden_discovery  # noqa: E402

ensure_sys_path()
from utils import atomic_write_json  # noqa: E402


def merge_hidden_discovery_en_to_sleep_report(
    uid: str,
    output_dir: str,
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    dry_run: bool = False,
) -> tuple[int, int]:
    output_dir = os.path.abspath(output_dir)
    rep_path = os.path.join(output_dir, f"{uid}{SUFFIX_SLEEP_REPORT_EN}")
    com_path = os.path.join(output_dir, f"{uid}{SUFFIX_SLEEP_PATTERN_COMMONALITY_EN}")
    ins_path = os.path.join(output_dir, f"{uid}{SUFFIX_SLEEP_PATTERN_COMMONALITY_INSIGHT_EN}")
    report_rows = load_json_list(rep_path)
    if not report_rows:
        return 0, 0
    com_by = index_by_record_date(load_json_list(com_path))
    ins_by = index_by_record_date(load_json_list(ins_path))
    written = 0
    skipped = 0
    changed = False
    for row in report_rows:
        if not isinstance(row, dict):
            continue
        rd = str(row.get("record_date") or "")
        if not rd or not record_date_in_range(rd, start_date, end_date):
            continue
        c = com_by.get(rd)
        ins = ins_by.get(rd)
        if not c or not ins:
            skipped += 1
            continue
        hd = _build_hidden_discovery(c, ins)
        if hd is None:
            skipped += 1
            continue
        qa = row.get("quality_analysis")
        if not isinstance(qa, dict):
            qa = {}
            row["quality_analysis"] = qa
        qa["hidden_discovery"] = hd
        written += 1
        changed = True
    if changed and not dry_run:
        atomic_write_json(rep_path, report_rows, ensure_ascii=False, indent=2)
    return written, skipped


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uid", required=True)
    p.add_argument("--output-dir", default=default_output_dir())
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    w, s = merge_hidden_discovery_en_to_sleep_report(
        args.uid.strip(),
        args.output_dir,
        start_date=args.start_date.strip() or None,
        end_date=args.end_date.strip() or None,
        dry_run=args.dry_run,
    )
    print(
        json.dumps(
            {"hidden_discovery_written_days": w, "hidden_discovery_skipped_days": s, "dry_run": args.dry_run},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
