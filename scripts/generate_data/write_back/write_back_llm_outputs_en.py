#!/usr/bin/env python3
"""
英文 LLM 侧车 JSON 写回 output（不调模型、不写 Mongo）。

顺序：
  1. morning_alarm_insight_en → ai_analysis_14d_en.fusion_insight
  2. sleep_pattern_commonality_en + insight_en → sleep_report_en.hidden_discovery
  3. sleep_report_en 睡眠结构/环境 status 中文→英文单词

示例：
  python scripts/generate_data/write_back/write_back_llm_outputs_en.py --uid <uid>
  python scripts/generate_data/write_back/write_back_llm_outputs_en.py --all --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from _common import default_output_dir  # noqa: E402
from _en_status_maps import SUFFIX_MORNING_ALARM_INSIGHT_EN  # noqa: E402
from merge_fusion_insight_en import merge_fusion_insight_en_from_morning  # noqa: E402
from merge_hidden_discovery_en import merge_hidden_discovery_en_to_sleep_report  # noqa: E402
from merge_sleep_report_status_en import merge_sleep_report_status_en  # noqa: E402


def discover_uids_with_morning_en(output_dir: str) -> list[str]:
    output_dir = os.path.abspath(output_dir)
    suffix = SUFFIX_MORNING_ALARM_INSIGHT_EN
    uids: list[str] = []
    if not os.path.isdir(output_dir):
        return uids
    for name in os.listdir(output_dir):
        if not name.endswith(suffix):
            continue
        uids.append(name[: -len(suffix)])
    return sorted(uids)


def run_write_back_en_for_uid(
    uid: str,
    output_dir: str,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    output_dir = os.path.abspath(output_dir)
    fw, fsk = merge_fusion_insight_en_from_morning(
        uid, output_dir, start_date=start_date, end_date=end_date, dry_run=dry_run
    )
    wh, hsk = merge_hidden_discovery_en_to_sleep_report(
        uid, output_dir, start_date=start_date, end_date=end_date, dry_run=dry_run
    )
    sp, ssk = merge_sleep_report_status_en(
        uid, output_dir, start_date=start_date, end_date=end_date, dry_run=dry_run
    )
    return {
        "uid": uid,
        "output_dir": output_dir,
        "start_date": start_date,
        "end_date": end_date,
        "dry_run": dry_run,
        "fusion_insight_written": fw,
        "fusion_insight_skipped_no_ai_row": fsk,
        "hidden_discovery_written_days": wh,
        "hidden_discovery_skipped_days": hsk,
        "status_fields_patched": sp,
        "report_days_unchanged": ssk,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uid", default="", help="单个 uid；与 --all 二选一")
    p.add_argument(
        "--all",
        action="store_true",
        help=f"处理 output 下所有 *{SUFFIX_MORNING_ALARM_INSIGHT_EN} 的 uid",
    )
    p.add_argument("--output-dir", default=default_output_dir())
    p.add_argument("--start-date", default="", help="YYYY-MM-DD，仅写回该日起（含）")
    p.add_argument("--end-date", default="", help="YYYY-MM-DD，仅写回该日止（含）")
    p.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    args = p.parse_args()

    uid = str(args.uid).strip()
    if bool(uid) == bool(args.all):
        print("请指定 --uid <uid> 或 --all，且不能同时指定", file=sys.stderr)
        return 1

    start_date = args.start_date.strip() or None
    end_date = args.end_date.strip() or None
    output_dir = args.output_dir

    if uid:
        uids = [uid]
    else:
        uids = discover_uids_with_morning_en(output_dir)
        if not uids:
            print(f"未在 {output_dir!r} 找到 *{SUFFIX_MORNING_ALARM_INSIGHT_EN}", file=sys.stderr)
            return 1

    results = [
        run_write_back_en_for_uid(
            u,
            output_dir,
            start_date=start_date,
            end_date=end_date,
            dry_run=args.dry_run,
        )
        for u in uids
    ]
    print(json.dumps(results if len(results) > 1 else results[0], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
