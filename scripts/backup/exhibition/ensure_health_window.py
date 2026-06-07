"""为 14 天 LLM 窗口补齐缺失 health（复用 main.py 步骤 1 同款 generate_persona_health_data）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT = os.path.abspath(os.path.join(BACKUP_DIR, "..", ".."))
GEN_DATA_DIR = os.path.join(PROJECT_ROOT, "scripts", "generate_data")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "health_data_personas_config.json")

for _p in (PROJECT_ROOT, GEN_DATA_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import generate_health_data_by_persona_config as health_gen_module  # noqa: E402
from generate_health_data_by_persona_config import generate_persona_health_data  # noqa: E402

from scripts.backup.exhibition.shared import (  # noqa: E402
    DATE_RANGE_END,
    DATE_RANGE_START,
    dates_in_range,
    health_dates,
    index_by_record_date,
    load_json_list,
    save_json_list,
)

# 与 main.py WARMUP_DAYS 一致
WARMUP_DAYS = 14
WINDOW_DAYS = 14
DEFAULT_STATE_MODE = "mixed"
DEFAULT_GOOD_RATIO = 0.5


def fourteen_day_window(anchor: str) -> tuple[str, str]:
    end_dt = datetime.strptime(anchor[:10], "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=WINDOW_DAYS - 1)
    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


def load_persona(uid: str) -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    for persona in config.get("personas") or []:
        if str(persona.get("user_id")) == uid:
            return persona
    raise ValueError(f"config 中未找到 uid={uid}")


def _warmup_start(official_start: date) -> date:
    return official_start - timedelta(days=WARMUP_DAYS)


def _contiguous_ranges(sorted_dates: list[str]) -> list[tuple[date, date]]:
    if not sorted_dates:
        return []
    ranges: list[tuple[date, date]] = []
    start = end = datetime.strptime(sorted_dates[0], "%Y-%m-%d").date()
    for ds in sorted_dates[1:]:
        cur = datetime.strptime(ds, "%Y-%m-%d").date()
        if cur == end + timedelta(days=1):
            end = cur
            continue
        ranges.append((start, end))
        start = end = cur
    ranges.append((start, end))
    return ranges


def _generate_health_via_main(
    persona: dict,
    start_d: date,
    end_d: date,
    *,
    state_mode: str = DEFAULT_STATE_MODE,
    good_ratio: float = DEFAULT_GOOD_RATIO,
) -> list[dict[str, Any]]:
    """调用 main.py 步骤 1 同款 generate_persona_health_data，写入临时目录后读回。"""
    with tempfile.TemporaryDirectory(prefix="exhibition_health_") as tmp:
        old_out = health_gen_module.OUTPUT_DIR
        try:
            health_gen_module.OUTPUT_DIR = tmp
            out_path = generate_persona_health_data(
                persona,
                state_mode=state_mode,
                good_ratio=good_ratio,
                start_date_override=start_d,
                end_date_override=end_d,
                overwrite=True,
            )
        finally:
            health_gen_module.OUTPUT_DIR = old_out
        if not out_path or not os.path.isfile(out_path):
            return []
        with open(out_path, "r", encoding="utf-8") as f:
            rows = json.load(f)
        if not isinstance(rows, list):
            return []
        return [r for r in rows if isinstance(r, dict)]


def collect_missing_for_anchors(
    uid: str,
    data_dir: str,
    anchors: list[str],
) -> list[str]:
    existing = health_dates(uid, data_dir)
    needed: set[str] = set()
    for anchor in anchors:
        start, end = fourteen_day_window(anchor)
        for d in dates_in_range(start, end):
            if d not in existing:
                needed.add(d)
    return sorted(needed)


def ensure_health_records(
    uid: str,
    data_dir: str,
    missing_dates: list[str],
    *,
    dry_run: bool = False,
    state_mode: str = DEFAULT_STATE_MODE,
    good_ratio: float = DEFAULT_GOOD_RATIO,
) -> list[str]:
    """用 main.py 同款 health 生成器造缺失日，合并进 processed 目录。"""
    if not missing_dates:
        return []
    if dry_run:
        return missing_dates

    persona = load_persona(uid)
    path = os.path.join(data_dir, f"{uid}_health_data.json")
    by_date = index_by_record_date(load_json_list(path))
    added: list[str] = []

    for start_d, end_d in _contiguous_ranges(missing_dates):
        new_rows = _generate_health_via_main(
            persona,
            start_d,
            end_d,
            state_mode=state_mode,
            good_ratio=good_ratio,
        )
        for rec in new_rows:
            rd = str(rec.get("record_date") or "")
            if not rd or rd in by_date:
                continue
            by_date[rd] = rec
            added.append(rd)

    if added:
        merged = sorted(by_date.values(), key=lambda r: str(r.get("record_date") or ""))
        save_json_list(path, merged)
    return sorted(added)


def ensure_health_for_anchors(
    uid: str,
    data_dir: str,
    anchors: list[str],
    *,
    dry_run: bool = False,
) -> list[str]:
    missing = collect_missing_for_anchors(uid, data_dir, anchors)
    return ensure_health_records(uid, data_dir, missing, dry_run=dry_run)


def ensure_fourteen_day_health(
    uid: str,
    data_dir: str,
    anchor: str,
    *,
    dry_run: bool = False,
) -> list[str]:
    return ensure_health_for_anchors(uid, data_dir, [anchor], dry_run=dry_run)


def ensure_june_warmup_health(
    uid: str,
    data_dir: str,
    *,
    dry_run: bool = False,
) -> list[str]:
    """6/1～6/30 正式区间 + main.py 同款向前 14 天缓冲（5/19 起）。"""
    official_start = datetime.strptime(DATE_RANGE_START, "%Y-%m-%d").date()
    official_end = datetime.strptime(DATE_RANGE_END, "%Y-%m-%d").date()
    warmup_start = _warmup_start(official_start)
    existing = health_dates(uid, data_dir)
    missing = [
        d
        for d in dates_in_range(warmup_start.isoformat(), official_end.isoformat())
        if d not in existing
    ]
    return ensure_health_records(uid, data_dir, missing, dry_run=dry_run)
