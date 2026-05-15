"""与 generate_health_data 同名时间函数对齐校验（sleep_report 包第一步）。"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN_DIR = os.path.join(ROOT, "scripts", "generate_data")
if GEN_DIR not in sys.path:
    sys.path.insert(0, GEN_DIR)

import generate_health_data as gh  # noqa: E402
from sleep_report import (  # noqa: E402
    calculate_duration,
    format_time_to_hhmm,
    generate_iso_date,
    utc_to_local,
)


def test_calculate_duration_iso():
    assert calculate_duration(
        "2026-03-15T22:00:00",
        "2026-03-16T06:00:00",
    ) == gh.calculate_duration("2026-03-15T22:00:00", "2026-03-16T06:00:00")


def test_calculate_duration_cross_midnight_hhmm():
    assert calculate_duration("23:50", "00:15", "2026-03-15") == gh.calculate_duration(
        "23:50", "00:15", "2026-03-15"
    )


def test_calculate_duration_empty():
    assert calculate_duration("", "") == 0


def test_utc_to_local_z():
    s = "2026-03-15T22:00:00Z"
    assert utc_to_local(s) == gh.utc_to_local(s)


def test_utc_to_local_hhmm_passthrough_shape():
    s = "6:30"
    assert utc_to_local(s) == gh.utc_to_local(s)


def test_format_time_to_hhmm():
    dt = utc_to_local("2026-03-15T22:00:00Z")
    assert format_time_to_hhmm(dt) == gh.format_time_to_hhmm(dt)


def test_generate_iso_date_suffix():
    out = generate_iso_date()
    assert isinstance(out, str)
    assert out.endswith("Z")
    assert "T" in out
