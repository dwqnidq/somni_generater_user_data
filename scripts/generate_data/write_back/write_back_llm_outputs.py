#!/usr/bin/env python3
"""
按固定顺序依次调用各独立写回脚本（不调用模型）。

参数
  uid, output_dir：必填路径上下文。
  start_date, end_date：可选 ``YYYY-MM-DD`` 闭区间，与各 JSON 的 ``record_date`` 对齐，仅写回该范围内的日；
  与 ``main.py`` 的 ``--start-date`` / ``--end-date`` 一致时，与 LLM 批处理窗口对齐。

各步亦可单独执行，例如：
  python scripts/generate_data/write_back/write_back_llm_outputs.py --uid <uid> --start-date 2026-05-01
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

from merge_env_intervention import merge_env_intervention_to_report  # noqa: E402
from merge_fusion_insight import merge_fusion_insight_from_morning  # noqa: E402
from merge_hidden_discovery import merge_hidden_discovery_to_sleep_report  # noqa: E402
from merge_main_summary import merge_main_summary_to_report  # noqa: E402
from merge_notice import merge_notice_to_report  # noqa: E402
from merge_sleep_events_intervention import merge_sleep_events_details_from_intervention  # noqa: E402
from merge_sleep_auditory import merge_sleep_auditory_to_report  # noqa: E402
from merge_sleep_quality import merge_sleep_quality_to_report  # noqa: E402

from _common import default_output_dir  # noqa: E402


def run_write_back_for_uid(
    uid: str,
    output_dir: str,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict[str, Any]:
    """将本用户 LLM 侧车 JSON 写回 ai_analysis_14d、sleep_events、sleep_report。

    Args:
        uid: user_id。
        output_dir: 含 ``{{uid}}_*.json`` 的目录（一般为项目 ``output/``）。
        start_date: 可选，``YYYY-MM-DD``，仅处理 ``record_date`` 落在此日（含）之后的行。
        end_date: 可选，``YYYY-MM-DD``，仅处理 ``record_date`` 落在此日（含）之前的行。
    """
    output_dir = os.path.abspath(output_dir)
    stats: dict[str, Any] = {
        "uid": uid,
        "output_dir": output_dir,
        "start_date": start_date,
        "end_date": end_date,
    }

    w, sk = merge_fusion_insight_from_morning(uid, output_dir, start_date=start_date, end_date=end_date)
    stats["fusion_insight_written"] = w
    stats["fusion_insight_skipped_no_ai_row"] = sk

    wh, sh = merge_hidden_discovery_to_sleep_report(uid, output_dir, start_date=start_date, end_date=end_date)
    stats["hidden_discovery_written_days"] = wh
    stats["hidden_discovery_skipped_days"] = sh

    ud, miss = merge_sleep_events_details_from_intervention(
        uid, output_dir, start_date=start_date, end_date=end_date
    )
    stats["sleep_events_detail_updates"] = ud
    stats["sleep_events_detail_missing_id"] = miss

    tq, mq = merge_sleep_quality_to_report(uid, output_dir, start_date=start_date, end_date=end_date)
    stats["sleep_quality_appended"] = tq
    stats["sleep_quality_skipped"] = mq

    ta, ma = merge_sleep_auditory_to_report(uid, output_dir, start_date=start_date, end_date=end_date)
    stats["sleep_auditory_updated"] = ta
    stats["sleep_auditory_skipped"] = ma

    tm, mm = merge_main_summary_to_report(uid, output_dir, start_date=start_date, end_date=end_date)
    stats["main_summary_updated"] = tm
    stats["main_summary_skipped"] = mm

    tn, mn = merge_notice_to_report(uid, output_dir, start_date=start_date, end_date=end_date)
    stats["notice_updated"] = tn
    stats["notice_skipped"] = mn

    te, me = merge_env_intervention_to_report(uid, output_dir, start_date=start_date, end_date=end_date)
    stats["env_intervention_appended"] = te
    stats["env_intervention_skipped"] = me

    return stats


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--uid", required=True)
    p.add_argument("--output-dir", default=default_output_dir())
    p.add_argument("--start-date", default="", help="YYYY-MM-DD，仅写回该日起（含）")
    p.add_argument("--end-date", default="", help="YYYY-MM-DD，仅写回该日止（含）")
    args = p.parse_args()
    print(
        json.dumps(
            run_write_back_for_uid(
                args.uid.strip(),
                args.output_dir,
                start_date=args.start_date.strip() or None,
                end_date=args.end_date.strip() or None,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
