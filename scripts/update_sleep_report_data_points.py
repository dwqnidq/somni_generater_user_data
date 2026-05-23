#!/usr/bin/env python3
"""
仅更新睡眠报告中的打鼾曲线：扫描 output 下 *_sleep_report.json，
结合同用户的 health_data、sleep_events 在内存中重算 data_points，
写回 quality_analysis.auditory.snoring_analysis.data_points（按分钟聚合，sleep_events.noise_db）。

同时重建 audios 与按分钟 data_points（与 main.py 步骤 7 补全一致）。
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
_WRITE_BACK = os.path.join(_SCRIPTS_GEN, "write_back")
for _p in (_SCRIPTS_GEN, _WRITE_BACK):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.chdir(PROJECT_ROOT)

from write_back.refresh_sleep_report_auditory_snoring import (  # noqa: E402
    refresh_sleep_report_auditory_snoring_for_uid,
)


def _user_id_from_sleep_report_path(path: str) -> str:
    base = os.path.basename(path)
    return base.replace("_sleep_report.json", "")


def refresh_one_sleep_report_file(
    report_path: str,
    *,
    output_dir: str = "output",
    dry_run: bool = False,
) -> tuple[bool, int]:
    """返回 (是否处理, 更新的报告日数)。"""
    user_id = _user_id_from_sleep_report_path(report_path)
    if dry_run:
        with open(report_path, "r", encoding="utf-8") as f:
            sleep_reports = json.load(f)
        n = sum(1 for r in sleep_reports if isinstance(r, dict) and r.get("record_date"))
        print(f"  [dry-run] 将刷新 {report_path}（约 {n} 条报告日）")
        return n > 0, n
    stats = refresh_sleep_report_auditory_snoring_for_uid(
        user_id, output_dir, refresh_audios=True
    )
    updated_n = int(stats.get("days_updated") or 0)
    if updated_n:
        print(f"  已写回 {report_path}（{updated_n} 条 record_date）")
    return updated_n > 0, updated_n


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
