#!/usr/bin/env python3
"""单条预览：睡眠痛点 analysis.module（传指定日期异常事件与 AI 干预事件组）。"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv

from _shared import (
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

import generate_health_data as gh  # noqa: E402


def _build_pain_point_event_groups(events: list[dict]) -> list[list[dict]]:
    event_by_id = {
        event.get("_id"): event
        for event in events
        if isinstance(event, dict) and event.get("_id")
    }
    groups = []
    for event in events:
        if not isinstance(event, dict) or event.get("event_type") != "AI主动干预":
            continue
        related_event = event_by_id.get(event.get("related_event_id"))
        if related_event and related_event.get("type") == "abnormal":
            groups.append([related_event, event])
    return groups


def main():
    ap = base_arg_parser(__doc__ or "")
    args = ap.parse_args()
    gh.set_model_switch(True)
    _, rd = load_health_row(args.user_id, args.record_date, args.output_dir)

    events_index = gh.build_sleep_events_index(args.user_id, output_dir=args.output_dir)
    sleep_events = _build_pain_point_event_groups(events_index.get(rd, []))
    payload = {"record_date": rd, "sleep_events": sleep_events}
    print(
        "传递给睡眠痛点模型的睡眠事件数据：\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )

    if not sleep_events:
        mod = []
    else:
        instruction = gh.load_prompt_instruction("sleep_pain_point_analysis_template.md")
        if not instruction:
            raise SystemExit("读取 sleep_pain_point_analysis_template.md 失败或为空")

        prompt = (
            "以下为指定日期睡眠过程中检测到的异常事件与 AI 主动干预事件组（JSON 二维数组）。"
            "每个子数组包含一个异常事件及其关联的 AI 主动干预事件。"
            "请仅依据这些事件组生成 JSON 数组，元素个数 0～3，不要附加解释。\n\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        raw = gh.call_qwen_api(
            prompt,
            system_prompt=instruction,
            max_tokens=4096,
            sleep_report_llm=True,
        )
        if not raw:
            mod = None
        else:
            try:
                mod = gh._parse_json_from_response(raw)
            except Exception:
                mod = gh._parse_model_json_array(raw)
            if isinstance(mod, list):
                mod = mod[:3]

    if mod is None or not isinstance(mod, list):
        raise SystemExit("生成失败（检查模板、密钥与模型返回 JSON）")
    out = args.out.strip() or default_out_path("preview_sleep_pain_point_one")
    write_result(out, {"record_date": rd, "pain_point_analysis_module": mod})


if __name__ == "__main__":
    main()
