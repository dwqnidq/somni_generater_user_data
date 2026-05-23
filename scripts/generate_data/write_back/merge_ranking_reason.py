#!/usr/bin/env python3
"""sleep_map_ranking_reason.ranking_reason 写回 somni_sleep_analysis.evaluation。"""

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
    load_json_list,
    record_date_in_range,
)

ensure_sys_path()
from utils import atomic_write_json  # noqa: E402


def merge_ranking_reason_to_analysis(
    uid: str,
    output_dir: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[int, int]:
    """将 ranking_reason 写入 somni_sleep_analysis 的 evaluation 字段。

    Returns:
        (updated, skipped) 写入成功数与跳过数。
    """
    output_dir = os.path.abspath(output_dir)
    rr_path = os.path.join(output_dir, f"{uid}_sleep_map_ranking_reason.json")
    analysis_path = os.path.join(output_dir, f"{uid}_somni_sleep_analysis.json")
    rr_rows = load_json_list(rr_path)
    analysis_rows = load_json_list(analysis_path)
    if not analysis_rows:
        return 0, len(rr_rows)

    analysis_by_date: dict[str, dict] = {}
    for r in analysis_rows:
        if not isinstance(r, dict):
            continue
        sd = str(r.get("stats_date") or "")
        if sd:
            analysis_by_date[sd] = r

    ok = 0
    miss = 0
    changed = False
    for rr in rr_rows:
        if not isinstance(rr, dict):
            continue
        sd = str(rr.get("stats_date") or "")
        if not sd or not record_date_in_range(sd, start_date, end_date):
            continue
        reason = str(rr.get("ranking_reason") or "").strip()
        if not reason:
            miss += 1
            continue
        row = analysis_by_date.get(sd)
        if not row:
            miss += 1
            continue
        row["evaluation"] = reason
        ok += 1
        changed = True
    if changed:
        atomic_write_json(analysis_path, analysis_rows, ensure_ascii=False, indent=2)
    return ok, miss


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uid", required=True)
    p.add_argument("--output-dir", default=default_output_dir())
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    args = p.parse_args()
    ok, miss = merge_ranking_reason_to_analysis(
        args.uid.strip(),
        args.output_dir,
        start_date=args.start_date.strip() or None,
        end_date=args.end_date.strip() or None,
    )
    print(json.dumps({"ranking_reason_updated": ok, "ranking_reason_skipped": miss}, ensure_ascii=False))


if __name__ == "__main__":
    main()
