#!/usr/bin/env python3
"""单条预览：14 天趋势 AI 分析（system = prompt/sleep_trend_14d_analysis.md）。"""

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


def _load_schedule_payload(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("日程 JSON 顶层必须是对象")
    schedule_info = data.get("schedule_info")
    if schedule_info is None:
        schedule_info = []
    if not isinstance(schedule_info, list):
        raise ValueError("schedule_info 必须是数组")
    return data


def _load_text_prompt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def main():
    ap = base_arg_parser(__doc__ or "")
    ap.add_argument(
        "--schedule-json",
        default=os.path.join(PROJECT_ROOT, "output", "123.json"),
        help="用于注入 14d 趋势分析的日程 JSON（默认 output/123.json）",
    )
    ap.add_argument(
        "--persona-system-prompt",
        default=os.path.join(PROJECT_ROOT, "docs", "personas", "睡眠指标标准.md"),
        help="补充系统提示词文件（会追加到原 system_prompt 后，不替换原提示词）",
    )
    args = ap.parse_args()
    gh.set_model_switch(True)
    persona_prompt_text = _load_text_prompt(args.persona_system_prompt)

    original_call_qwen_api = gh.call_qwen_api

    def debug_call_qwen_api(prompt, *call_args, **call_kwargs):
        original_system_prompt = call_kwargs.get("system_prompt", "") or ""
        merged_system_prompt = (
            f"{original_system_prompt}\n\n"
            "## 补充依据：睡眠指标标准\n"
            f"{persona_prompt_text}"
        ).strip()
        call_kwargs["system_prompt"] = merged_system_prompt

        system_prompt = merged_system_prompt
        print("\n========== AI 调用调试信息开始 ==========")
        print("\n[1] 传递给 AI 的系统提示词（system_prompt）:\n")
        print(system_prompt or "(空)")

        print("\n[2] 传递给 AI 的用户提示词（prompt）原文:\n")
        print(prompt)

        prompt_text = str(prompt)
        left_brace = prompt_text.find("{")
        right_brace = prompt_text.rfind("}")
        if left_brace != -1 and right_brace != -1 and right_brace > left_brace:
            try:
                payload = json.loads(prompt_text[left_brace : right_brace + 1])
                print("\n[3] prompt 中附带的数据 JSON（格式化）:\n")
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                print(
                    "\n[4] 数据概览: "
                    f"sleep_records_14d={len(payload.get('sleep_records_14d', []))}, "
                    f"schedule_records_14d={len(payload.get('schedule_records_14d', []))}"
                )
            except Exception as e:
                print(f"\n[3] 无法解析 prompt 中的 JSON 数据: {e}")
        else:
            print("\n[3] prompt 中未检测到 JSON 数据段")

        print("\n========== AI 调用调试信息结束 ==========\n")
        return original_call_qwen_api(prompt, *call_args, **call_kwargs)

    gh.call_qwen_api = debug_call_qwen_api

    _, rd = load_health_row(args.user_id, args.record_date, args.output_dir)

    schedule_payload = _load_schedule_payload(args.schedule_json)
    injected_schedule_records = []
    for item in schedule_payload.get("schedule_info", []):
        if not isinstance(item, dict):
            continue
        injected_schedule_records.append(
            {
                "event_date": rd,
                "event_type": "schedule_info",
                "event_name": item.get("activity"),
                "start_time": item.get("start_time"),
                "end_time": item.get("end_time"),
                "duration_minutes": None,
                # 业务映射：motion_score=情绪评分(0-100), step=步数
                "motion_score": schedule_payload.get("motion_score"),
                "step": schedule_payload.get("step"),
            }
        )

    original_compact_schedule = gh._compact_schedule_records_for_trend_14d_prompt

    def _debug_compact_schedule_records_for_trend_14d_prompt(_sched_seg):
        if injected_schedule_records:
            return injected_schedule_records
        return original_compact_schedule(_sched_seg)

    gh._compact_schedule_records_for_trend_14d_prompt = (
        _debug_compact_schedule_records_for_trend_14d_prompt
    )

    print(
        "注入 14d 日程数据：\n"
        + json.dumps(
            {
                "schedule_json_path": args.schedule_json,
                "motion_score": schedule_payload.get("motion_score"),
                "step": schedule_payload.get("step"),
                "schedule_records_14d_count": len(injected_schedule_records),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    rows = gh.generate_ai_analysis(
        args.user_id,
        use_doubao=True,
        output_dir=args.output_dir,
        start_date=rd,
        end_date=rd,
    )
    if not rows:
        raise SystemExit("未生成任何记录（请检查日期是否在 health 内、DASHSCOPE_API_KEY、USE_MODEL）")
    out = args.out.strip() or default_out_path("preview_ai_analysis_14d_one")
    write_result(out, rows[0])


if __name__ == "__main__":
    main()
