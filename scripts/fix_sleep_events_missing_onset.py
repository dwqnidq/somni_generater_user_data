#!/usr/bin/env python3
"""为 output 中「潜伏期>30 但缺少入睡困难」的当夜补全事件及 AI 主动干预。

用法（项目根目录）:
  python scripts/fix_sleep_events_missing_onset.py --dry-run
  python scripts/fix_sleep_events_missing_onset.py
"""
from __future__ import annotations

import argparse
import json
import os
import random
import secrets
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
os.chdir(PROJECT_ROOT)


def _atomic_write_json(path: str | Path, data: object) -> None:
    path = os.path.abspath(str(path))
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

OUTPUT_DIR = PROJECT_ROOT / "output"
BLOCKED_UIDS = frozenset(
    {"69aea6e3af5e6cbf08027967", "69aea6f8af5e6cbf0802796b"}
)
LATENCY_THRESHOLD = 30


def _new_object_id() -> str:
    return secrets.token_hex(12)


def _time_to_minutes(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _minutes_to_hhmm(mins: int) -> str:
    v = mins % 1440
    return f"{v // 60:02d}:{v % 60:02d}"


def _idf_seg_minute_window(seg: dict) -> tuple[int, int] | None:
    sm = _time_to_minutes(str(seg.get("start") or seg.get("start_time") or ""))
    em = _time_to_minutes(str(seg.get("end") or seg.get("end_time") or ""))
    if sm is None or em is None:
        return None
    if em < sm:
        em += 1440
    return sm, em


def _pick_minute_in_first_onset_awake(idf_data: list, rng: random.Random) -> int | None:
    if not idf_data or (idf_data[0] or {}).get("stage") != "awake":
        return None
    win = _idf_seg_minute_window(idf_data[0])
    if not win:
        return None
    sm, em = win
    span = max(1, em - sm)
    early_limit = sm + min(int(span * 0.35), 8, span - 1)
    pick_hi = max(sm, early_limit)
    return rng.randint(sm, pick_hi) % 1440


def _utc_iso_to_local_hm(utc_iso_z: str, tz_offset_hours: int = 8) -> str | None:
    try:
        utc_dt = datetime.strptime(utc_iso_z, "%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None
    local_dt = utc_dt + timedelta(hours=tz_offset_hours)
    return local_dt.strftime("%H:%M")


def _health_records(health: object) -> list[dict]:
    if isinstance(health, list):
        return [r for r in health if isinstance(r, dict)]
    if isinstance(health, dict):
        for key in ("records", "data", "health_data"):
            rows = health.get(key)
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
    return []


def _onset_detail_template(events: list[dict]) -> dict:
    for e in events:
        if e.get("event_type") == "入睡困难" and isinstance(e.get("detail"), dict):
            return dict(e["detail"])
    return {
        "trigger_cause": "识别到用户入睡过程存在阻碍",
        "action_taken": "启动入睡阶段状态评估",
        "result_summary": "确认为入睡阶段状态异常",
    }


def _ai_detail_template(events: list[dict]) -> dict:
    for e in events:
        if e.get("event_type") == "AI主动干预" and e.get("code") == "sleeping":
            if isinstance(e.get("detail"), dict):
                return dict(e["detail"])
    return {
        "trigger_cause": "监测到用户入睡困难的异常表现",
        "action_taken": "释放薰衣草香氛，间隔播放轻柔白噪音，开启呼吸引导光配合舒缓节奏",
        "result_summary": "有效引导用户放松，顺利进入睡眠状态",
    }


def _pick_onset_hhmm(uid: str, record_date: str, sleep_rec: dict, rng: random.Random) -> str:
    idf_data = sleep_rec.get("idf_data") or []
    stage_min = _pick_minute_in_first_onset_awake(idf_data, rng)
    if stage_min is not None:
        return _minutes_to_hhmm(stage_min)
    raw = sleep_rec.get("raw_data") or {}
    latency = int(raw.get("sleep_latency") or 0)
    sleep_t = str(raw.get("sleep_time") or "")
    stage_min = 30
    if "T" in sleep_t:
        hm_local = _utc_iso_to_local_hm(sleep_t, tz_offset_hours=8)
        start_min = _time_to_minutes(hm_local) if hm_local else None
        if start_min is not None:
            stage_min = (start_min + min(45, max(5, latency))) % 1440
    return _minutes_to_hhmm(stage_min)


def _sort_key(e: dict) -> tuple:
    return (
        str(e.get("record_date") or ""),
        _time_to_minutes(str(e.get("event_timestamp") or "")) or 9999,
        str(e.get("event_type") or ""),
    )


def fix_user(uid: str, *, dry_run: bool) -> int:
    health_path = OUTPUT_DIR / f"{uid}_health_data.json"
    events_path = OUTPUT_DIR / f"{uid}_sleep_events.json"
    if not health_path.is_file() or not events_path.is_file():
        return 0

    health = json.loads(health_path.read_text(encoding="utf-8"))
    events: list[dict] = json.loads(events_path.read_text(encoding="utf-8"))
    by_date = {
        str(e.get("record_date")): e
        for e in events
        if e.get("event_type") == "入睡困难" and e.get("record_date")
    }
    onset_tpl = _onset_detail_template(events)
    ai_tpl = _ai_detail_template(events)
    health_by_date = {
        str(r.get("record_date") or r.get("date")): r
        for r in _health_records(health)
        if r.get("record_date") or r.get("date")
    }

    added = 0
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    for record_date, sleep_rec in sorted(health_by_date.items()):
        raw = sleep_rec.get("raw_data") or {}
        if int(raw.get("sleep_latency") or 0) <= LATENCY_THRESHOLD:
            continue
        if record_date in by_date:
            continue

        rng = random.Random(f"{uid}:{record_date}:fix-onset")
        hhmm = _pick_onset_hhmm(uid, record_date, sleep_rec, rng)
        parent_min = _time_to_minutes(hhmm)
        ai_hhmm = _minutes_to_hhmm((parent_min + 2) % 1440) if parent_min is not None else hhmm
        parent_id = _new_object_id()

        onset = {
            "uid": uid,
            "record_date": record_date,
            "event_timestamp": hhmm,
            "event_type": "入睡困难",
            "type": "abnormal",
            "code": "sleeping",
            "detail": dict(onset_tpl),
            "related_event_id": "",
            "sort_order": 0,
            "create_time": ts,
            "update_time": ts,
            "duration_sec": max(60, int(raw.get("sleep_latency") or 30) * 60 // 4),
            "_id": parent_id,
            "language": "zh",
        }
        ai_event = {
            "uid": uid,
            "record_date": record_date,
            "event_timestamp": ai_hhmm,
            "event_type": "AI主动干预",
            "type": "intervention",
            "code": "sleeping",
            "detail": dict(ai_tpl),
            "related_event_id": parent_id,
            "sort_order": 0,
            "create_time": ts,
            "update_time": ts,
            "duration_sec": 15,
            "_id": _new_object_id(),
            "language": "zh",
        }
        events.extend([onset, ai_event])
        by_date[record_date] = onset
        added += 1
        print(f"  + {uid[-4:]} {record_date} latency={raw.get('sleep_latency')} @ {hhmm}")

    if added and not dry_run:
        events.sort(key=_sort_key)
        _atomic_write_json(events_path, events)

    return added


def main() -> None:
    ap = argparse.ArgumentParser(description="补全潜伏期>30 但缺失的入睡困难事件")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    total = 0
    for fp in sorted(OUTPUT_DIR.glob("*_sleep_events.json")):
        uid = fp.name.split("_sleep_events")[0]
        if uid in BLOCKED_UIDS:
            continue
        total += fix_user(uid, dry_run=args.dry_run)

    suffix = "（未写文件）" if args.dry_run else ""
    print(f"\n共补全 {total} 夜入睡困难事件{suffix}")


if __name__ == "__main__":
    main()
