#!/usr/bin/env python3
"""按 health_data 重算 sleep_report 中四阶段分钟数及关联 percent/status（保留 LLM 文案）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any, Optional

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from _common import (  # noqa: E402
    default_output_dir,
    ensure_sys_path,
    index_by_record_date,
    load_json_list,
    record_date_in_range,
)

ensure_sys_path()

from import_sleep_metrics import load_build_sleep_structure_metrics  # noqa: E402

build_sleep_structure_metrics = load_build_sleep_structure_metrics()


def _atomic_write_json(path: str, data: object) -> None:
    path = os.path.abspath(path)
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
        os.replace(tmp_path, path)
    except Exception:
        if os.path.isfile(tmp_path):
            os.remove(tmp_path)
        raise


def _load_sleep_standard(config_path: str) -> dict:
    if not os.path.isfile(config_path):
        return {"deep": [15, 25], "light": [45, 60], "rem": [20, 25], "awake": 10}
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    return config.get(
        "sleepStandard",
        {"deep": [15, 25], "light": [45, 60], "rem": [20, 25], "awake": 10},
    )


def _health_by_record_date(uid: str, output_dir: str) -> dict[str, dict]:
    path = os.path.join(os.path.abspath(output_dir), f"{uid}_health_data.json")
    return index_by_record_date(load_json_list(path))


def refresh_sleep_report_stage_metrics_for_uid(
    uid: str,
    output_dir: str,
    sleep_standard: dict,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """写回 sleep_summary.deep_sleep_minutes 与 quality_analysis.sleep_structure。"""
    output_dir = os.path.abspath(output_dir)
    uid = str(uid).strip()
    rep_path = os.path.join(output_dir, f"{uid}_sleep_report.json")
    report_rows = load_json_list(rep_path)
    if not report_rows:
        return {"uid": uid, "days_updated": 0, "skipped": "no_sleep_report"}

    health_by_date = _health_by_record_date(uid, output_dir)
    updated = 0
    missing_health = 0

    for report in report_rows:
        if not isinstance(report, dict):
            continue
        rd = str(report.get("record_date") or "")
        if not rd or not record_date_in_range(rd, start_date, end_date):
            continue
        health_row = health_by_date.get(rd)
        if not health_row:
            missing_health += 1
            continue

        structure, deep_minutes, *_ = build_sleep_structure_metrics(
            health_row, sleep_standard
        )
        qa = report.setdefault("quality_analysis", {})
        qa["sleep_structure"] = structure
        summary = report.setdefault("sleep_summary", {})
        summary["deep_sleep_minutes"] = deep_minutes
        updated += 1

    if updated > 0:
        _atomic_write_json(rep_path, report_rows)

    return {
        "uid": uid,
        "days_updated": updated,
        "missing_health": missing_health,
        "report_path": rep_path,
    }


def refresh_all_sleep_reports_stage_metrics(
    output_dir: str,
    config_file: str,
    uid: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    output_dir = os.path.abspath(output_dir)
    sleep_standard = _load_sleep_standard(config_file)
    pattern_uid = f"{uid}_sleep_report.json" if uid else "*_sleep_report.json"
    import glob

    paths = sorted(glob.glob(os.path.join(output_dir, pattern_uid)))
    results: list[dict[str, Any]] = []
    for path in paths:
        base = os.path.basename(path)
        file_uid = base.replace("_sleep_report.json", "")
        results.append(
            refresh_sleep_report_stage_metrics_for_uid(
                file_uid,
                output_dir,
                sleep_standard,
                start_date=start_date,
                end_date=end_date,
            )
        )
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="重算 output 睡眠报告四阶段分钟数")
    parser.add_argument("--output-dir", default=default_output_dir())
    parser.add_argument(
        "--config",
        default=os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(_SCRIPT_DIR))),
            "config",
            "config.json",
        ),
    )
    parser.add_argument("--uid", default=None, help="仅处理指定用户")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    args = parser.parse_args()

    stats = refresh_all_sleep_reports_stage_metrics(
        args.output_dir,
        args.config,
        uid=args.uid,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    total_days = sum(int(s.get("days_updated") or 0) for s in stats)
    print(f"处理 {len(stats)} 个用户，共更新 {total_days} 天")
    for s in stats:
        if s.get("skipped"):
            print(f"  {s['uid']}: 跳过 ({s['skipped']})")
        else:
            print(
                f"  {s['uid']}: 更新 {s.get('days_updated', 0)} 天"
                + (f", 缺 health {s['missing_health']} 天" if s.get("missing_health") else "")
            )
