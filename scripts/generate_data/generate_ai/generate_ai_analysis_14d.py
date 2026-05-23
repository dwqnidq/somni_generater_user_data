"""
批量生成近14天趋势 AI 分析（ai_analysis_14d）。

使用 output/{uid}_calendar_events.json 作为日程数据（替代已废弃的 schedule_data）。
使用 output/{uid}_daily_emotion_steps.json 提供锚点日的 steps 与 score（情绪）；
任一缺失则跳过该锚点日并继续下一日。天气取自 output/qweather_monthly_data.json（可用 --weather-json 覆盖），
按锚定日注入当日预报（如 5/20 分析用 5/20 天气，5/21 用 5/21 天气）。

锚点日列表 = 天气预报 JSON 中的 date（再按 --start-date/--end-date 过滤）；对每个锚点日各调一次 LLM，
产出一条 ai_analysis_14d（含锚定日前连续 14 天睡眠窗口）。系统提示词默认 sleep_trend_14d_analysis.md。

作为独立脚本运行：
  python scripts/generate_data/generate_ai/generate_ai_analysis_14d.py --uid <uid>

作为模块导入：
  from generate_ai_analysis_14d import generate_ai_analysis_14d_for_uid
  results = generate_ai_analysis_14d_for_uid(uid, output_dir)
  # [{"uid":..., "record_date":..., "sleep_insight":..., "schedule_insight":..., ...}, ...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Callable, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
GEN_DATA_DIR = os.path.join(PROJECT_ROOT, "scripts", "generate_data")

for _p in (PROJECT_ROOT, GEN_DATA_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_ai import llm_client  # noqa: E402
from generate_ai.llm_client import LlmQuotaExhausted  # noqa: E402
from generate_ai.llm_resume import bootstrap_resume, checkpoint_save, upsert_row  # noqa: E402
from generate_ai.multi_day_llm_helpers import (  # noqa: E402
    apply_max_records,
    backward_14_health_complete,
    merge_last_skipped,
)
from generate_ai.runtime import bootstrap_llm  # noqa: E402
from generate_ai.trend_14d_analysis import (  # noqa: E402
    compact_schedule_records_for_trend_14d_prompt,
    generate_ai_analysis,
)


def _load_calendar_events(uid: str, output_dir: str) -> List[dict]:
    path = os.path.join(output_dir, f"{uid}_calendar_events.json")
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict)]


def _build_schedule_records_for_date(calendar_events: List[dict], target_date: str) -> List[dict]:
    """将 calendar_events 中指定日期的条目转换为日程记录格式。"""
    return [
        {
            "event_date": e.get("event_date"),
            "event_type": e.get("event_type", "schedule_info"),
            "event_name": e.get("event_name"),
            "start_time": e.get("start_time"),
            "end_time": e.get("end_time"),
            "duration_minutes": e.get("duration_minutes"),
            "motion_score": e.get("motion_score"),
            "step": e.get("step"),
        }
        for e in calendar_events
        if str(e.get("event_date") or "") == target_date
    ]


def _load_daily_emotion_steps_rows(uid: str, output_dir: str) -> List[dict]:
    path = os.path.join(output_dir, f"{uid}_daily_emotion_steps.json")
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _today_health_for_date(emotion_rows: List[dict], target_date: str) -> Optional[dict]:
    """锚点日 steps + score（情绪）齐全则返回 today_health 结构，否则 None（调用方应跳过）。"""
    for r in emotion_rows:
        if str(r.get("event_date") or "") != target_date:
            continue
        steps = r.get("steps")
        score = r.get("score")
        if steps is None or score is None:
            return None
        return {"steps": steps, "emotion_score": score}
    return None


def _load_weather_by_date(path: str) -> dict[str, dict]:
    """读取多日天气预报，返回 {YYYY-MM-DD: 扁平天气字段}。"""
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    from utils import qweather_records_by_date

    return qweather_records_by_date(data)


def _wrap_qwen_inject_today_health_weather(
    original: Callable[..., Any],
    today_health: dict,
    today_weather: dict,
) -> Callable[..., Any]:
    """在 user prompt 的 JSON payload 中写入 today_health、today_weather（不修改 system_prompt）。"""

    def wrapped(prompt: object, *call_args: object, **call_kwargs: object):
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
        return original(prompt, *call_args, **call_kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def generate_ai_analysis_14d_for_uid(
    uid: str,
    output_dir: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    retry_delay: float = 0.5,
    weather_json: Optional[str] = None,
    max_records: Optional[int] = None,
    resume: bool = False,
) -> Tuple[List[dict], Optional[str]]:
    """为单个用户按天气预报日期批量生成 14 天趋势 AI 分析。

    使用 calendar_events 替代 schedule_data 作为日程来源。
    遍历 qweather 多日预报中的每个 date（在 start/end 范围内）；缺数据则跳过并继续下一日。

    若锚点日及向前连续 14 个自然日缺少任一日的 health 记录，则跳过。
    若锚点日在 daily_emotion_steps 中缺少 steps 或 score，则跳过。

    Returns:
        (rows, last_skipped_date)：rows 为 generate_ai_analysis 返回的记录列表；
        last_skipped_date 为本次处理日期范围内「因数据不足等原因跳过」的最晚一日
        （YYYY-MM-DD），若全日成功生成则为 ``None``。
    """
    os.environ.setdefault("SLEEP_TREND_14D_PROMPT", "sleep_trend_14d_analysis.md")

    calendar_events = _load_calendar_events(uid, output_dir)
    emotion_rows = _load_daily_emotion_steps_rows(uid, output_dir)
    weather_path = weather_json or os.path.join(PROJECT_ROOT, "output", "qweather_monthly_data.json")
    weather_by_date = _load_weather_by_date(weather_path)
    if not weather_by_date:
        print(f"  [跳过] 天气预报为空或缺失: {weather_path}")
        return [], None
    original_compact = compact_schedule_records_for_trend_14d_prompt

    out_path = os.path.join(output_dir, f"{uid}_ai_analysis_14d.json")
    all_results, done, by_date = bootstrap_resume(out_path, resume)
    if resume and done:
        print(f"  [续跑] 已从 {os.path.basename(out_path)} 加载 {len(done)} 个已完成日期")

    health_path = os.path.join(output_dir, f"{uid}_health_data.json")
    if not os.path.isfile(health_path):
        print(f"  [跳过] uid={uid} 无 health 文件")
        return [], None
    with open(health_path, "r", encoding="utf-8") as f:
        health_rows = json.load(f)
    if not isinstance(health_rows, list):
        return [], None
    health_dates = {
        str(r.get("record_date"))
        for r in health_rows
        if isinstance(r, dict) and r.get("record_date")
    }

    # 以天气预报中的 date 为锚点日（5/20、5/21…各用当日天气）；缺项则 continue
    dates = sorted(weather_by_date.keys())
    if start_date:
        dates = [d for d in dates if d >= start_date]
    if end_date:
        dates = [d for d in dates if d <= end_date]

    dates = apply_max_records(dates, max_records)

    last_skipped: Optional[str] = None

    for i, target_date in enumerate(dates):
        if target_date in done:
            print(f"  [{i + 1}/{len(dates)}] uid={uid} date={target_date} … 续跑跳过（已有记录）")
            continue
        print(f"  [{i + 1}/{len(dates)}] uid={uid} date={target_date} …", end=" ", flush=True)

        if not backward_14_health_complete(target_date, health_dates):
            print("跳过（向前 14 天 health 不齐）")
            last_skipped = merge_last_skipped(last_skipped, target_date)
            continue

        today_health = _today_health_for_date(emotion_rows, target_date)
        if today_health is None:
            print("跳过（无当日 steps/score：daily_emotion_steps）")
            last_skipped = merge_last_skipped(last_skipped, target_date)
            continue

        today_weather = weather_by_date.get(target_date) or {}
        if not today_weather:
            print("跳过（天气预报无该日）")
            last_skipped = merge_last_skipped(last_skipped, target_date)
            continue

        # 为当前日期注入对应的日历事件作为日程
        injected = _build_schedule_records_for_date(calendar_events, target_date)

        def _patched_compact(_sched_seg, _injected=injected):
            if _injected:
                return _injected
            return original_compact(_sched_seg)

        import generate_ai.trend_14d_analysis as trend_mod

        original_qwen = llm_client.call_qwen_api
        wrapped_api = _wrap_qwen_inject_today_health_weather(
            original_qwen, today_health, today_weather
        )
        llm_client.call_qwen_api = wrapped_api
        llm_client.call_doubao_api = wrapped_api
        trend_mod.compact_schedule_records_for_trend_14d_prompt = _patched_compact
        try:
            rows = generate_ai_analysis(
                uid,
                use_doubao=True,
                output_dir=output_dir,
                start_date=target_date,
                end_date=target_date,
            )
        except LlmQuotaExhausted:
            checkpoint_save(out_path, all_results)
            print("配额/限流耗尽，已保存进度")
            raise
        finally:
            trend_mod.compact_schedule_records_for_trend_14d_prompt = original_compact
            llm_client.call_qwen_api = original_qwen
            llm_client.call_doubao_api = original_qwen

        if not rows:
            print("失败（已跳过）")
            last_skipped = merge_last_skipped(last_skipped, target_date)
        else:
            print("完成")
            for row in rows:
                if isinstance(row, dict) and row.get("record_date"):
                    all_results = upsert_row(by_date, row)
                    done.add(str(row.get("record_date")))
            checkpoint_save(out_path, all_results)

        if i < len(dates) - 1:
            time.sleep(retry_delay)

    return all_results, last_skipped


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output-dir", default=os.path.join(PROJECT_ROOT, "output"))
    p.add_argument("--out", default=os.path.join(PROJECT_ROOT, "output", "ai_analysis_14d.json"))
    p.add_argument("--uid", default="")
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    p.add_argument("--retry-delay", type=float, default=0.5)
    p.add_argument(
        "--weather-json",
        default="",
        help="天气预报 JSON（默认 <项目>/output/qweather_monthly_data.json）",
    )
    p.add_argument(
        "--max-records",
        type=int,
        default=0,
        help="在日期过滤后最多处理 N 个锚点日（0 表示不限制）",
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
    weather_json = (args.weather_json or "").strip() or None
    if weather_json:
        weather_json = os.path.abspath(weather_json)
    uids = [args.uid.strip()] if args.uid.strip() else _iter_uids(output_dir)
    all_results: List[dict] = []
    for uid in uids:
        print(f"\n处理 uid={uid} …")
        mr = int(args.max_records) or None
        rows, _ls = generate_ai_analysis_14d_for_uid(
            uid=uid,
            output_dir=output_dir,
            start_date=args.start_date.strip() or None,
            end_date=args.end_date.strip() or None,
            retry_delay=args.retry_delay,
            weather_json=weather_json,
            max_records=mr or None,
        )
        all_results.extend(rows)
    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n已写入 {len(all_results)} 条 → {out_path}")


if __name__ == "__main__":
    main()
