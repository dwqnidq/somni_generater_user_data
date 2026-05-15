"""
批量生成晨间日程·天气·路况闹钟上下文洞察（alarm_insight）。

读取 output/{uid}_health_data.json、{uid}_weather.json、
{uid}_traffic_link_realtime.json；日程优先 users_schedules/{uid}_calendar_events.json，
无则回退 output/{uid}_calendar_events.json。
以每条 record_date 的次日为「明日」生成 alarm_insight。无明日日程时仍生成，仅结合天气与路况。

作为独立脚本运行：
  python scripts/generate_data/generate_ai/generate_morning_timeline_alarm_context_advisory.py --uid <uid>

作为模块导入：
  from generate_morning_timeline_alarm_context_advisory import generate_alarm_insight_for_uid
  results = generate_alarm_insight_for_uid(uid, output_dir)
  # [{"uid":..., "record_date":..., "tomorrow_date":..., "alarm_insight": "..."}, ...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
GEN_DATA_DIR = os.path.join(PROJECT_ROOT, "scripts", "generate_data")

for _p in (PROJECT_ROOT, GEN_DATA_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_ai.multi_day_llm_helpers import apply_max_records, merge_last_skipped  # noqa: E402
from generate_ai.runtime import PROJECT_ROOT, bootstrap_llm, load_health_rows  # noqa: E402

DEFAULT_SYSTEM_PROMPT = os.path.join(PROJECT_ROOT, "prompt", "morning_timeline_alarm_context_advisory.md")
DEFAULT_SCHEDULES_DIR = "users_schedules"
LLM_TEMPERATURE = 0.7
LLM_TOP_P = 0.5


def _load_json_object(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _load_json_events_list(path: str) -> List[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def _tomorrow_of(record_date: str) -> str:
    d = datetime.strptime(record_date, "%Y-%m-%d")
    return (d + timedelta(days=1)).strftime("%Y-%m-%d")


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


def _build_schedule_for_tomorrow(events: List[dict], tomorrow: str) -> dict:
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


def _resolve_calendar_events(
    uid: str, schedules_dir: str, output_dir: str
) -> Tuple[List[dict], str]:
    """优先 schedules_dir，否则回退到 output_dir 下同文件名。"""
    primary = os.path.join(PROJECT_ROOT, schedules_dir, f"{uid}_calendar_events.json")
    events = _load_json_events_list(primary)
    if events:
        return events, primary
    fallback = os.path.join(output_dir, f"{uid}_calendar_events.json")
    fb = _load_json_events_list(fallback)
    if fb:
        print(f"  提示: 未在 {primary} 读到有效日程，已回退: {fallback}")
        return fb, fallback
    return [], primary


def _resolve_system_prompt(system_prompt_path: Optional[str]) -> str:
    """与 preview 一致：支持绝对/相对路径及 prompt 目录下文件名。"""
    from generate_ai import llm_client

    sp = (system_prompt_path or DEFAULT_SYSTEM_PROMPT).strip()
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
        system = llm_client.load_prompt_instruction(os.path.basename(sp) or sp)
    if not system:
        raise ValueError(f"系统提示词为空，请检查: {sp}")
    return system


def _build_user_payload(
    tomorrow: str,
    raw_weather: dict,
    traffic: dict,
    calendar_events: List[dict],
) -> dict:
    weather_payload: dict = {}
    if raw_weather:
        weather_payload["weather_origin"] = raw_weather
    return {
        "schedule": _build_schedule_for_tomorrow(calendar_events, tomorrow),
        "weather": weather_payload,
        "traffic": traffic,
    }


def _build_user_msg(user_payload: dict) -> str:
    return (
        "以下为真实输入数据（JSON），顶层键仅为 schedule、weather、traffic。"
        '请严格按系统提示词仅输出一个 JSON 对象，且只包含键 alarm_insight（字符串）；不要 markdown 围栏或解释。\n\n'
        + json.dumps(user_payload, ensure_ascii=False)
    )


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def generate_alarm_insight_for_date(
    record_date: str,
    tomorrow: str,
    weather: dict,
    traffic: dict,
    calendar_events: List[dict],
    system_prompt_path: Optional[str] = None,
) -> Optional[str]:
    """为单日生成 alarm_insight 字符串。

    Returns:
        alarm_insight 字符串，或 None（调用失败时）。
    """
    from generate_ai import llm_client

    instruction = _resolve_system_prompt(system_prompt_path)
    user_payload = _build_user_payload(tomorrow, weather, traffic, calendar_events)
    user_msg = _build_user_msg(user_payload)
    raw = llm_client.call_qwen_api(
        user_msg,
        system_prompt=instruction,
        max_tokens=768,
        temperature=LLM_TEMPERATURE,
        top_p=LLM_TOP_P,
        sleep_report_llm=True,
    )
    if not raw or not raw.strip():
        return None
    try:
        parsed = llm_client.parse_json_from_response(raw)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed.get("alarm_insight", "")


def generate_alarm_insight_for_uid(
    uid: str,
    output_dir: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    system_prompt_path: Optional[str] = None,
    schedules_dir: str = DEFAULT_SCHEDULES_DIR,
    retry_delay: float = 0.5,
    max_records: Optional[int] = None,
) -> Tuple[List[dict], Optional[str]]:
    """为单个用户批量生成每日 alarm_insight。

    无明日日程时仍生成；天气/路况为空时仍调用模型（与 preview 一致），由提示词约束不臆造。

    Returns:
        (rows, last_skipped_date)：last_skipped_date 为范围内 LLM 失败而跳过的最晚一日。
    """
    try:
        health_rows = load_health_rows(uid, output_dir)
    except FileNotFoundError:
        print(f"  [跳过] uid={uid} 无 health 文件")
        return [], None

    weather_path = os.path.join(output_dir, f"{uid}_weather.json")
    traffic_path = os.path.join(output_dir, f"{uid}_traffic_link_realtime.json")
    weather = _load_json_object(weather_path)
    traffic = _load_json_object(traffic_path)
    calendar_events, calendar_path = _resolve_calendar_events(uid, schedules_dir, output_dir)

    if not weather:
        print(f"  警告: 天气文件为空或缺失: {weather_path}")
    if not traffic:
        print(f"  警告: 路况文件为空或缺失: {traffic_path}")
    if not calendar_events:
        p1 = os.path.join(PROJECT_ROOT, schedules_dir, f"{uid}_calendar_events.json")
        p2 = os.path.join(output_dir, f"{uid}_calendar_events.json")
        print(f"  警告: 日程列表为空，已尝试: {p1} 与 {p2}")
    else:
        print(f"  日程来源: {calendar_path}（共 {len(calendar_events)} 条）")

    rows = sorted(health_rows, key=lambda r: str(r.get("record_date") or ""))
    if start_date:
        rows = [r for r in rows if str(r.get("record_date") or "") >= start_date]
    if end_date:
        rows = [r for r in rows if str(r.get("record_date") or "") <= end_date]

    rows = apply_max_records(rows, max_records)

    results: List[dict] = []
    last_skipped: Optional[str] = None
    for i, row in enumerate(rows):
        rd = str(row.get("record_date") or "")
        tomorrow = _tomorrow_of(rd)
        print(f"  [{i + 1}/{len(rows)}] uid={uid} date={rd} tomorrow={tomorrow} …", end=" ", flush=True)
        schedule = _build_schedule_for_tomorrow(calendar_events, tomorrow)
        if calendar_events and not schedule.get("events_tomorrow_ordered"):
            print(
                f"\n  提示: 无 event_date == {tomorrow} 的日程，schedule 明日列表为空",
                end=" ",
                flush=True,
            )
        insight = generate_alarm_insight_for_date(
            record_date=rd,
            tomorrow=tomorrow,
            weather=weather,
            traffic=traffic,
            calendar_events=calendar_events,
            system_prompt_path=system_prompt_path,
        )
        if insight is None:
            print("失败（已跳过）")
            last_skipped = merge_last_skipped(last_skipped, rd)
        else:
            print("完成")
            results.append({
                "uid": uid,
                "record_date": rd,
                "tomorrow_date": tomorrow,
                "alarm_insight": insight,
            })
        if i < len(rows) - 1:
            time.sleep(retry_delay)

    return results, last_skipped


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output-dir", default=os.path.join(PROJECT_ROOT, "output"))
    p.add_argument("--out", default=os.path.join(PROJECT_ROOT, "output", "morning_alarm_insight.json"))
    p.add_argument("--uid", default="")
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    p.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT)
    p.add_argument(
        "--schedules-dir",
        default=DEFAULT_SCHEDULES_DIR,
        help="相对工程根，内含 {uid}_calendar_events.json，默认 users_schedules",
    )
    p.add_argument("--retry-delay", type=float, default=0.5)
    p.add_argument(
        "--max-records",
        type=int,
        default=0,
        help="在日期过滤后最多处理 N 条 health 行（0 表示不限制）",
    )
    return p.parse_args()


def _iter_uids(output_dir: str) -> List[str]:
    return sorted(
        name.replace("_health_data.json", "")
        for name in os.listdir(output_dir)
        if name.endswith("_health_data.json")
    )


def main() -> None:
    bootstrap_llm()
    args = _parse_args()
    output_dir = os.path.abspath(args.output_dir)
    from generate_ai.runtime import iter_uids
    uids = [args.uid.strip()] if args.uid.strip() else iter_uids(output_dir)
    all_results: List[dict] = []
    for uid in uids:
        print(f"\n处理 uid={uid} …")
        mr = int(args.max_records) or None
        rows, _ls = generate_alarm_insight_for_uid(
            uid=uid,
            output_dir=output_dir,
            start_date=args.start_date.strip() or None,
            end_date=args.end_date.strip() or None,
            system_prompt_path=args.system_prompt or None,
            schedules_dir=args.schedules_dir.strip() or DEFAULT_SCHEDULES_DIR,
            retry_delay=args.retry_delay,
            max_records=mr,
        )
        all_results.extend(rows)
    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n已写入 {len(all_results)} 条 → {out_path}")


if __name__ == "__main__":
    main()
