#!/usr/bin/env python3
"""sleep_event_environment_intervention 的 pain_point_analysis_module[0] append 到 sleep_report.pain_point_analysis.module。"""

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


def merge_env_intervention_to_report(
    uid: str,
    output_dir: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[int, int]:
    output_dir = os.path.abspath(output_dir)
    e_path = os.path.join(output_dir, f"{uid}_sleep_event_environment_intervention.json")
    rep_path = os.path.join(output_dir, f"{uid}_sleep_report.json")
    e_rows = load_json_list(e_path)
    report_rows = load_json_list(rep_path)
    if not report_rows:
        return 0, len(e_rows)
    ok = 0
    miss = 0
    changed = False
    for er in e_rows:
        if not isinstance(er, dict):
            continue
        rd = str(er.get("record_date") or "")
        if not rd or not record_date_in_range(rd, start_date, end_date):
            continue
        mod = er.get("pain_point_analysis_module")
        if not isinstance(mod, list) or not mod:
            miss += 1
            continue
        first = mod[0]
        if not isinstance(first, dict):
            miss += 1
            continue
        row = find_report_row(report_rows, rd)
        if not row:
            miss += 1
            continue
        pp = row.get("pain_point_analysis")
        if not isinstance(pp, dict):
            pp = {}
            row["pain_point_analysis"] = pp
        mlist = pp.get("module")
        if not isinstance(mlist, list):
            mlist = []
            pp["module"] = mlist
        mlist.append(first)
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
    ok, miss = merge_env_intervention_to_report(
        args.uid.strip(),
        args.output_dir,
        start_date=args.start_date.strip() or None,
        end_date=args.end_date.strip() or None,
    )
    print(json.dumps({"env_intervention_appended": ok, "env_intervention_skipped": miss}, ensure_ascii=False))


if __name__ == "__main__":
    main()
