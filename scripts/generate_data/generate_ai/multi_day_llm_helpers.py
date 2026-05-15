"""多天 LLM 步骤共用的日期窗口与「有效起始日」计算。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional


def backward_14_health_complete(anchor: str, health_dates: set[str]) -> bool:
    """锚点日及向前共 14 个自然日是否均在 health_dates 中有记录。"""
    try:
        d0 = datetime.strptime(anchor[:10], "%Y-%m-%d")
    except ValueError:
        return False
    for i in range(14):
        ds = (d0 - timedelta(days=i)).strftime("%Y-%m-%d")
        if ds not in health_dates:
            return False
    return True


def merge_last_skipped(prev: Optional[str], day: str) -> str:
    """在已处理的跳过日中取日历最晚的一天（YYYY-MM-DD 字符串比较即可）。"""
    if not prev:
        return day
    return day if day > prev else prev


def min_date_str(dates: Iterable[Optional[str]]) -> Optional[str]:
    """若干 YYYY-MM-DD 中取最小；忽略 None / 空串。"""
    xs = [d for d in dates if d]
    if not xs:
        return None
    return min(xs)


def plus_one_day(day: str) -> str:
    return (datetime.strptime(day[:10], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def max_date_str(a: Optional[str], b: Optional[str]) -> Optional[str]:
    if not a:
        return b
    if not b:
        return a
    return a if a > b else b


def apply_max_records(items: list, max_records: Optional[int]) -> list:
    """在日期/行列表已按规则排序并过滤后，仅保留前 ``max_records`` 条。

    ``max_records`` 为 ``None`` 或 ``<= 0`` 时不截断。
    """
    if max_records is None or max_records <= 0:
        return items
    return items[:max_records]


def effective_start_after_skips(
    last_skipped_dates: list[Optional[str]],
    range_start: Optional[str],
) -> Optional[str]:
    """取各脚本「最后跳过日」的日历最早者，再取其次日；无跳过则返回 range_start。"""
    m = min_date_str(last_skipped_dates)
    if not m:
        return range_start
    nxt = plus_one_day(m)
    if range_start and nxt < range_start:
        return range_start
    return nxt
