"""睡眠报告用时间工具（与 generate_health_data 中同名函数行为对齐）。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_SLEEP_CLOCK_HHMM_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")


def calculate_duration(start_time_str, end_time_str, anchor_date_str=None):
    """计算两时刻之间的分钟数；支持 ISO(UTC) 或 idf_data 中的 HH:MM(:SS)。"""

    def to_dt(s):
        if not s or not isinstance(s, str):
            return None
        s = s.strip()
        if not s:
            return None
        if _SLEEP_CLOCK_HHMM_RE.match(s):
            if anchor_date_str:
                try:
                    base_d = datetime.strptime(anchor_date_str[:10], "%Y-%m-%d").date()
                except ValueError:
                    base_d = datetime(2000, 1, 1).date()
            else:
                base_d = datetime(2000, 1, 1).date()
            if len(s) <= 5:
                tm = datetime.strptime(s, "%H:%M").time()
            else:
                tm = datetime.strptime(s, "%H:%M:%S").time()
            return datetime.combine(base_d, tm)
        try:
            return datetime.fromisoformat(s.replace("Z", ""))
        except ValueError:
            return None

    start_time = to_dt(start_time_str)
    end_time = to_dt(end_time_str)
    if start_time is None or end_time is None:
        return 0

    if end_time < start_time:
        end_time += timedelta(days=1)

    delta = end_time - start_time
    return int(delta.total_seconds() / 60)


def utc_to_local(utc_time_str):
    """将 UTC ISO 字符串转为本地 naive datetime；若为纯 HH:MM(:SS) 则视为已是本地墙钟。"""
    if not utc_time_str or not isinstance(utc_time_str, str):
        return datetime.now()
    s = utc_time_str.strip()
    if not s:
        return datetime.now()
    if _SLEEP_CLOCK_HHMM_RE.match(s):
        base_d = datetime(2000, 1, 1).date()
        if len(s) <= 5:
            tm = datetime.strptime(s, "%H:%M").time()
        else:
            tm = datetime.strptime(s, "%H:%M:%S").time()
        return datetime.combine(base_d, tm)
    utc_time = datetime.fromisoformat(s.replace("Z", ""))
    return utc_time + timedelta(hours=8)


def format_time_to_hhmm(dt):
    """将 datetime 格式化为 HH:MM。"""
    return dt.strftime("%H:%M")


def minutes_between_datetimes(start_dt, end_dt):
    """两时刻之间的整分钟数（可跨日）。"""
    e = end_dt
    if e < start_dt:
        e += timedelta(days=1)
    return int((e - start_dt).total_seconds() / 60)


def generate_iso_date():
    """生成 ISODate 风格字符串（与 Mongo 模拟字段一致）。"""
    return datetime.now().isoformat() + "Z"
