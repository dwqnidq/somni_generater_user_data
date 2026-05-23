"""相邻分钟 Snore audios 合并。"""

from __future__ import annotations

import os
import sys

_WRITE_BACK = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "generate_data",
    "write_back",
)
if _WRITE_BACK not in sys.path:
    sys.path.insert(0, _WRITE_BACK)

from merge_snore_audios import merge_adjacent_minute_snore_audios_in_list  # noqa: E402


def test_merge_consecutive_minutes_sums_duration():
    audios = [
        {"type": "Snore", "time": "03:28", "duration_sec": 24, "url": "a"},
        {"type": "Snore", "time": "03:29", "duration_sec": 24, "url": "b"},
        {"type": "Snore", "time": "03:30", "duration_sec": 24, "url": "c"},
        {"type": "Snore", "time": "05:35", "duration_sec": 12, "url": "d"},
    ]
    out, removed = merge_adjacent_minute_snore_audios_in_list(audios, bedtime_min=0)
    snores = [a for a in out if a.get("type") == "Snore"]
    assert len(snores) == 2
    assert snores[0] == {
        "type": "Snore",
        "time": "03:28",
        "duration_sec": 72,
        "url": "a",
    }
    assert snores[1]["time"] == "05:35"
    assert removed == 2
