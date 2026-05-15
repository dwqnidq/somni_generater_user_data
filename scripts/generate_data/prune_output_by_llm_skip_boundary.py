"""按多天 LLM 步骤的「最后跳过日」收敛边界，裁剪 output 中早于有效起始日的记录。

有效保留起始日 ``keep_from`` 与 main.py 中 ``llm_day_start`` 一致：
取各步 ``last_skipped_date`` 的日历最早者，再取其次日（且不早于 ``range_start``）。
删除 ``date_field < keep_from`` 的列表项（``keep_from`` 当日保留）。
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from generate_ai.multi_day_llm_helpers import effective_start_after_skips

# (文件名后缀, 日期字段名)
UID_LIST_FILE_SPECS: tuple[tuple[str, str], ...] = (
    ("health_data.json", "record_date"),
    ("environment_data.json", "record_date"),
    ("environment_data_baseline.json", "record_date"),
    ("vitals_data.json", "record_date"),
    ("vitals_data_baseline.json", "record_date"),
    ("sleep_events.json", "record_date"),
    ("sleep_report.json", "record_date"),
    ("daily_emotion_steps.json", "event_date"),
    ("calendar_events.json", "event_date"),
    ("sleep_art_data.json", "record_date"),
    ("somni_sleep_analysis.json", "stats_date"),
    ("ai_analysis_14d.json", "record_date"),
    ("morning_alarm_insight.json", "record_date"),
    ("sleep_pattern_commonality.json", "record_date"),
    ("sleep_pattern_commonality_insight.json", "record_date"),
    ("sleep_ai_intervention.json", "record_date"),
    ("sleep_quality.json", "record_date"),
    ("sleep_auditory.json", "record_date"),
    ("sleep_main_summary.json", "record_date"),
    ("sleep_notice.json", "record_date"),
    ("sleep_event_environment_intervention.json", "record_date"),
)


def compute_keep_from_date(
    last_skipped_dates: list[Optional[str]],
    range_start: Optional[str],
) -> Optional[str]:
    """与 main.py ``llm_day_start`` 相同：有跳过时返回保留起始日，否则返回 ``range_start``。"""
    return effective_start_after_skips(last_skipped_dates, range_start)


def _day_str(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    return s[:10] if len(s) >= 10 else s


def _filter_list_rows(rows: list, date_key: str, keep_from: str) -> tuple[list, int]:
    kept: list = []
    removed = 0
    for row in rows:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        d = _day_str(row.get(date_key))
        if d and d < keep_from:
            removed += 1
            continue
        kept.append(row)
    return kept, removed


def prune_uid_output_files(
    uid: str,
    output_dir: str,
    keep_from: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """裁剪 ``output_dir/{uid}_*`` 中早于 ``keep_from`` 的列表记录。返回统计摘要。"""
    if not keep_from or len(keep_from) < 10:
        return {"keep_from": keep_from, "files": {}, "total_removed": 0}

    out_abs = os.path.abspath(output_dir)
    prefix = f"{uid}_"
    file_stats: dict[str, dict[str, int]] = {}
    total_removed = 0

    for suffix, date_key in UID_LIST_FILE_SPECS:
        path = os.path.join(out_abs, prefix + suffix)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            continue
        before = len(data)
        kept, removed = _filter_list_rows(data, date_key, keep_from)
        if removed == 0:
            continue
        if not dry_run:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(kept, f, ensure_ascii=False, indent=2)
        file_stats[suffix] = {"before": before, "after": before - removed, "removed": removed}
        total_removed += removed

    return {"keep_from": keep_from, "files": file_stats, "total_removed": total_removed}
