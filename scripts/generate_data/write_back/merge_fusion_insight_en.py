#!/usr/bin/env python3
"""morning_alarm_insight_en → ai_analysis_14d_en 同日 fusion_insight（覆盖）。"""

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
    SUFFIX_AI_ANALYSIS_14D_EN,
    SUFFIX_MORNING_ALARM_INSIGHT_EN,
)

ensure_sys_path()
from utils import atomic_write_json  # noqa: E402


def merge_fusion_insight_en_from_morning(
    uid: str,
    output_dir: str,
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    dry_run: bool = False,
) -> tuple[int, int]:
    output_dir = os.path.abspath(output_dir)
    ai_path = os.path.join(output_dir, f"{uid}{SUFFIX_AI_ANALYSIS_14D_EN}")
    morning_path = os.path.join(output_dir, f"{uid}{SUFFIX_MORNING_ALARM_INSIGHT_EN}")
    ai_rows = load_json_list(ai_path)
    morning_rows = load_json_list(morning_path)
    if not ai_rows or not morning_rows:
        return 0, 0
    ai_by_date = index_by_record_date(ai_rows)
    written = 0
    skipped = 0
    for m in morning_rows:
        if not isinstance(m, dict):
            continue
        rd = str(m.get("record_date") or "")
        if not rd or not record_date_in_range(rd, start_date, end_date):
            continue
        target = ai_by_date.get(rd)
        if not target:
            skipped += 1
            continue
        insight = m.get("alarm_insight")
        target["fusion_insight"] = insight if insight is not None else ""
        written += 1
    if written and not dry_run:
        atomic_write_json(ai_path, ai_rows, ensure_ascii=False, indent=2)
    return written, skipped


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uid", required=True)
    p.add_argument("--output-dir", default=default_output_dir())
    p.add_argument("--start-date", default="", help="仅处理 record_date >= 该日 YYYY-MM-DD")
    p.add_argument("--end-date", default="", help="仅处理 record_date <= 该日 YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    args = p.parse_args()
    w, s = merge_fusion_insight_en_from_morning(
        args.uid.strip(),
        args.output_dir,
        start_date=args.start_date.strip() or None,
        end_date=args.end_date.strip() or None,
        dry_run=args.dry_run,
    )
    print(
        json.dumps(
            {"fusion_insight_written": w, "fusion_insight_skipped_no_ai_row": s, "dry_run": args.dry_run},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
