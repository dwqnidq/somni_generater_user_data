#!/usr/bin/env python3
"""sleep_ai_intervention 中 sleep_events 二维数组按 _id 将 detail 写回 {uid}_sleep_events.json。"""

from __future__ import annotations

import argparse
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from _common import default_output_dir, ensure_sys_path, load_json_list, record_date_in_range  # noqa: E402

ensure_sys_path()
from utils import atomic_write_json  # noqa: E402


def merge_sleep_events_details_from_intervention(
    uid: str,
    output_dir: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[int, int]:
    output_dir = os.path.abspath(output_dir)
    int_path = os.path.join(output_dir, f"{uid}_sleep_ai_intervention.json")
    ev_path = os.path.join(output_dir, f"{uid}_sleep_events.json")
    interventions = load_json_list(int_path)
    events = load_json_list(ev_path)
    if not events:
        return 0, 0
    id_to_idx = {}
    for i, e in enumerate(events):
        if isinstance(e, dict) and e.get("_id"):
            id_to_idx[str(e["_id"])] = i
    updated_ids = 0
    missing = 0
    for day in interventions:
        if not isinstance(day, dict):
            continue
        rd = str(day.get("record_date") or "")
        if not record_date_in_range(rd, start_date, end_date):
            continue
        groups = day.get("sleep_events")
        if not isinstance(groups, list):
            continue
        for pair in groups:
            if not isinstance(pair, list):
                continue
            for ev in pair:
                if not isinstance(ev, dict):
                    continue
                eid = ev.get("_id")
                if not eid:
                    continue
                idx = id_to_idx.get(str(eid))
                if idx is None:
                    missing += 1
                    continue
                detail = ev.get("detail")
                if isinstance(detail, dict):
                    events[idx]["detail"] = detail
                    updated_ids += 1
    if updated_ids:
        atomic_write_json(ev_path, events, ensure_ascii=False, indent=2)
    return updated_ids, missing


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uid", required=True)
    p.add_argument("--output-dir", default=default_output_dir())
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    args = p.parse_args()
    u, m = merge_sleep_events_details_from_intervention(
        args.uid.strip(),
        args.output_dir,
        start_date=args.start_date.strip() or None,
        end_date=args.end_date.strip() or None,
    )
    print(json.dumps({"sleep_events_detail_updates": u, "sleep_events_detail_missing_id": m}, ensure_ascii=False))


if __name__ == "__main__":
    main()
