#!/usr/bin/env python3
"""单条预览：main.title + main.summary（与 generate_sleep_main_summary 共用生成逻辑）。"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from _shared import (
    PROJECT_ROOT,
    base_arg_parser,
    bootstrap,
    default_out_path,
    force_doubao_env,
    load_health_row,
    load_sleep_report_main_title,
    write_result,
)

bootstrap()
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
force_doubao_env()

from generate_ai.runtime import bootstrap_llm  # noqa: E402
from generate_sleep_main_summary import generate_main_summary_for_date  # noqa: E402
from sleep_report.sleep_helpers import build_sleep_events_index  # noqa: E402


def main():
    bootstrap_llm()
    ap = base_arg_parser(__doc__ or "")
    args = ap.parse_args()
    sleep_data, rd = load_health_row(args.user_id, args.record_date, args.output_dir)
    main_title = load_sleep_report_main_title(args.user_id, rd, args.output_dir)
    output_dir = os.path.join(PROJECT_ROOT, args.output_dir)
    events_index = build_sleep_events_index(args.user_id, output_dir=output_dir)
    main = generate_main_summary_for_date(
        args.user_id, sleep_data, events_index, main_title=main_title
    )
    if not main:
        raise SystemExit("生成失败（检查模板、密钥与模型返回 JSON）")
    out = args.out.strip() or default_out_path("preview_sleep_main_summary_one")
    write_result(
        out,
        {"record_date": rd, "main_title": main_title, "main": main},
    )


if __name__ == "__main__":
    main()
