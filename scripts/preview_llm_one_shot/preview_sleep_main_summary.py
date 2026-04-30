#!/usr/bin/env python3
"""单条预览：main.summary（system = 完整 prompt/generate_health_data__main_summary_general.md）。"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv

from _shared import (
    PROJECT_ROOT,
    apply_translate_qwen_env_defaults,
    base_arg_parser,
    bootstrap,
    default_out_path,
    load_config_profile,
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
    profile = load_config_profile(args.user_id)
    personality_type = (profile.get("personalInformation") or {}).get("type", "M-L-C")
    sleep_events_index = gh.build_sleep_events_index(args.user_id, output_dir=args.output_dir)
    date_sleep_events = sleep_events_index.get(rd, [])
    event_lines = [
        {
            "time": e.get("event_timestamp", ""),
            "type": e.get("event_type", ""),
            "code": e.get("code", ""),
            "detail": e.get("detail", {}),
        }
        for e in date_sleep_events
    ]

    raw = sleep_data["raw_data"]
    (
        _a,
        _d,
        _l,
        _r,
        _pie_aw,
        _pie_d,
        _pie_l,
        _pie_r,
        _awake_percent,
        deep_percent,
        light_percent,
        rem_percent,
    ) = gh.sleep_report_structure_minutes_and_percents(sleep_data)
    sleep_latency = raw.get("sleep_latency", 0)
    sleep_efficiency = raw.get("sleep_efficiency", 0)
    apnea_count = int(raw.get("apnea_count", 0) or 0)

    main_title = gh.pick_main_title(
        personality_type,
        sleep_data,
        light_percent,
        deep_percent,
        rem_percent,
        sleep_latency,
        sleep_efficiency,
        apnea_count,
        recent_titles=None,
    )
    sleep_data_for_prompt = {
        **sleep_data,
        "sleep_events": event_lines,
    }

    system = gh.render_prompt_template(
        "generate_health_data__main_summary_general.md",
        {
            "SLEEP_LABEL": main_title,
            "SLEEP_DATA_JSON": json.dumps(sleep_data_for_prompt, ensure_ascii=False),
        },
    )
    user_msg = '请严格按系统说明仅输出 JSON：{"summary":"..."}，不要 markdown 围栏或解释。'
    raw_out = gh.call_qwen_api(
        user_msg,
        system_prompt=system,
        max_tokens=512,
        temperature=0.35,
        sleep_report_llm=True,
    )
    if not raw_out.strip():
        raise SystemExit(
            "模型无返回（检查 DASHSCOPE_API_KEY、TRANSLATE_BASE_URL、TRANSLATE_MODEL_NAME）"
        )
    try:
        parsed = gh._parse_json_from_response(raw_out)
    except Exception as e:
        raise SystemExit(f"解析 JSON 失败: {e}\n原始:\n{raw_out[:800]}") from e
    out = args.out.strip() or default_out_path("preview_sleep_main_summary_one")
    write_result(
        out,
        {
            "record_date": rd,
            "main_title": main_title,
            "main": {"title": main_title, "summary": parsed.get("summary", "")},
        },
    )


if __name__ == "__main__":
    main()
