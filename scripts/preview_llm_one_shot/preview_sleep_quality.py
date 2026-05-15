#!/usr/bin/env python3
"""单条预览：睡眠质量 analysis.module（system = prompt/sleep_quality_analysis_template.md）。"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from _shared import (
    PREVIEW_LLM_TEMPERATURE,
    PREVIEW_LLM_TOP_P,
    PROJECT_ROOT,
    base_arg_parser,
    bootstrap,
    default_out_path,
    force_doubao_env,
    load_health_row,
    write_result,
)

bootstrap()
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
force_doubao_env()

from generate_ai.runtime import bootstrap_llm  # noqa: E402
from generate_sleep_quality import generate_quality_for_date  # noqa: E402

bootstrap_llm()


def main():
    ap = base_arg_parser(__doc__ or "")
    args = ap.parse_args()
    sleep_data, rd = load_health_row(args.user_id, args.record_date, args.output_dir)
    mod = generate_quality_for_date(
        sleep_data,
        temperature=PREVIEW_LLM_TEMPERATURE,
        top_p=PREVIEW_LLM_TOP_P,
    )
    if not mod:
        raise SystemExit("生成失败（检查模板、密钥与模型返回 JSON）")
    out = args.out.strip() or default_out_path("preview_sleep_quality_one")
    write_result(out, {"record_date": rd, "quality_analysis_module": mod})


if __name__ == "__main__":
    main()
