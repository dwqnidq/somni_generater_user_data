#!/usr/bin/env python3
"""从 output/schedules_report.md 解析八人格两天日程模板，按组铺到 6 月并落盘预览。

模板：每人格 md 中 2026-05-29（第 1 天）、2026-05-30（第 2 天）。
铺月：6/1–6/2、6/3–6/4、…、6/29–6/30 共 15 组，每组复用上述两天。

用法（项目根）:
  .venv/bin/python scripts/insert_data/generate_schedules_from_report_md.py
  .venv/bin/python scripts/insert_data/generate_schedules_from_report_md.py --out-dir output/calendar_events_from_report
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
_SCRIPTS_GEN = os.path.join(PROJECT_ROOT, "scripts", "generate_data")
if _SCRIPTS_GEN not in sys.path:
    sys.path.insert(0, _SCRIPTS_GEN)

from schedules_report_md import (  # noqa: E402
    DEFAULT_SCHEDULES_REPORT_MD,
    TEMPLATE_DAY_KEYS,
    calc_duration_minutes,
    parse_schedules_report_md,
)

DEFAULT_MD = DEFAULT_SCHEDULES_REPORT_MD
DEFAULT_OUT = os.path.join(PROJECT_ROOT, "output", "calendar_events_from_report")
PREVIEW_SUMMARY = os.path.join(PROJECT_ROOT, "output", "schedules_from_report_june_preview.md")

JUNE_YEAR = 2026
JUNE_MONTH = 6
CREATE_TIME_ISO = "2026-06-01T08:00:00.000Z"


def june_date_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    d = date(JUNE_YEAR, JUNE_MONTH, 1)
    end = date(JUNE_YEAR, JUNE_MONTH, 30)
    while d <= end:
        d2 = d + timedelta(days=1)
        if d2 > end:
            break
        pairs.append((d.isoformat(), d2.isoformat()))
        d += timedelta(days=2)
    return pairs


def build_june_events(personas: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    pairs = june_date_pairs()
    out: dict[str, list[dict[str, Any]]] = {}

    for uid, info in personas.items():
        days_tpl = info["days"]
        events: list[dict[str, Any]] = []
        for target_day1, target_day2 in pairs:
            mapping = (
                (target_day1, TEMPLATE_DAY_KEYS[0]),
                (target_day2, TEMPLATE_DAY_KEYS[1]),
            )
            for event_date, tpl_key in mapping:
                for ev in days_tpl.get(tpl_key, []):
                    events.append(
                        {
                            "uid": uid,
                            "event_date": event_date,
                            "event_type": ev["type"],
                            "event_name": ev["name"],
                            "start_time": ev["start"],
                            "end_time": ev["end"],
                            "duration_minutes": calc_duration_minutes(
                                ev["start"], ev["end"]
                            ),
                            "create_time": CREATE_TIME_ISO,
                            "update_time": CREATE_TIME_ISO,
                            "language": "zh",
                        }
                    )
        out[uid] = events
    return out


def write_preview_summary(
    path: str,
    personas: dict[str, dict[str, Any]],
    by_uid: dict[str, list[dict[str, Any]]],
) -> None:
    lines = [
        "# 六月日程预览（来自 schedules_report.md）",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 模板日：{TEMPLATE_DAY_KEYS[0]} / {TEMPLATE_DAY_KEYS[1]}",
        f"> 铺月：{JUNE_YEAR}-{JUNE_MONTH:02d}-01 ～ 30，每 2 天一组共 {len(june_date_pairs())} 组",
        "",
    ]
    for uid, info in personas.items():
        evs = by_uid[uid]
        per_day: dict[str, int] = {}
        for e in evs:
            d = e["event_date"]
            per_day[d] = per_day.get(d, 0) + 1
        lines.append(f"## {info['name']}")
        lines.append("")
        lines.append(f"- UID: `{uid}`")
        lines.append(f"- 总条数: **{len(evs)}**（{len(per_day)} 个有日程的 6 月日期）")
        lines.append("")
        sample_dates = ["2026-06-01", "2026-06-02", "2026-06-29", "2026-06-30"]
        for sd in sample_dates:
            day_evs = [e for e in evs if e["event_date"] == sd]
            if not day_evs:
                continue
            lines.append(f"### {sd}（{len(day_evs)} 条）")
            lines.append("")
            lines.append("| 时间段 | 类型 | 事件名称 | 时长 |")
            lines.append("|--------|------|----------|------|")
            for e in day_evs:
                lines.append(
                    f"| {e['start_time']}–{e['end_time']} | {e['event_type']} "
                    f"| {e['event_name']} | {e['duration_minutes']}min |"
                )
            lines.append("")
        lines.append("---")
        lines.append("")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="从 schedules_report.md 生成 6 月日程预览 JSON")
    parser.add_argument("--md", default=DEFAULT_MD, help="源 Markdown 路径")
    parser.add_argument("--out-dir", default=DEFAULT_OUT, help="JSON 输出目录")
    parser.add_argument(
        "--summary",
        default=PREVIEW_SUMMARY,
        help="可读预览 Markdown 路径",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.md):
        print(f"错误: 找不到 {args.md}", file=sys.stderr)
        return 2

    personas = parse_schedules_report_md(args.md)
    if len(personas) != 8:
        print(f"警告: 解析到 {len(personas)} 个人格（期望 8）", file=sys.stderr)

    by_uid = build_june_events(personas)
    os.makedirs(args.out_dir, exist_ok=True)

    total = 0
    for uid, events in by_uid.items():
        path = os.path.join(args.out_dir, f"{uid}_calendar_events.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
        name = personas[uid]["name"]
        print(f"  {name} ({uid[:12]}…): {len(events)} 条 → {path}")
        total += len(events)

    write_preview_summary(args.summary, personas, by_uid)
    print(f"\n可读预览: {args.summary}")
    print(f"合计 {total} 条（8 人格 × 15 组 × 模板两天）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
