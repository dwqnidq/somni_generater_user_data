"""merge_snoring_data_points 单元测试。"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WRITE_BACK = os.path.join(PROJECT_ROOT, "scripts", "generate_data", "write_back")
if _WRITE_BACK not in sys.path:
    sys.path.insert(0, _WRITE_BACK)

from merge_snoring_data_points import (  # noqa: E402
    build_snoring_data_points_from_events,
    hhmm_to_minutes,
    normalize_hhmm,
)


def _snore(ts: str, db: float, **extra) -> dict:
    return {
        "event_type": "打鼾",
        "code": "snoring",
        "event_timestamp": ts,
        "noise_db": db,
        **extra,
    }


def test_normalize_hhmm():
    assert normalize_hhmm("4:12") == "04:12"
    assert normalize_hhmm("04:12:30") == "04:12"


def test_average_per_minute_rounds_to_int():
    events = [
        _snore("04:12", 1),
        _snore("04:12", 2),
        _snore("04:12", 3),
        _snore("04:12", 4),
        _snore("04:13", 10),
    ]
    dps = build_snoring_data_points_from_events(events)
    assert len(dps) == 2
    assert dps[0] == {"time": "04:12", "value": 3}
    assert dps[1] == {"time": "04:13", "value": 10}


def test_session_sort_with_bedtime():
    events = [
        _snore("03:39", 50),
        _snore("01:22", 40),
        _snore("01:23", 42),
    ]
    bed = hhmm_to_minutes("01:08")
    dps = build_snoring_data_points_from_events(events, bedtime_min=bed)
    assert [p["time"] for p in dps] == ["01:22", "01:23", "03:39"]


def test_skips_missing_noise_db():
    events = [_snore("04:12", 40), {"event_type": "打鼾", "event_timestamp": "04:13"}]
    dps = build_snoring_data_points_from_events(events)
    assert dps == [{"time": "04:12", "value": 40}]
