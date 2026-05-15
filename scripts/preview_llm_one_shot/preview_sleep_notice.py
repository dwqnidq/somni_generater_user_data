#!/usr/bin/env python3
"""单条预览：notice（与 generate_sleep_notice 共用生成逻辑）。"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from _shared import (
    PROJECT_ROOT,
    base_arg_parser,
    bootstrap,
    default_out_path,
    force_doubao_env,
    load_config_profile,
    load_health_row,
    write_result,
)

bootstrap()
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
force_doubao_env()

from generate_ai.runtime import bootstrap_llm, load_health_rows  # noqa: E402
from generate_sleep_notice import generate_notice_for_date  # noqa: E402
from sleep_report.sleep_helpers import build_sleep_events_index  # noqa: E402


def main():
    bootstrap_llm()
    ap = base_arg_parser(__doc__ or "")
    args = ap.parse_args()
    sleep_data, rd = load_health_row(args.user_id, args.record_date, args.output_dir)
    output_dir = os.path.join(PROJECT_ROOT, args.output_dir)
    profile = load_config_profile(args.user_id)
    p_type = str(profile.get("code") or "M-L-C").strip() or "M-L-C"
    all_rows = load_health_rows(args.user_id, output_dir)
    by_date = {str(r.get("record_date")): r for r in all_rows if r.get("record_date")}
    events_index = build_sleep_events_index(args.user_id, output_dir=output_dir)
    notice = generate_notice_for_date(
        args.user_id,
        sleep_data,
        events_index,
        output_dir,
        p_type,
        health_rows_by_date=by_date,
    )
    if not notice:
        raise SystemExit("生成失败（需有前一日 health 数据，且模型返回有效 JSON）")
    out = args.out.strip() or default_out_path("preview_sleep_notice_one")
    write_result(out, {"record_date": rd, "notice": notice})


if __name__ == "__main__":
    main()
