"""fix_quiz_personalities_light_cw_ww"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = ROOT / "scripts" / "fix_quiz_personalities_light_cw_ww.py"
_spec = importlib.util.spec_from_file_location("fix_qp_light", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

replace_leading_kelvin_only = _mod.replace_leading_kelvin_only
resolve_kelvin_and_description = _mod.resolve_kelvin_and_description
scan_periods_for_updates = _mod.scan_periods_for_updates
build_mongo_set_payload = _mod.build_mongo_set_payload


def test_replace_k_only():
    assert replace_leading_kelvin_only("2500k 暖黄光 10→8lux", 2700) == "2700k 暖黄光 10→8lux"
    assert replace_leading_kelvin_only("2000K 深暖光", 2700) == "2700K 深暖光"


def test_resolve_raises_low_k():
    k, desc, raised = resolve_kelvin_and_description("2200k 暖黄光 5→0lux")
    assert raised is True
    assert k == 2700
    assert desc == "2700k 暖黄光 5→0lux"


def test_low_k_updates_description_and_cw_ww():
    periods = [
        {
            "phase": "relax",
            "schemes": [
                {
                    "name": "放松专属",
                    "light": {
                        "description": "2500k 暖黄光",
                        "color_stops": [{"cw": 0, "ww": 0, "r": 1}],
                    },
                }
            ],
        }
    ]
    desc_u, cw_u, changes, skipped = scan_periods_for_updates(periods)
    assert skipped == []
    assert len(desc_u) == 1
    assert len(changes) == 1
    payload = build_mongo_set_payload(desc_u, cw_u)
    assert payload["periods.0.schemes.0.light.description"] == "2700k 暖黄光"
    assert payload["periods.0.schemes.0.light.color_stops.0.ww"] == 255
    assert periods[0]["schemes"][0]["light"]["description"] == "2500k 暖黄光"


def test_no_k_still_skipped():
    periods = [
        {
            "phase": "guard",
            "schemes": [
                {
                    "name": "默认",
                    "light": {
                        "description": "灯光关闭",
                        "color_stops": [{"cw": 0, "ww": 0}],
                    },
                }
            ],
        }
    ]
    desc_u, cw_u, changes, skipped = scan_periods_for_updates(periods)
    assert desc_u == [] and cw_u == [] and changes == []
    assert len(skipped) == 1


if __name__ == "__main__":
    test_replace_k_only()
    test_resolve_raises_low_k()
    test_low_k_updates_description_and_cw_ww()
    test_no_k_still_skipped()
    print("all passed")
