"""build_snoring_data_points_per_minute：按分钟取自 sleep_events.noise_db。"""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GEN = os.path.join(PROJECT_ROOT, "scripts", "generate_data")
_WRITE_BACK = os.path.join(_GEN, "write_back")
for _p in (_GEN, _WRITE_BACK):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sleep_report.auditory import build_snoring_data_points_per_minute  # noqa: E402


def _snore(ts: str, db: float) -> dict:
    return {
        "event_type": "打鼾",
        "code": "snoring",
        "event_timestamp": ts,
        "noise_db": db,
    }


def test_average_per_minute():
    events = [
        _snore("03:28", 37),
        _snore("03:28", 39),
        _snore("03:29", 55),
    ]
    dps = build_snoring_data_points_per_minute(events)
    assert len(dps) == 2
    assert dps[0] == {"time": "03:28", "value": 38}
    assert dps[1] == {"time": "03:29", "value": 55}
