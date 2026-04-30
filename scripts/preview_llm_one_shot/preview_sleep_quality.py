#!/usr/bin/env python3
"""单条预览：睡眠质量 analysis.module（system = prompt/sleep_quality_analysis_template.md）。"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from _shared import (
    PROJECT_ROOT,
    apply_translate_qwen_env_defaults,
    base_arg_parser,
    bootstrap,
    default_out_path,
    load_health_row,
    write_result,
)

bootstrap()
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
apply_translate_qwen_env_defaults()

import generate_health_data as gh  # noqa: E402


def main():
    ap = base_arg_parser(__doc__ or "")
    args = ap.parse_args()
    gh.set_model_switch(True)
    sleep_data, rd = load_health_row(args.user_id, args.record_date, args.output_dir)
    mod = gh.generate_quality_module_via_qwen(sleep_data)
    if not mod:
        raise SystemExit("生成失败（检查模板、密钥与模型返回 JSON）")
    out = args.out.strip() or default_out_path("preview_sleep_quality_one")
    write_result(out, {"record_date": rd, "quality_analysis_module": mod})


if __name__ == "__main__":
    main()
