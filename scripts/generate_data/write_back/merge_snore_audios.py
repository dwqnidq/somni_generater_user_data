#!/usr/bin/env python3
"""将 sleep_report 中 auditory.audios 的 Snore 合并：先同分钟，再相邻分钟（时长累加）。"""

from __future__ import annotations

import argparse
import json
import os
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
from merge_snoring_data_points import (  # noqa: E402
    _bedtime_minutes_from_report_row,
    hhmm_to_minutes,
)

ensure_sys_path()
from utils import atomic_write_json  # noqa: E402

_SNORE_BASE_DURATION_SEC = 3


def merge_snore_audios_in_list(audios: list[Any]) -> tuple[list[Any], int]:
    """
    对 ``type == "Snore"`` 且 ``time`` 相同的条目合并为一条：
    保留组内任意一条 ``url``，``duration_sec = 3 * 组内条数``。
    非 Snore 条目顺序与内容不变；Snore 在首次出现的时间点替换为合并结果。

    返回 (新 audios 列表, 移除的 Snore 条数)。
    """
    if not audios:
        return [], 0

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in audios:
        if not isinstance(item, dict):
            continue
        if (item.get("type") or "") != "Snore":
            continue
        time_key = str(item.get("time") or "").strip()
        if not time_key:
            continue
        groups[time_key].append(item)

    merged_by_time: dict[str, dict[str, Any]] = {}
    for time_key, items in groups.items():
        pick = items[0]
        merged_by_time[time_key] = {
            "url": pick.get("url"),
            "duration_sec": _SNORE_BASE_DURATION_SEC * len(items),
            "type": "Snore",
            "time": time_key,
        }

    out: list[Any] = []
    seen_snore_times: set[str] = set()
    removed = 0

    for item in audios:
        if not isinstance(item, dict):
            out.append(item)
            continue
        if (item.get("type") or "") != "Snore":
            out.append(item)
            continue
        time_key = str(item.get("time") or "").strip()
        if not time_key:
            out.append(item)
            continue
        if time_key in seen_snore_times:
            continue
        seen_snore_times.add(time_key)
        out.append(merged_by_time[time_key])
        removed += len(groups[time_key]) - 1

    return out, removed


def _snore_session_sort_key(minute: int, bedtime_min: Optional[int]) -> int:
    if bedtime_min is None:
        return minute
    if minute >= bedtime_min:
        return minute - bedtime_min
    return (1440 - bedtime_min) + minute


def merge_adjacent_minute_snore_audios_in_list(
    audios: list[Any],
    *,
    bedtime_min: Optional[int] = None,
) -> tuple[list[Any], int]:
    """
    将睡眠会话内「分钟相邻」（session 序相差 1）的 Snore 再合并为一条。
    ``time`` 取该段起始分钟；``duration_sec`` 为组内累加。不修改 data_points。
    """
    if not audios:
        return [], 0

    indexed: list[tuple[int, int, str, dict[str, Any]]] = []
    for item in audios:
        if not isinstance(item, dict) or (item.get("type") or "") != "Snore":
            continue
        time_key = str(item.get("time") or "").strip()
        minute = hhmm_to_minutes(time_key)
        if minute is None:
            continue
        indexed.append(
            (_snore_session_sort_key(minute, bedtime_min), minute, time_key, item)
        )
    if not indexed:
        return list(audios), 0

    indexed.sort(key=lambda x: (x[0], x[1]))
    groups: list[list[tuple[int, int, str, dict[str, Any]]]] = [[indexed[0]]]
    for row in indexed[1:]:
        if row[0] - groups[-1][-1][0] == 1:
            groups[-1].append(row)
        else:
            groups.append([row])

    merged_by_start: dict[str, dict[str, Any]] = {}
    minute_time_to_start: dict[str, str] = {}
    for group in groups:
        start_time = group[0][2]
        pick = group[0][3]
        total_duration = 0
        for _, _, _, audio in group:
            try:
                total_duration += int(audio.get("duration_sec", 0) or 0)
            except (TypeError, ValueError):
                pass
        if total_duration <= 0:
            total_duration = _SNORE_BASE_DURATION_SEC * len(group)
        merged_by_start[start_time] = {
            "url": pick.get("url"),
            "duration_sec": total_duration,
            "type": "Snore",
            "time": start_time,
        }
        for _, _, time_key, _ in group:
            minute_time_to_start[time_key] = start_time

    out: list[Any] = []
    seen_starts: set[str] = set()
    removed = 0
    for item in audios:
        if not isinstance(item, dict):
            out.append(item)
            continue
        if (item.get("type") or "") != "Snore":
            out.append(item)
            continue
        time_key = str(item.get("time") or "").strip()
        if not time_key:
            out.append(item)
            continue
        start_time = minute_time_to_start.get(time_key)
        if not start_time:
            out.append(item)
            continue
        if start_time in seen_starts:
            removed += 1
            continue
        seen_starts.add(start_time)
        out.append(merged_by_start[start_time])
        removed += len([t for t in minute_time_to_start if minute_time_to_start[t] == start_time]) - 1

    return out, removed


def merge_snore_audios_to_report(
    uid: str,
    output_dir: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[int, int, int]:
    """
    写回 ``quality_analysis.auditory.audios``：同分钟合并后再合并相邻分钟 Snore。
    不修改 ``snoring_analysis.data_points`` 与 ``auditory.module``。

    返回 (更新的报告日数, 跳过的报告日数, 移除的 Snore 条数合计)。
    """
    output_dir = os.path.abspath(output_dir)
    rep_path = os.path.join(output_dir, f"{uid}_sleep_report.json")
    report_rows = load_json_list(rep_path)
    if not report_rows:
        return 0, 0, 0

    ok = 0
    skip = 0
    total_removed = 0
    changed = False

    for row in report_rows:
        if not isinstance(row, dict):
            continue
        rd = str(row.get("record_date") or "")
        if not rd or not record_date_in_range(rd, start_date, end_date):
            continue
        qa = row.get("quality_analysis")
        if not isinstance(qa, dict):
            skip += 1
            continue
        aud = qa.get("auditory")
        if not isinstance(aud, dict):
            skip += 1
            continue
        audios = aud.get("audios")
        if not isinstance(audios, list) or not audios:
            skip += 1
            continue

        bedtime_min = _bedtime_minutes_from_report_row(row)
        merged, removed_same = merge_snore_audios_in_list(audios)
        merged, removed_adj = merge_adjacent_minute_snore_audios_in_list(
            merged, bedtime_min=bedtime_min
        )
        removed = removed_same + removed_adj
        if merged == audios and removed <= 0:
            skip += 1
            continue
        aud["audios"] = merged
        ok += 1
        total_removed += removed
        changed = True

    if changed:
        atomic_write_json(rep_path, report_rows, ensure_ascii=False, indent=2)
    return ok, skip, total_removed


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uid", required=True)
    p.add_argument("--output-dir", default=default_output_dir())
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    args = p.parse_args()
    ok, skip, removed = merge_snore_audios_to_report(
        args.uid.strip(),
        args.output_dir,
        start_date=args.start_date.strip() or None,
        end_date=args.end_date.strip() or None,
    )
    print(
        json.dumps(
            {
                "snore_audios_merged_days": ok,
                "skipped": skip,
                "snore_entries_removed": removed,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
