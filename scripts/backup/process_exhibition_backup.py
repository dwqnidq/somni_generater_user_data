#!/usr/bin/env python3
"""展会备份数据处理流水线：复制 → 缩短 discover → 补齐 LLM 缺口 → 生成 _en。

只读源目录 backup/somni_all_data_展会版本/，全部产物写入新目录（默认 backup/somni_all_data_展会版本_processed/）。

用法（项目根目录）:
  python scripts/backup/process_exhibition_backup.py
  python scripts/backup/process_exhibition_backup.py --steps copy,shorten --dry-run
  python scripts/backup/process_exhibition_backup.py --steps regenerate-ai --uid 69aea6d8af5e6cbf08027966
  python scripts/backup/process_exhibition_backup.py --steps regenerate-report --uid 69aea6e3af5e6cbf08027967 --report-date 2026-06-06
  python scripts/backup/process_exhibition_backup.py --steps regenerate-ai --dry-run
  python scripts/backup/process_exhibition_backup.py --steps translate --dry-run
  python scripts/backup/process_exhibition_backup.py --all --force

步骤:
  copy              — 从源目录复制到输出目录
  shorten           — 缩短 sleep_report.hidden_discovery 文案并补 icon
  regenerate-report — 重生成指定 uid+日期的 sleep_report 整份（非 LLM 骨架 + LLM + write_back，需 API）
  regenerate-ai     — 全量重生成 ai_analysis_14d + fusion_insight（两个 prompt，需 API）
  fill              — 仅补齐缺失的 ai / hidden_discovery（需 API）
  translate         — 复制 *_en 并翻译（需 API）
"""

from __future__ import annotations

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.backup.exhibition.fill_gaps import fill_gaps_for_all, regenerate_ai_for_all  # noqa: E402
from scripts.backup.exhibition.regenerate_sleep_report_day import (  # noqa: E402
    regenerate_sleep_report_day_for_all,
)
from scripts.backup.exhibition.shared import (  # noqa: E402
    DEFAULT_OUTPUT_REL,
    DEFAULT_SOURCE_REL,
    copy_source_tree,
    resolve_path,
)
from scripts.backup.exhibition.shorten_discovery import shorten_all_sleep_reports  # noqa: E402
from scripts.backup.exhibition.translate_en import translate_exhibition_dir  # noqa: E402

ALL_STEPS = ("copy", "shorten", "regenerate-report", "regenerate-ai", "fill", "translate")


def _parse_steps(raw: str) -> tuple[str, ...]:
    if not raw.strip():
        return ALL_STEPS
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    unknown = [p for p in parts if p not in ALL_STEPS]
    if unknown:
        raise ValueError(f"未知步骤: {unknown}，可选: {ALL_STEPS}")
    return tuple(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-dir", default=DEFAULT_SOURCE_REL, help="只读源目录")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_REL, help="产物输出目录")
    parser.add_argument("--steps", default="", help=f"逗号分隔，默认全部: {','.join(ALL_STEPS)}")
    parser.add_argument("--all", action="store_true", help="等价于 --steps copy,shorten,fill,translate")
    parser.add_argument("--uid", default="", help="仅处理指定 uid")
    parser.add_argument("--start-date", default="2026-06-01")
    parser.add_argument("--end-date", default="2026-06-30")
    parser.add_argument(
        "--report-date",
        default="",
        help="regenerate-report 步骤的目标 record_date（YYYY-MM-DD）；默认与 --start-date 相同",
    )
    parser.add_argument("--weather-json", default="", help="ai_analysis 用天气 JSON")
    parser.add_argument("--force", action="store_true", help="输出目录已存在时覆盖（copy 步骤）")
    parser.add_argument("--dry-run", action="store_true", help="只统计/预览，不写文件、不调 LLM")
    parser.add_argument("--skip-ai", action="store_true", help="fill 步骤跳过 ai_analysis_14d")
    parser.add_argument("--skip-discovery", action="store_true", help="fill 步骤跳过 hidden_discovery")
    args = parser.parse_args()

    source_dir = resolve_path(args.source_dir, DEFAULT_SOURCE_REL)
    output_dir = resolve_path(args.output_dir, DEFAULT_OUTPUT_REL)
    steps = ALL_STEPS if args.all or not args.steps.strip() else _parse_steps(args.steps)
    uid = args.uid.strip() or None
    weather_json = args.weather_json.strip() or None
    summary: dict[str, object] = {"source": source_dir, "output": output_dir, "steps": steps}

    if "copy" in steps:
        if args.dry_run:
            summary["copy"] = {"action": "would_copy", "source": source_dir, "output": output_dir}
        else:
            copy_source_tree(source_dir, output_dir, force=args.force)
            summary["copy"] = {"copied": True}

    work_dir = output_dir
    if not os.path.isdir(work_dir):
        if args.dry_run and os.path.isdir(source_dir):
            work_dir = source_dir
            summary["_note"] = "dry-run 使用源目录预览（输出目录尚未创建）"
        else:
            raise FileNotFoundError(f"输出目录不存在，请先运行 copy 步骤: {output_dir}")

    if "shorten" in steps:
        stats = shorten_all_sleep_reports(work_dir, uid=uid, dry_run=args.dry_run)
        summary["shorten"] = stats

    if "regenerate-report" in steps:
        if not uid:
            raise ValueError("regenerate-report 步骤须指定 --uid")
        report_date = args.report_date.strip() or args.start_date.strip()
        if not report_date:
            raise ValueError("regenerate-report 步骤须指定 --report-date 或 --start-date")
        reg_report_stats = regenerate_sleep_report_day_for_all(
            work_dir,
            record_date=report_date,
            uid=uid,
            retry_delay=0.5,
            dry_run=args.dry_run,
        )
        summary["regenerate-report"] = reg_report_stats

    if "regenerate-ai" in steps:
        reg_stats = regenerate_ai_for_all(
            work_dir,
            uid=uid,
            start_date=args.start_date,
            end_date=args.end_date,
            weather_json=weather_json,
            retry_delay=0.5,
            dry_run=args.dry_run,
        )
        summary["regenerate-ai"] = reg_stats

    if "fill" in steps:
        fill_stats = fill_gaps_for_all(
            work_dir,
            uid=uid,
            start_date=args.start_date,
            end_date=args.end_date,
            weather_json=weather_json,
            skip_ai=args.skip_ai,
            skip_discovery=args.skip_discovery,
            dry_run=args.dry_run,
        )
        summary["fill"] = fill_stats

    if "translate" in steps:
        tr_stats = translate_exhibition_dir(work_dir, dry_run=args.dry_run)
        summary["translate"] = tr_stats

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
