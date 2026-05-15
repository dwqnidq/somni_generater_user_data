#!/usr/bin/env python3
"""sleep_notice.notice 写回 sleep_report.notice。"""

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
    find_report_row,
    load_json_list,
    record_date_in_range,
)

ensure_sys_path()
from utils import atomic_write_json  # noqa: E402


def merge_notice_to_report(
    uid: str,
    output_dir: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[int, int]:
    output_dir = os.path.abspath(output_dir)
    n_path = os.path.join(output_dir, f"{uid}_sleep_notice.json")
    rep_path = os.path.join(output_dir, f"{uid}_sleep_report.json")
    n_rows = load_json_list(n_path)
    report_rows = load_json_list(rep_path)
    if not report_rows:
        return 0, len(n_rows)

    ok = 0
    miss = 0
    changed = False
    for nr in n_rows:
        if not isinstance(nr, dict):
            continue
        rd = str(nr.get("record_date") or "")
        if not rd or not record_date_in_range(rd, start_date, end_date):
            continue
        notice = nr.get("notice")
        if not isinstance(notice, dict) or not str(notice.get("content") or "").strip():
            miss += 1
            continue
        row = find_report_row(report_rows, rd)
        if not row:
            miss += 1
            continue
        row["notice"] = {
            "title": str(notice.get("title") or "Bio-OS 算法已进化"),
            "content": str(notice.get("content") or "").strip(),
        }
        ok += 1
        changed = True

    if changed:
        atomic_write_json(rep_path, report_rows, ensure_ascii=False, indent=2)
    return ok, miss


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uid", required=True)
    p.add_argument("--output-dir", default=default_output_dir())
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    args = p.parse_args()
    ok, miss = merge_notice_to_report(
        args.uid.strip(),
        args.output_dir,
        start_date=args.start_date.strip() or None,
        end_date=args.end_date.strip() or None,
    )
    print(json.dumps({"notice_updated": ok, "notice_skipped": miss}, ensure_ascii=False))


if __name__ == "__main__":
    main()
