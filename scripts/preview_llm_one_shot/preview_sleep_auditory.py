#!/usr/bin/env python3
"""单条预览：听觉 module（system = prompt/sleep_audio_analysis.md）。"""

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
    idx = gh.build_sleep_events_index(args.user_id, output_dir=args.output_dir)
    ev = idx.get(rd, [])
    auditory = gh.generate_auditory(
        sleep_data, user_id=args.user_id, sleep_events_index=idx, output_dir=args.output_dir
    )
    env_rows_by_date = gh.index_environment_noise_rows_by_record_date(
        args.user_id, output_dir=args.output_dir
    )
    all_events = [event for events in idx.values() for event in events]
    audios, data_points = gh.rebuild_auditory_audios_and_snoring_data_points(
        rd,
        args.user_id,
        all_events,
        sleep_data,
        env_rows_by_date.get(rd, []),
    )
    auditory["audios"] = audios
    auditory["snoring_analysis"] = {"data_points": data_points}
    mod = gh.generate_auditory_module_via_qwen(
        args.user_id, rd, sleep_data, ev, auditory, output_dir=args.output_dir
    )
    if not mod:
        raise SystemExit("生成失败（检查模板、密钥与模型返回 JSON）")
    out = args.out.strip() or default_out_path("preview_sleep_auditory_one")
    write_result(out, {"record_date": rd, "auditory_module": mod})


if __name__ == "__main__":
    main()
