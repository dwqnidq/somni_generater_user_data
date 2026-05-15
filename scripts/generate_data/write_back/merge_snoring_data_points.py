#!/usr/bin/env python3
"""从 sleep_events 打鼾事件生成 snoring_analysis.data_points 并写回 sleep_report。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
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

_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")


def normalize_hhmm(raw: Any) -> Optional[str]:
    """将 event_timestamp 规范为 ``HH:MM``；无法解析时返回 None。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = _HHMM_RE.match(s)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return f"{hh:02d}:{mm:02d}"


def hhmm_to_minutes(hhmm: str) -> Optional[int]:
    norm = normalize_hhmm(hhmm)
    if not norm:
        return None
    hh, mm = norm.split(":")
    return int(hh) * 60 + int(mm)


def minutes_to_hhmm(minutes: int) -> str:
    v = int(minutes) % 1440
    return f"{v // 60:02d}:{v % 60:02d}"


def _round_noise_db(avg: float) -> int:
    """四舍五入为整数分贝（0.5 向上取整）。"""
    if avg >= 0:
        return int(avg + 0.5)
    return int(avg - 0.5)


def is_snoring_event(event: dict) -> bool:
    if not isinstance(event, dict):
        return False
    if str(event.get("code") or "").strip().lower() == "snoring":
        return True
    return str(event.get("event_type") or "").strip() == "打鼾"


def _bedtime_minutes_from_report_row(report_row: dict) -> Optional[int]:
    sq = (report_row.get("quality_analysis") or {}).get("sleep_quality") or {}
    if not isinstance(sq, dict):
        return None
    return hhmm_to_minutes(sq.get("bedtime"))


def sort_minutes_for_sleep_session(minutes: list[int], bedtime_min: Optional[int]) -> list[int]:
    """按睡眠会话顺序排序分钟（以 bedtime 为锚，支持跨午夜）。"""
    if not minutes:
        return []
    if bedtime_min is None:
        return sorted(minutes)

    def session_key(m: int) -> int:
        if m >= bedtime_min:
            return m - bedtime_min
        return (1440 - bedtime_min) + m

    return sorted(minutes, key=session_key)


def build_snoring_data_points_from_events(
    events: list[dict],
    *,
    bedtime_min: Optional[int] = None,
) -> list[dict[str, Any]]:
    """
    同一分钟内多条打鼾的 noise_db 取平均后四舍五入为整数；
    每分钟输出一条 ``{time, value}``（方案 A）。
    """
    by_minute: dict[int, list[float]] = defaultdict(list)
    for ev in events:
        if not is_snoring_event(ev):
            continue
        hhmm = normalize_hhmm(ev.get("event_timestamp"))
        if not hhmm:
            continue
        minute = hhmm_to_minutes(hhmm)
        if minute is None:
            continue
        noise = ev.get("noise_db")
        if noise is None:
            continue
        try:
            by_minute[minute].append(float(noise))
        except (TypeError, ValueError):
            continue

    out: list[dict[str, Any]] = []
    for minute in sort_minutes_for_sleep_session(list(by_minute.keys()), bedtime_min):
        values = by_minute[minute]
        if not values:
            continue
        avg = sum(values) / len(values)
        out.append({"time": minutes_to_hhmm(minute), "value": _round_noise_db(avg)})
    return out


def merge_snoring_data_points_to_report(
    uid: str,
    output_dir: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[int, int]:
    """
    按 ``record_date`` 从 sleep_events 重算并写回
    ``quality_analysis.auditory.snoring_analysis.data_points``。
    返回 (更新的报告日数, 跳过的报告日数)。
    """
    output_dir = os.path.abspath(output_dir)
    uid = str(uid).strip()
    events_path = os.path.join(output_dir, f"{uid}_sleep_events.json")
    rep_path = os.path.join(output_dir, f"{uid}_sleep_report.json")
    sleep_events = load_json_list(events_path)
    report_rows = load_json_list(rep_path)
    if not report_rows:
        return 0, 0

    events_by_date: dict[str, list[dict]] = defaultdict(list)
    for ev in sleep_events:
        if not isinstance(ev, dict):
            continue
        ev_uid = str(ev.get("uid") or "").strip()
        if ev_uid and ev_uid != uid:
            continue
        rd = str(ev.get("record_date") or "")
        if not rd:
            continue
        events_by_date[rd].append(ev)

    ok = 0
    skip = 0
    changed = False
    for row in report_rows:
        if not isinstance(row, dict):
            continue
        rd = str(row.get("record_date") or "")
        if not rd or not record_date_in_range(rd, start_date, end_date):
            continue
        day_events = [
            e
            for e in events_by_date.get(rd, [])
            if is_snoring_event(e)
        ]
        bedtime_min = _bedtime_minutes_from_report_row(row)
        dps = build_snoring_data_points_from_events(day_events, bedtime_min=bedtime_min)
        qa = row.setdefault("quality_analysis", {})
        aud = qa.setdefault("auditory", {})
        sa = aud.setdefault("snoring_analysis", {})
        if not isinstance(sa, dict):
            sa = {}
            aud["snoring_analysis"] = sa
        sa["data_points"] = dps
        ok += 1
        changed = True

    if changed:
        atomic_write_json(rep_path, report_rows, ensure_ascii=False, indent=2)
    return ok, skip


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uid", required=True)
    p.add_argument("--output-dir", default=default_output_dir())
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    args = p.parse_args()
    ok, skip = merge_snoring_data_points_to_report(
        args.uid.strip(),
        args.output_dir,
        start_date=args.start_date.strip() or None,
        end_date=args.end_date.strip() or None,
    )
    print(
        json.dumps(
            {"snoring_data_points_updated_days": ok, "skipped": skip},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
