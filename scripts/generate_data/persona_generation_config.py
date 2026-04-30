"""从 config/health_data_personas_config.json 读取根级 generation 并与单人格覆盖合并。"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

DEFAULT_GENERATION: dict[str, Any] = {
    "sample_interval_sec": 60,
    "day_state_mix": {
        "mode": "mixed",
        "good_ratio": 0.5,
        "max_bad_days_per_week": 3,
    },
    "environment": {
        "continuous_noise_db_min": 35,
        "sudden_noise_db_min": 45,
        "temperature_bedroom_range": [22, 28],
        "humidity_range": [40, 65],
        "illuminance_sleep_range": [0, 5],
        "sleep_onset_hot_temp_c": 28,
        "sleep_pressure_noise_min": 60,
    },
    "vitals": {
        "night_hr_abnormal_min": 80,
        "baseline_minutes_after_sleep_onset": 15,
    },
    "event_triggers": {},
    "event_feedback": {
        "window_half_minutes": 5,
        "insert_gap_sec": 5,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_personas_config(config_path: str | None = None) -> dict:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = config_path or os.path.join(root, "config", "health_data_personas_config.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def merge_generation(cfg: dict, persona: dict | None = None) -> dict:
    root_gen = cfg.get("generation") or {}
    merged = _deep_merge(DEFAULT_GENERATION, root_gen)
    if persona and isinstance(persona.get("generation"), dict):
        merged = _deep_merge(merged, persona["generation"])
    return merged
