"""从 output/schedules_report.md 解析八人格两天日程模板。"""

from __future__ import annotations

import os
import re
from typing import Any

TEMPLATE_DAY_KEYS = ("2026-05-29", "2026-05-30")

RE_UID = re.compile(r"UID\s*\|\s*`([0-9a-f]{24})`", re.I)
RE_DAY = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})\s*$")
RE_TABLE_ROW = re.compile(
    r"^\|\s*\d+\s*\|\s*([0-9]{1,2}:[0-9]{2})\s*[–\-]\s*([0-9]{1,2}:[0-9]{2})\s*"
    r"\|\s*(\w+)\s*\|\s*(.+?)\s*\|\s*(\d+)min\s*\|"
)

_PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
DEFAULT_SCHEDULES_REPORT_MD = os.path.join(_PROJECT_ROOT, "output", "schedules_report.md")


def _parse_time_hm(text: str) -> tuple[int, int]:
    h, m = text.strip().split(":", 1)
    return int(h), int(m)


def calc_duration_minutes(start: str, end: str) -> int:
    sh, sm = _parse_time_hm(start)
    eh, em = _parse_time_hm(end)
    s_total = sh * 60 + sm
    e_total = eh * 60 + em
    if e_total <= s_total:
        e_total += 24 * 60
    return e_total - s_total


def parse_schedules_report_md(path: str) -> dict[str, dict[str, Any]]:
    """返回 {uid: {name, days: {template_date: [event dict]}}}。"""
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    personas: dict[str, dict[str, Any]] = {}
    current_uid: str | None = None
    current_name: str = ""
    current_day: str | None = None

    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith("## ") and not line.startswith("## 八人格"):
            current_name = line[3:].strip()
            current_uid = None
            current_day = None
            continue
        m_uid = RE_UID.search(line)
        if m_uid and current_name:
            current_uid = m_uid.group(1)
            personas[current_uid] = {
                "name": current_name,
                "days": {k: [] for k in TEMPLATE_DAY_KEYS},
            }
            continue
        m_day = RE_DAY.match(line)
        if m_day and current_uid:
            current_day = m_day.group(1)
            continue
        m_row = RE_TABLE_ROW.match(line)
        if m_row and current_uid and current_day:
            if current_day not in TEMPLATE_DAY_KEYS:
                continue
            start_t, end_t, ev_type, ev_name, _dur = m_row.groups()
            personas[current_uid]["days"][current_day].append(
                {
                    "start": start_t,
                    "end": end_t,
                    "name": ev_name.strip(),
                    "type": ev_type.strip(),
                }
            )

    return personas


def load_two_day_templates(
    md_path: str | None = None,
) -> dict[str, dict[str, Any]]:
    """加载 md 并转为 {uid: {name, days: [day0_events, day1_events]}}。"""
    path = md_path or DEFAULT_SCHEDULES_REPORT_MD
    if not os.path.isfile(path):
        raise FileNotFoundError(f"日程模板文件不存在: {path}")

    parsed = parse_schedules_report_md(path)
    out: dict[str, dict[str, Any]] = {}
    for uid, info in parsed.items():
        day_lists = [
            info["days"].get(TEMPLATE_DAY_KEYS[0], []),
            info["days"].get(TEMPLATE_DAY_KEYS[1], []),
        ]
        out[uid] = {"name": info["name"], "days": day_lists}
    return out
