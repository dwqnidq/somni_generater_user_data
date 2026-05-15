#!/usr/bin/env python3
"""morning_alarm_insight → {uid}_ai_analysis_14d.json 同日行的 fusion_insight（覆盖）。"""

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

ensure_sys_path()
from utils import atomic_write_json  # noqa: E402


def merge_fusion_insight_from_morning(
    uid: str,
    output_dir: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[int, int]:
    output_dir = os.path.abspath(output_dir)
    ai_path = os.path.join(output_dir, f"{uid}_ai_analysis_14d.json")
    morning_path = os.path.join(output_dir, f"{uid}_morning_alarm_insight.json")
    ai_rows = load_json_list(ai_path)
    morning_rows = load_json_list(morning_path)
    if not ai_rows:
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
    if written:
        atomic_write_json(ai_path, ai_rows, ensure_ascii=False, indent=2)
    return written, skipped


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uid", required=True)
    p.add_argument("--output-dir", default=default_output_dir())
    p.add_argument("--start-date", default="", help="仅处理 record_date >= 该日 YYYY-MM-DD")
    p.add_argument("--end-date", default="", help="仅处理 record_date <= 该日 YYYY-MM-DD")
    args = p.parse_args()
    w, s = merge_fusion_insight_from_morning(
        args.uid.strip(),
        args.output_dir,
        start_date=args.start_date.strip() or None,
        end_date=args.end_date.strip() or None,
    )
    print(json.dumps({"fusion_insight_written": w, "fusion_insight_skipped_no_ai_row": s}, ensure_ascii=False))


if __name__ == "__main__":
    main()
