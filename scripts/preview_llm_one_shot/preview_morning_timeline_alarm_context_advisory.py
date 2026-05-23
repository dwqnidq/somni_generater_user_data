#!/usr/bin/env python3
"""单条预览：晨间日程·天气·路况闹钟上下文洞察（system = prompt/morning_timeline_alarm_context_advisory.md）。

从工程根下目录读取：
  output/{user_id}_weather.json — 天气（扁平指标将包入 weather.weather_origin，便于与提示词一致）
  output/{user_id}_traffic_link_realtime.json — 单路段实时路况；status：1畅通、2缓行、3拥堵、4严重拥堵
  {schedules_dir}/{user_id}_calendar_events.json — 日程（默认 schedules_dir 为 users_schedules）；
  若该路径无文件或解析后为空，则自动回退到 {output_dir}/{user_id}_calendar_events.json（与同目录天气/路况一致）。

用户消息仅含 schedule / weather / traffic 三个顶层键。默认「明日」为所选 health 行 record_date 的次日，可用 --tomorrow-date 覆盖。
无明日日程时 schedule.has_tomorrow_events 为 false，模型仅结合天气与路况，不臆造日程。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

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


def _load_json_object(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _load_json_events_list(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def _event_sort_key(ev: dict) -> tuple[str, str]:
    return (str(ev.get("start_time") or "99:99"), str(ev.get("event_name") or ""))


def _compact_event(ev: dict) -> dict:
    return {
        "event_date": ev.get("event_date"),
        "event_name": ev.get("event_name"),
        "start_time": ev.get("start_time"),
        "end_time": ev.get("end_time"),
        "duration_minutes": ev.get("duration_minutes"),
    }


def _build_schedule_for_tomorrow(events: list[dict], tomorrow: str) -> dict:
    day_events = [e for e in events if str(e.get("event_date") or "") == tomorrow]
    day_events.sort(key=_event_sort_key)
    compact = [_compact_event(e) for e in day_events]
    first = compact[0] if compact else None
    return {
        "tomorrow_date": tomorrow,
        "has_tomorrow_events": bool(compact),
        "first_event_tomorrow": first,
        "events_tomorrow_ordered": compact,
    }


def _default_tomorrow_from_record_date(record_date: str) -> str:
    d = datetime.strptime(record_date, "%Y-%m-%d")
    return (d + timedelta(days=1)).strftime("%Y-%m-%d")


def _resolve_calendar_events(
    user_id: str, schedules_dir: str, output_dir: str
) -> tuple[list[dict], str]:
    """优先 schedules_dir 下非空列表；否则回退 output_dir。均无则 []（仍会调 LLM）。"""
    primary = os.path.join(PROJECT_ROOT, schedules_dir, f"{user_id}_calendar_events.json")
    fallback = os.path.join(os.path.abspath(output_dir), f"{user_id}_calendar_events.json")
    events = _load_json_events_list(primary)
    if events:
        return events, primary
    fb = _load_json_events_list(fallback)
    if fb:
        if os.path.isfile(primary):
            print(f"提示: {primary} 无日程条目，已回退: {fallback}")
        else:
            print(f"提示: 未找到 {primary}，已使用: {fallback}")
        return fb, fallback
    return [], primary if os.path.isfile(primary) else fallback


def main():
    ap = base_arg_parser(__doc__ or "")
    ap.add_argument(
        "--tomorrow-date",
        default="",
        help="明日日期 YYYY-MM-DD；省略则取 health 所选行的 record_date 的次日",
    )
    ap.add_argument(
        "--schedules-dir",
        default="users_schedules",
        help="相对工程根目录，内含 {user_id}_calendar_events.json，默认 users_schedules",
    )
    ap.add_argument(
        "--system-prompt-file",
        default="morning_timeline_alarm_context_advisory.md",
        help="prompt 目录下的文件名，或传绝对/相对路径的 .md",
    )
    args = ap.parse_args()
    gh.set_model_switch(True)

    _, rd = load_health_row(args.user_id, args.record_date, args.output_dir)
    tomorrow = (args.tomorrow_date or "").strip() or _default_tomorrow_from_record_date(rd)

    out_base = os.path.join(PROJECT_ROOT, args.output_dir)
    weather_path = os.path.join(out_base, f"{args.user_id}_weather.json")
    traffic_path = os.path.join(out_base, f"{args.user_id}_traffic_link_realtime.json")
    raw_weather = _load_json_object(weather_path)
    traffic = _load_json_object(traffic_path)
    calendar_events, calendar_path = _resolve_calendar_events(
        args.user_id, args.schedules_dir, args.output_dir
    )

    if not raw_weather:
        print(f"警告: 未读取到天气文件或为空，将传入空 weather 对象: {weather_path}")
    if not traffic:
        print(f"警告: 未读取到路况文件或为空，将传入空 traffic 对象: {traffic_path}")
    if not calendar_events:
        p1 = os.path.join(PROJECT_ROOT, args.schedules_dir, f"{args.user_id}_calendar_events.json")
        p2 = os.path.join(PROJECT_ROOT, args.output_dir, f"{args.user_id}_calendar_events.json")
        print(f"警告: 日程列表为空。已尝试: {p1} 与 {p2}")

    weather_payload: dict = {}
    if raw_weather:
        weather_payload["weather_origin"] = raw_weather

    schedule = _build_schedule_for_tomorrow(calendar_events, tomorrow)
    if calendar_events and not schedule.get("events_tomorrow_ordered"):
        print(
            f"警告: 已加载 {len(calendar_events)} 条日程，但无 event_date == {tomorrow} 的条目；"
            "schedule 中明日列表为空。请使用 --tomorrow-date 指定与日程一致的日期，或调整 --record-date。"
        )

    user_payload = {
        "schedule": schedule,
        "weather": weather_payload,
        "traffic": traffic,
    }

    print("=== 传递给模型的数据（用户消息 JSON 三键）===")
    print(json.dumps(user_payload, ensure_ascii=False, indent=2))
    print("======================")

    sp = args.system_prompt_file.strip()
    system = ""
    for candidate in (
        sp,
        os.path.join(PROJECT_ROOT, "prompt", sp),
        os.path.join(PROJECT_ROOT, "prompt", os.path.basename(sp)),
    ):
        if candidate and os.path.isfile(candidate):
            with open(candidate, "r", encoding="utf-8") as f:
                system = f.read().strip()
            break
    if not system:
        system = gh.load_prompt_instruction(os.path.basename(sp) or sp)
    if not system:
        raise SystemExit(f"系统提示词为空，请检查: {sp}")

    user_msg = (
        "以下为真实输入数据（JSON），顶层键仅为 schedule、weather、traffic。"
        '请严格按系统提示词仅输出一个 JSON 对象，且只包含键 alarm_insight（字符串）；不要 markdown 围栏或解释。\n\n'
        + json.dumps(user_payload, ensure_ascii=False)
    )
    raw_out = gh.call_qwen_api(
        user_msg,
        system_prompt=system,
        max_tokens=768,
        temperature=PREVIEW_LLM_TEMPERATURE,
        top_p=PREVIEW_LLM_TOP_P,
        sleep_report_llm=True,
    )
    if not raw_out.strip():
        raise SystemExit("模型无返回（请检查密钥、模型配置与网络）")

    try:
        parsed = gh._parse_json_from_response(raw_out)
    except Exception as e:
        raise SystemExit(f"解析 JSON 失败: {e}\n原始输出:\n{raw_out[:800]}") from e
    if not isinstance(parsed, dict):
        raise SystemExit("模型输出不是 JSON 对象")

    out = args.out.strip() or default_out_path("preview_morning_timeline_alarm_context_advisory_one")
    write_result(
        out,
        {
            "record_date": rd,
            "tomorrow_date": tomorrow,
            "weather_path": weather_path,
            "traffic_path": traffic_path,
            "calendar_path": calendar_path,
            "alarm_insight": parsed.get("alarm_insight", ""),
        },
    )
    print(f"已写入: {os.path.abspath(out)}")


if __name__ == "__main__":
    main()
