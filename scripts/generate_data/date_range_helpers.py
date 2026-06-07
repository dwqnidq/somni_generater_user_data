"""人格 date_range 与 CLI / 缓冲起始日覆盖的合并规则。"""

from __future__ import annotations

from datetime import date


def apply_date_range_overrides(
    config_start: date,
    config_end: date,
    start_override: date | None,
    end_override: date | None,
) -> tuple[date, date] | None:
    """合并配置区间与覆盖参数。

    - ``start_override`` 早于配置 ``start``：向前扩展（14 日缓冲）。
    - ``start_override`` 晚于或等于配置 ``start``：与配置求交，用于 CLI 收窄。
    - ``end_override``：与配置 ``end`` 取较早者（收窄结束日）。
    """
    start_dt = config_start
    end_dt = config_end
    if start_override:
        if start_override < start_dt:
            start_dt = start_override
        else:
            start_dt = max(start_dt, start_override)
    if end_override:
        end_dt = min(end_dt, end_override)
    if start_dt > end_dt:
        return None
    return start_dt, end_dt
