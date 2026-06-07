"""fix_somni_temp_plans_lux_spacing 单元测试"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = ROOT / "scripts" / "insert_data" / "fix_somni_temp_plans_lux_spacing.py"
_spec = importlib.util.spec_from_file_location("fix_temp_plans_lux", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

normalize_lux_in_string = _mod.normalize_lux_in_string
build_set_payload = _mod.build_set_payload


def test_attached_lux():
    assert normalize_lux_in_string("10→8lux") == "10→8 Lux"
    assert normalize_lux_in_string("≤4 lux") == "≤4 Lux"


def test_spaced_lux():
    assert normalize_lux_in_string("0 lux") == "0 Lux"
    assert normalize_lux_in_string("吸气 0 lux→4 lux") == "吸气 0 Lux→4 Lux"


def test_already_correct():
    assert normalize_lux_in_string("0 Lux") == "0 Lux"
    assert normalize_lux_in_string("10→8 Lux") == "10→8 Lux"


def test_no_double_space():
    assert "  Lux" not in normalize_lux_in_string("0 lux")
    assert normalize_lux_in_string("0 lux").count(" ") == 1


def test_build_set_payload_nested():
    doc = {
        "phases": [
            {
                "schemes": [
                    {"light": {"description": "2500k 暖黄光 10→8lux"}},
                ]
            }
        ]
    }
    payload = build_set_payload(doc)
    assert payload["phases.0.schemes.0.light.description"] == "2500k 暖黄光 10→8 Lux"
