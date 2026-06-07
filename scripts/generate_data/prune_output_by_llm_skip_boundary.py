"""按日期边界裁剪 output 中列表型 JSON 里早于 ``keep_from`` 的记录。

``prune_uid_output_files`` 默认仅裁剪 LLM 侧车 JSON，避免 LLM 试跑失败时误删 health 等基础数据。

``prune_uid_warmup_days`` 在 LLM 全流程结束后删除向前缓冲日（含基础数据 + LLM 侧车）。
删除规则：``date_field < keep_from`` 的列表项删除，``keep_from`` 当日保留。
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from generate_ai.multi_day_llm_helpers import effective_start_after_skips

# LLM 侧车产物：(文件名后缀, 日期字段名)
UID_LLM_OUTPUT_FILE_SPECS: tuple[tuple[str, str], ...] = (
    ("ai_analysis_14d.json", "record_date"),
    ("morning_alarm_insight.json", "record_date"),
    ("sleep_pattern_commonality.json", "record_date"),
    ("sleep_pattern_commonality_insight.json", "record_date"),
    ("sleep_ai_intervention.json", "record_date"),
    ("sleep_map_ranking_reason.json", "stats_date"),
    ("sleep_quality.json", "record_date"),
    ("sleep_auditory.json", "record_date"),
    ("sleep_main_summary.json", "record_date"),
    ("sleep_notice.json", "record_date"),
    ("sleep_event_environment_intervention.json", "record_date"),
)

# 基础生成产物（main.py --with-llm 缓冲日结束后裁剪）
UID_BASE_OUTPUT_FILE_SPECS: tuple[tuple[str, str], ...] = (
    ("health_data.json", "record_date"),
    ("environment_data.json", "record_date"),
    ("vitals_data.json", "record_date"),
    ("sleep_events.json", "record_date"),
    ("sleep_report.json", "record_date"),
    ("somni_sleep_analysis.json", "stats_date"),
    ("sleep_art_data.json", "record_date"),
    ("daily_emotion_steps.json", "record_date"),
    ("calendar_events.json", "event_date"),
)

UID_WARMUP_PRUNE_FILE_SPECS: tuple[tuple[str, str], ...] = (
    UID_BASE_OUTPUT_FILE_SPECS + UID_LLM_OUTPUT_FILE_SPECS
)

# 兼容旧名（测试/外部若引用）
UID_LIST_FILE_SPECS = UID_LLM_OUTPUT_FILE_SPECS


def compute_keep_from_date(
    last_skipped_dates: list[Optional[str]],
    range_start: Optional[str],
) -> Optional[str]:
    """与 main.py ``llm_day_start`` 相同：有跳过时返回保留起始日，否则返回 ``range_start``。"""
    return effective_start_after_skips(last_skipped_dates, range_start)


def keep_from_qweather_monthly_first_date(
    output_dir: str,
    *,
    filename: str = "qweather_monthly_data.json",
) -> Optional[str]:
    """取多日天气预报 JSON 数组**第一条**记录的 ``date``（YYYY-MM-DD），作裁剪保留起始日。

    删除规则仍为 ``date_field < keep_from``；即保留该日及之后，删此前。
    文件缺失、非数组或首条无 date 时返回 ``None``。
    """
    path = os.path.join(os.path.abspath(output_dir), filename)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    if not isinstance(first, dict):
        return None
    d = _day_str(first.get("date"))
    return d if len(d) >= 10 else None


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


def _prune_uid_files_with_specs(
    uid: str,
    output_dir: str,
    keep_from: str,
    file_specs: tuple[tuple[str, str], ...],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not keep_from or len(keep_from) < 10:
        return {"keep_from": keep_from, "files": {}, "total_removed": 0}

    out_abs = os.path.abspath(output_dir)
    prefix = f"{uid}_"
    file_stats: dict[str, dict[str, int]] = {}
    total_removed = 0

    for suffix, date_key in file_specs:
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


def prune_uid_output_files(
    uid: str,
    output_dir: str,
    keep_from: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """裁剪 ``output_dir/{uid}_*`` 中 LLM 侧车文件里早于 ``keep_from`` 的列表记录。返回统计摘要。"""
    return _prune_uid_files_with_specs(
        uid,
        output_dir,
        keep_from,
        UID_LLM_OUTPUT_FILE_SPECS,
        dry_run=dry_run,
    )


def prune_uid_warmup_days(
    uid: str,
    output_dir: str,
    keep_from: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """LLM 完成后删除早于正式 ``keep_from`` 的缓冲日（基础数据 + LLM 侧车）。"""
    return _prune_uid_files_with_specs(
        uid,
        output_dir,
        keep_from,
        UID_WARMUP_PRUNE_FILE_SPECS,
        dry_run=dry_run,
    )
