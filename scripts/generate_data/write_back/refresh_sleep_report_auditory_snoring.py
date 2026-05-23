#!/usr/bin/env python3
"""补全 sleep_report.quality_analysis.auditory：audios + 按分钟聚合的 snoring data_points。"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

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

from merge_snore_audios import merge_snore_audios_to_report  # noqa: E402
from merge_snoring_data_points import (  # noqa: E402
    _bedtime_minutes_from_report_row,
    build_snoring_data_points_from_events,
    is_snoring_event,
)
from sleep_report.auditory import (  # noqa: E402
    build_apnea_auditory_target_title,
    rebuild_auditory_audios_and_snoring_data_points,
)


def _health_by_record_date(uid: str, output_dir: str) -> dict[str, dict]:
    import generate_health_data as gh

    return gh._health_data_by_record_date(uid, output_dir=output_dir)


def refresh_sleep_report_auditory_snoring_for_uid(
    uid: str,
    output_dir: str,
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    refresh_audios: bool = True,
) -> dict[str, Any]:
    """写回 snoring_analysis.data_points（按分钟、sleep_events.noise_db）。

    refresh_audios=True 时同时重建 audios，并刷新呼吸暂停相关的 target / risk_alert。
    """
    output_dir = os.path.abspath(output_dir)
    uid = str(uid).strip()
    rep_path = os.path.join(output_dir, f"{uid}_sleep_report.json")
    events_path = os.path.join(output_dir, f"{uid}_sleep_events.json")
    report_rows = load_json_list(rep_path)
    sleep_events = load_json_list(events_path)
    if not report_rows:
        return {
            "uid": uid,
            "days_updated": 0,
            "refresh_audios": refresh_audios,
            "skipped": "no_sleep_report",
        }

    health_by_date = _health_by_record_date(uid, output_dir)
    events_by_date: dict[str, list[dict]] = {}
    for ev in sleep_events:
        if not isinstance(ev, dict):
            continue
        ev_uid = str(ev.get("uid") or "").strip()
        if ev_uid and ev_uid != uid:
            continue
        rd = str(ev.get("record_date") or "")
        if rd:
            events_by_date.setdefault(rd, []).append(ev)

    updated = 0
    changed = False
    for row in report_rows:
        if not isinstance(row, dict):
            continue
        rd = str(row.get("record_date") or "")
        if not rd or not record_date_in_range(rd, start_date, end_date):
            continue
        sleep_day = health_by_date.get(rd) or {}
        day_events = events_by_date.get(rd, [])
        snoring_events = [e for e in day_events if is_snoring_event(e)]

        qa = row.setdefault("quality_analysis", {})
        aud = qa.setdefault("auditory", {})
        if not isinstance(aud, dict):
            aud = {}
            qa["auditory"] = aud

        if refresh_audios:
            audios, _ = rebuild_auditory_audios_and_snoring_data_points(
                rd, uid, sleep_events, sleep_day
            )
            aud["audios"] = audios
            apnea_count = int((sleep_day.get("raw_data") or {}).get("apnea_count", 0) or 0)
            if apnea_count >= 5:
                aud["target"] = build_apnea_auditory_target_title(rd)
                aud["risk_alert"] = f"昨晚出现{apnea_count}次呼吸暂停疑似时间，建议关注。"
            else:
                aud["target"] = ""
                aud["risk_alert"] = ""

        bedtime_min = _bedtime_minutes_from_report_row(row)
        dps = build_snoring_data_points_from_events(
            snoring_events, bedtime_min=bedtime_min
        )
        sa = aud.setdefault("snoring_analysis", {})
        if not isinstance(sa, dict):
            sa = {}
            aud["snoring_analysis"] = sa
        sa["data_points"] = dps
        updated += 1
        changed = True

    if changed:
        atomic_write_json(rep_path, report_rows, ensure_ascii=False, indent=2)

    snore_merged_days = 0
    snore_entries_removed = 0
    if refresh_audios and updated > 0:
        snore_merged_days, _, snore_entries_removed = merge_snore_audios_to_report(
            uid, output_dir, start_date=start_date, end_date=end_date
        )

    return {
        "uid": uid,
        "days_updated": updated,
        "refresh_audios": refresh_audios,
        "snore_audios_merged_days": snore_merged_days,
        "snore_entries_removed": snore_entries_removed,
    }
