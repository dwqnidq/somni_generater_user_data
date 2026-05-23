"""merge_snore_audios 单元测试。"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WRITE_BACK = os.path.join(PROJECT_ROOT, "scripts", "generate_data", "write_back")
if _WRITE_BACK not in sys.path:
    sys.path.insert(0, _WRITE_BACK)

from merge_snore_audios import merge_snore_audios_in_list  # noqa: E402


def test_merge_snore_by_time_keeps_non_snore_order():
    audios = [
        {"type": "Cough", "time": "01:00", "url": "c1.wav", "duration_sec": 2},
        {"type": "Snore", "time": "03:28", "url": "a.wav", "duration_sec": 3},
        {"type": "Snore", "time": "03:28", "url": "b.wav", "duration_sec": 3},
        {"type": "Somniloquy", "time": "04:00", "url": "t.wav", "duration_sec": 5},
        {"type": "Snore", "time": "03:29", "url": "c.wav", "duration_sec": 3},
        {"type": "Snore", "time": "03:29", "url": "d.wav", "duration_sec": 3},
        {"type": "Snore", "time": "03:29", "url": "e.wav", "duration_sec": 3},
    ]
    merged, removed = merge_snore_audios_in_list(audios)
    assert removed == 3
    assert len(merged) == 4
    assert merged[0]["type"] == "Cough"
    assert merged[1] == {
        "url": "a.wav",
        "duration_sec": 6,
        "type": "Snore",
        "time": "03:28",
    }
    assert merged[2]["type"] == "Somniloquy"
    assert merged[3] == {
        "url": "c.wav",
        "duration_sec": 9,
        "type": "Snore",
        "time": "03:29",
    }


def test_snore_without_time_unchanged():
    audios = [
        {"type": "Snore", "url": "x.wav", "duration_sec": 3},
        {"type": "Snore", "time": "01:00", "url": "a.wav", "duration_sec": 3},
        {"type": "Snore", "time": "01:00", "url": "b.wav", "duration_sec": 3},
    ]
    merged, removed = merge_snore_audios_in_list(audios)
    assert removed == 1
    assert merged[0]["url"] == "x.wav"
    assert merged[1]["duration_sec"] == 6


def test_no_snore_duplicates_unchanged():
    audios = [{"type": "Snore", "time": "01:00", "url": "a.wav", "duration_sec": 3}]
    merged, removed = merge_snore_audios_in_list(audios)
    assert removed == 0
    assert merged == audios
