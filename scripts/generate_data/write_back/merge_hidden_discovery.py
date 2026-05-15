#!/usr/bin/env python3
"""sleep_pattern_commonality + insight → sleep_report.quality_analysis.hidden_discovery（整对象替换）。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

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


def _build_hidden_discovery(common_row: dict, insight_row: dict) -> Optional[dict]:
    inner = insight_row.get("sleep_pattern_commonality_insight")
    if not isinstance(inner, dict):
        return None
    tgt = str(inner.get("target") or "").strip()
    desc = str(inner.get("description") or "").strip()
    tips = str(inner.get("tips") or "").strip()
    if not (tgt and desc and tips):
        return None
    blocks = common_row.get("sleep_pattern_commonality")
    if not isinstance(blocks, list):
        blocks = []
    discover: list[dict] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        title = str(b.get("highlight") or "")
        content = str(b.get("analysis") or "")
        raw_list = b.get("list")
        lst: list[Any] = list(raw_list) if isinstance(raw_list, list) else []
        discover.append(
            {"title": title, "content": content, "confidence": "", "type": "", "list": lst}
        )
    return {"module": [{"target": tgt, "description": desc, "tips": tips}], "discover": discover}


def merge_hidden_discovery_to_sleep_report(
    uid: str,
    output_dir: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[int, int]:
    output_dir = os.path.abspath(output_dir)
    rep_path = os.path.join(output_dir, f"{uid}_sleep_report.json")
    com_path = os.path.join(output_dir, f"{uid}_sleep_pattern_commonality.json")
    ins_path = os.path.join(output_dir, f"{uid}_sleep_pattern_commonality_insight.json")
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
    if changed:
        atomic_write_json(rep_path, report_rows, ensure_ascii=False, indent=2)
    return written, skipped


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uid", required=True)
    p.add_argument("--output-dir", default=default_output_dir())
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    args = p.parse_args()
    w, s = merge_hidden_discovery_to_sleep_report(
        args.uid.strip(),
        args.output_dir,
        start_date=args.start_date.strip() or None,
        end_date=args.end_date.strip() or None,
    )
    print(json.dumps({"hidden_discovery_written_days": w, "hidden_discovery_skipped_days": s}, ensure_ascii=False))


if __name__ == "__main__":
    main()
