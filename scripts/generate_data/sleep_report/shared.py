"""跨模块共享工具函数（非 LLM）。"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta


def _variant_pick(record_date, key, options):
    """同一指标多文案轮换，按日期稳定选取，避免条条报告雷同。"""
    if not options:
        return None
    seed = f"{record_date or ''}|{key}"
    h = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16)
    return options[h % len(options)]


def parse_time(time_str, format='%H:%M'):
    """解析时间字符串为 datetime 对象"""
    return datetime.strptime(time_str, format)


def format_time(dt, format='%H:%M'):
    """格式化 datetime 对象为时间字符串"""
    return dt.strftime(format)


def _parse_utc_iso_to_local_dt(utc_iso_str):
    """将形如 2026-04-10T15:30:00Z 的 UTC 字符串转换为本地 naive datetime(UTC+8)。"""
    if not utc_iso_str:
        return None
    dt_utc = datetime.fromisoformat(utc_iso_str.replace('Z', ''))
    return dt_utc + timedelta(hours=8)


def local_naive_dt_to_utc_iso_z(dt_local):
    """东八区墙钟 naive 时间转 UTC，输出带 Z 的 ISO 字符串（供 collected_at）。"""
    if dt_local is None:
        return ""
    dt_utc = dt_local - timedelta(hours=8)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def collected_at_to_local_naive_dt(collected_at_str):
    """解析 vitals/environment 的 collected_at：支持 UTC ISO(Z) 或历史本地 'YYYY-MM-DD HH:MM:SS'。"""
    if not collected_at_str or not str(collected_at_str).strip():
        return None
    s = str(collected_at_str).strip()
    if s.endswith("Z") or "T" in s:
        return _parse_utc_iso_to_local_dt(s)
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def format_sleep_event_local_timestamp(dt_local):
    """睡眠事件 event_timestamp：本地「几点几分」（与体征/环境同一墙钟时刻的时分）。"""
    if dt_local is None:
        return ""
    return dt_local.strftime("%H:%M")


def parse_sleep_event_timestamp_to_dt(event):
    """将睡眠事件的 event_timestamp 转为可排序的 datetime（本地语义）。"""
    ts = (event.get("event_timestamp") or "").strip()
    rd = event.get("record_date") or ""
    if len(ts) >= 16 and ts[4] == "-" and (" " in ts or "T" in ts):
        try:
            return datetime.strptime(ts.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    if len(ts) == 5 and ts[2] == ":":
        try:
            return datetime.strptime(f"{rd} {ts}", "%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return datetime.min


def session_anchor_event_local_dt(event, sleep_start=None, window_end=None):
    """
    event.record_date 与健康记录日一致（跨午夜仍用入睡当日）；event_timestamp（HH:MM）
    通过 ±1 日候选对齐到睡眠窗内的本地绝对时间。
    无睡眠窗信息时，退化为 record_date 当日 + 时分。
    """
    ts = (event.get("event_timestamp") or "").strip()
    rd = event.get("record_date") or ""
    if len(ts) >= 16 and ts[4] == "-" and (" " in ts or "T" in ts):
        try:
            return datetime.strptime(ts.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    if len(ts) != 5 or ts[2] != ":" or not rd:
        return parse_sleep_event_timestamp_to_dt(event)
    try:
        tpart = datetime.strptime(ts, "%H:%M").time()
        d0 = datetime.strptime(rd[:10], "%Y-%m-%d").date()
        c0 = datetime.combine(d0, tpart)
    except ValueError:
        return parse_sleep_event_timestamp_to_dt(event)
    if sleep_start is None or window_end is None:
        return c0
    candidates = [c0, c0 + timedelta(days=1), c0 - timedelta(days=1)]
    in_win = [c for c in candidates if sleep_start <= c <= window_end]
    if len(in_win) == 1:
        return in_win[0]
    if len(in_win) > 1:
        return min(in_win)
    return min(candidates, key=lambda c: abs((c - sleep_start).total_seconds()))
