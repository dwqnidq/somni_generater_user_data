#!/usr/bin/env python3
"""
仅更新睡眠报告中的打鼾曲线：扫描 output 下 *_sleep_report.json，
结合同用户的 health_data、sleep_events、environment_data 在内存中重算 data_points，
只写回 quality_analysis.auditory.snoring_analysis.data_points，不修改 audios、module 等其余字段。

计算方式与 generate_health_data.build_snoring_analysis_data_points 一致
（由重建的 audios 推导，不落盘 audios）。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
_SCRIPTS_GEN = os.path.join(PROJECT_ROOT, "scripts", "generate_data")
if _SCRIPTS_GEN not in sys.path:
    sys.path.insert(0, _SCRIPTS_GEN)
os.chdir(PROJECT_ROOT)

import generate_health_data as gh  # noqa: E402
from utils import atomic_write_json  # noqa: E402


def _user_id_from_sleep_report_path(path: str) -> str:
    base = os.path.basename(path)
    return base.replace("_sleep_report.json", "")


def refresh_one_sleep_report_file(
    report_path: str,
    *,
    output_dir: str = "output",
    dry_run: bool = False,
) -> tuple[bool, int]:
    """
    仅更新每个报告日下的 snoring_analysis.data_points，其余 JSON 字段保持不变。
    返回 (是否写盘或 dry_run 下本会写盘, 处理的报告条数)。
    """
    user_id = _user_id_from_sleep_report_path(report_path)
    with open(report_path, "r", encoding="utf-8") as f:
        sleep_reports = json.load(f)
    if not isinstance(sleep_reports, list):
        return False, 0

    sleep_events_path = os.path.join(output_dir, f"{user_id}_sleep_events.json")
    if not os.path.exists(sleep_events_path):
        print(f"  [跳过] 无睡眠事件文件: {sleep_events_path}")
        return False, 0
    with open(sleep_events_path, "r", encoding="utf-8") as f:
        sleep_events = json.load(f)
    if not isinstance(sleep_events, list):
        print(f"  [跳过] sleep_events 非数组: {sleep_events_path}")
        return False, 0

    health_by_date = gh._health_data_by_record_date(user_id, output_dir=output_dir)
    env_rows_by_date = gh.index_environment_noise_rows_by_record_date(
        user_id, output_dir=output_dir
    )

    updated_n = 0
    for report in sleep_reports:
        if not isinstance(report, dict):
            continue
        record_date = report.get("record_date")
        if not record_date:
            continue
        sleep_day = health_by_date.get(record_date) or {}
        _audios, dps = gh.rebuild_auditory_audios_and_snoring_data_points(
            str(record_date),
            user_id,
            sleep_events,
            sleep_day,
            env_rows_by_date.get(str(record_date), []),
        )
        qa = report.setdefault("quality_analysis", {})
        aud = qa.setdefault("auditory", {})
        sa = aud.setdefault("snoring_analysis", {})
        if not isinstance(sa, dict):
            sa = {}
            aud["snoring_analysis"] = sa
        sa["data_points"] = dps
        updated_n += 1

    if updated_n == 0:
        return False, 0
    if dry_run:
        print(f"  [dry-run] 将写回 {report_path}（共 {updated_n} 条报告日）")
        return True, updated_n
    atomic_write_json(report_path, sleep_reports)
    print(f"  已写回 {report_path}（{updated_n} 条 record_date）")
    return True, updated_n


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user",
        dest="user_id",
        default=None,
        help="仅处理该 user_id 对应的 output/{id}_sleep_report.json",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="睡眠报告所在目录（相对项目根），默认 output",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要处理的文件，不写盘",
    )
    args = parser.parse_args()

    out_dir = args.output_dir.strip() or "output"
    pattern = os.path.join(out_dir, "*_sleep_report.json")
    paths = sorted(glob.glob(pattern))
    if args.user_id:
        uid = str(args.user_id).strip()
        one = os.path.join(out_dir, f"{uid}_sleep_report.json")
        paths = [one] if os.path.isfile(one) else []

    if not paths:
        print(f"未找到匹配文件: {pattern}")
        return

    total_files = 0
    total_days = 0
    for p in paths:
        print(f"处理: {p}")
        ok, n = refresh_one_sleep_report_file(
            p, output_dir=out_dir, dry_run=bool(args.dry_run)
        )
        if ok:
            total_files += 1
            total_days += n

    print(f"完成：更新 {total_files} 个文件，合计 {total_days} 条日报告。")


if __name__ == "__main__":
    main()
