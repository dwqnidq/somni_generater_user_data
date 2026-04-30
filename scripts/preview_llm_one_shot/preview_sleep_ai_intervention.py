#!/usr/bin/env python3
"""单条预览：睡眠事件 AI 干预重写（按指定日期找 AI 干预事件及其关联异常事件，调用模型重写 detail）。"""

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
    load_health_row,
    write_result,
)

bootstrap()
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
apply_translate_qwen_env_defaults()

import generate_health_data as gh  # noqa: E402


def _build_intervention_event_groups(events: list[dict]) -> list[list[dict]]:
    """找出所有 AI 主动干预事件，通过 related_event_id 找到关联的父级事件，
    返回 [[父级事件, AI干预事件], ...] 的二维数组。"""
    event_by_id = {
        e.get("_id"): e
        for e in events
        if isinstance(e, dict) and e.get("_id")
    }
    groups = []
    for event in events:
        if not isinstance(event, dict) or event.get("event_type") != "AI主动干预":
            continue
        related_id = event.get("related_event_id", "")
        if not related_id:
            continue
        parent = event_by_id.get(related_id)
        if parent:
            groups.append([parent, event])
    return groups


def main():
    ap = base_arg_parser(__doc__ or "")
    args = ap.parse_args()
    gh.set_model_switch(True)
    _, rd = load_health_row(args.user_id, args.record_date, args.output_dir)

    events_index = gh.build_sleep_events_index(args.user_id, output_dir=args.output_dir)
    event_groups = _build_intervention_event_groups(events_index.get(rd, []))
    payload = {"record_date": rd, "sleep_events": event_groups}
    print(
        "传递给 AI 干预重写模型的睡眠事件数据：\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )

    if not event_groups:
        result = []
    else:
        instruction = gh.load_prompt_instruction("sleep_event_ai_intervention.md")
        if not instruction:
            raise SystemExit("读取 sleep_event_ai_intervention.md 失败或为空")

        prompt = (
            "以下为指定日期睡眠过程中的异常事件与 AI 主动干预事件组（JSON 二维数组）。"
            "每个子数组包含一个父级事件及其关联的 AI 主动干预事件。"
            "请仅重写每条记录中的 detail 字段，保持其余所有字段与结构不变，"
            "输出严格等长的二维 JSON 数组，不要附加任何解释。\n\n"
            + json.dumps(event_groups, ensure_ascii=False)
        )
        raw = gh.call_qwen_api(
            prompt,
            system_prompt=instruction,
            max_tokens=8192,
            sleep_report_llm=True,
        )
        if not raw:
            result = None
        else:
            try:
                result = gh._parse_json_from_response(raw)
            except Exception:
                result = gh._parse_model_json_array(raw)

    if result is None or not isinstance(result, list):
        raise SystemExit("生成失败（检查模板、密钥与模型返回 JSON）")

    out = args.out.strip() or default_out_path("preview_sleep_ai_intervention_one")
    write_result(out, {"record_date": rd, "sleep_events": result})
    print(f"\n结果已写入：{out}")


if __name__ == "__main__":
    main()
