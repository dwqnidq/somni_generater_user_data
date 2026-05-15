#!/usr/bin/env python3
"""单条预览：14 天趋势 AI 分析（system = prompt/sleep_trend_14d_analysis.md）。

会从 output 目录读取同用户的：
  {user_id}_health_data.json（睡眠，14 天窗口由 generate_ai_analysis 内部取）
  {user_id}_calendar_events.json（日程；仅注入 anchor_record_date 当天）
  {user_id}_daily_emotion_steps.json（步数 + 情绪分数；仅注入 anchor 当天）
  qweather_today_snapshot.json（今日天气快照，路径可通过 --weather-json 覆盖）
缺失侧车文件时对应字段以空对象/数组注入，不中断运行。
"""

from __future__ import annotations

import json
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

import generate_health_data as gh  # noqa: E402


def _load_calendar_events_for_date(user_id: str, output_dir: str, target_date: str) -> list[dict]:
    """从 output/{user_id}_calendar_events.json 加载指定日期的日程事件。"""
    path = os.path.join(PROJECT_ROOT, output_dir, f"{user_id}_calendar_events.json")
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        events = json.load(f)
    if not isinstance(events, list):
        return []
    return [e for e in events if isinstance(e, dict) and e.get("event_date") == target_date]


def _load_emotion_steps_for_date(user_id: str, output_dir: str, target_date: str) -> dict:
    """从 output/{user_id}_daily_emotion_steps.json 读取指定日期的步数与情绪分。

    返回字典字段：steps、emotion_score；任一缺失时为 None。
    """
    path = os.path.join(PROJECT_ROOT, output_dir, f"{user_id}_daily_emotion_steps.json")
    if not os.path.isfile(path):
        return {"steps": None, "emotion_score": None}
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        return {"steps": None, "emotion_score": None}
    for r in rows:
        if isinstance(r, dict) and r.get("event_date") == target_date:
            return {
                "steps": r.get("steps"),
                "emotion_score": r.get("score"),
            }
    return {"steps": None, "emotion_score": None}


def _load_weather_snapshot(path: str) -> dict:
    """读取气象快照 JSON；只保留 sleep_trend_14d_analysis 模板涉及的字段。"""
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {}
    keys = ("sunrise", "sunset", "uvIndex", "humidity", "aqiDisplay")
    return {k: data.get(k) for k in keys if data.get(k) is not None}


def _load_text_prompt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def main():
    ap = base_arg_parser(__doc__ or "")
    ap.add_argument(
        "--weather-json",
        default=os.path.join(PROJECT_ROOT, "output", "qweather_today_snapshot.json"),
        help="今日天气快照 JSON（默认 output/qweather_today_snapshot.json）",
    )
    ap.add_argument(
        "--persona-system-prompt",
        default=os.path.join(PROJECT_ROOT, "docs", "personas", "睡眠指标标准.md"),
        help="补充系统提示词文件（会追加到原 system_prompt 后，不替换原提示词）",
    )
    args = ap.parse_args()
    gh.set_model_switch(True)
    persona_prompt_text = _load_text_prompt(args.persona_system_prompt)

    _, rd = load_health_row(args.user_id, args.record_date, args.output_dir)

    calendar_events = _load_calendar_events_for_date(args.user_id, args.output_dir, rd)
    today_health = _load_emotion_steps_for_date(args.user_id, args.output_dir, rd)
    today_weather = _load_weather_snapshot(args.weather_json)

    injected_schedule_records = [
        {
            "event_date": item.get("event_date"),
            "event_type": item.get("event_type"),
            "event_name": item.get("event_name"),
            "start_time": item.get("start_time"),
            "end_time": item.get("end_time"),
            "duration_minutes": item.get("duration_minutes"),
        }
        for item in calendar_events
    ]

    original_compact_schedule = gh._compact_schedule_records_for_trend_14d_prompt

    def _debug_compact_schedule_records_for_trend_14d_prompt(_sched_seg):
        # 提示词要求 schedule_records_14d 只放 anchor 当天的日程
        if injected_schedule_records:
            return injected_schedule_records
        return original_compact_schedule(_sched_seg)

    gh._compact_schedule_records_for_trend_14d_prompt = (
        _debug_compact_schedule_records_for_trend_14d_prompt
    )

    original_call_doubao_api = gh.call_doubao_api

    def debug_call_doubao_api(prompt, *call_args, **call_kwargs):
        original_system_prompt = call_kwargs.get("system_prompt", "") or ""
        merged_system_prompt = (
            f"{original_system_prompt}\n\n"
            "## 补充依据：睡眠指标标准\n"
            f"{persona_prompt_text}"
        ).strip()
        call_kwargs["system_prompt"] = merged_system_prompt
        # 预览脚本固定采样参数（覆盖 generate_ai_analysis 内的 env 温度等）
        call_kwargs["temperature"] = PREVIEW_LLM_TEMPERATURE
        call_kwargs["top_p"] = PREVIEW_LLM_TOP_P

        # 把 today_health / today_weather 注入到 prompt 内的 JSON payload
        prompt_text = str(prompt)
        lb = prompt_text.find("{")
        rb = prompt_text.rfind("}")
        if lb != -1 and rb != -1 and rb > lb:
            try:
                payload = json.loads(prompt_text[lb : rb + 1])
                payload["today_health"] = today_health
                payload["today_weather"] = today_weather
                prompt = prompt_text[:lb] + json.dumps(payload, ensure_ascii=False)
            except Exception as e:
                print(f"  [警告] 注入 today_health / today_weather 失败: {e}")

        print("\n========== AI 调用调试信息开始 ==========")
        print("\n[1] 传递给 AI 的系统提示词（system_prompt）:\n")
        print(merged_system_prompt or "(空)")

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
                    f"schedule_records_14d={len(payload.get('schedule_records_14d', []))}, "
                    f"today_health={payload.get('today_health')}, "
                    f"today_weather_keys={list((payload.get('today_weather') or {}).keys())}"
                )
            except Exception as e:
                print(f"\n[3] 无法解析 prompt 中的 JSON 数据: {e}")
        else:
            print("\n[3] prompt 中未检测到 JSON 数据段")

        print("\n========== AI 调用调试信息结束 ==========\n")
        return original_call_doubao_api(prompt, *call_args, **call_kwargs)

    # 兼容当前主流程仍从 call_qwen_api 入口发起；统一转到豆包别名调用。
    gh.call_qwen_api = debug_call_doubao_api
    gh.call_doubao_api = debug_call_doubao_api

    calendar_events_path = os.path.join(
        PROJECT_ROOT, args.output_dir, f"{args.user_id}_calendar_events.json"
    )
    emotion_steps_path = os.path.join(
        PROJECT_ROOT, args.output_dir, f"{args.user_id}_daily_emotion_steps.json"
    )
    print(
        "注入 14d AI 分析侧车数据：\n"
        + json.dumps(
            {
                "target_date": rd,
                "calendar_events_path": calendar_events_path,
                "schedule_records_14d_count": len(injected_schedule_records),
                "emotion_steps_path": emotion_steps_path,
                "today_health": today_health,
                "weather_json_path": args.weather_json,
                "today_weather": today_weather,
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
