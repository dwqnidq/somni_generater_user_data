"""小型睡眠辅助函数。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from .time_utils import calculate_duration, utc_to_local, format_time_to_hhmm


def probability_zero_night_awakenings(profile):
    """
    按人格睡眠质量给出「本晚夜间清醒次数为 0」的抽样概率。
    睡眠结构较好的人格概率高，较差的人格概率低但仍偶有整晚无中段清醒。
    """
    prof = profile or {}
    sp = prof.get("stage_pattern")
    if sp == "optimal":
        return 0.52
    if sp == "healthy_active":
        return 0.38
    if sp == "sensitive_calm":
        return 0.22
    if sp == "sensitive_active":
        return 0.14
    if sp == "poor_quality":
        return 0.06
    se = prof.get("sleep_efficiency") or {"min": 85, "max": 93}
    try:
        mid = 0.5 * (float(se["min"]) + float(se["max"]))
    except (TypeError, KeyError, ValueError):
        mid = 88.0
    return max(0.05, min(0.48, (mid - 73.0) / 25.0))


def night_wake_episodes_for_prompts(sleep_data):
    """从 idf_data 统计夜间清醒段数：去掉首段入睡潜伏期清醒，去掉与 wake_time 对齐的觉后清醒段。"""
    raw = sleep_data.get("raw_data", {})
    idf = sleep_data.get("idf_data") or []
    awake_segs = [s for s in idf if s.get("stage") == "awake"]
    if len(awake_segs) <= 1:
        return 0
    mid = awake_segs[1:]
    wk = raw.get("wake_time") or ""
    if wk:
        wk_hm = format_time_to_hhmm(utc_to_local(wk))
        if mid and mid[-1].get("start") == wk_hm:
            mid = mid[:-1]
    return len(mid)


def night_wake_minutes_for_prompts(sleep_data):
    """从 idf_data 统计夜间中段清醒总分钟数（不含入睡前与起床后清醒段）。"""
    raw = sleep_data.get("raw_data", {})
    idf = sleep_data.get("idf_data") or []
    awake_segs = [s for s in idf if s.get("stage") == "awake"]
    if len(awake_segs) <= 1:
        return 0

    mid = awake_segs[1:]
    wk = raw.get("wake_time") or ""
    if wk:
        wk_hm = format_time_to_hhmm(utc_to_local(wk))
        if mid and mid[-1].get("start") == wk_hm:
            mid = mid[:-1]

    anchor = sleep_data.get("record_date") or raw.get("record_date")
    total = 0
    for seg in mid:
        start = seg.get("start")
        end = seg.get("end")
        if not start or not end:
            continue
        total += calculate_duration(start, end, anchor)
    return total


def build_sleep_events_index(user_id, output_dir="output"):
    """按 record_date 索引该用户 sleep_events，减少重复 IO。"""
    idx = {}
    sleep_events_file = os.path.join(output_dir, f"{user_id}_sleep_events.json")
    if not os.path.exists(sleep_events_file):
        return idx
    try:
        with open(sleep_events_file, 'r', encoding='utf-8') as f:
            all_events = json.load(f)
        for e in all_events:
            d = e.get('record_date')
            if not d:
                continue
            idx.setdefault(d, []).append(e)
    except Exception:
        return {}
    return idx


def filter_sleep_events_by_session_id(events: list, session_id: str) -> list:
    """仅保留指定 session_id 的睡眠事件（用于环境干预分析等按 session 隔离）。"""
    sid = str(session_id or "").strip()
    if not sid:
        return list(events) if events else []
    return [
        e
        for e in events
        if isinstance(e, dict) and str(e.get("session_id") or "").strip() == sid
    ]
