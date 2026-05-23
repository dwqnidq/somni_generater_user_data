"""
批量生成睡眠事件 AI 干预重写（sleep_ai_intervention）。

读取 output/{uid}_health_data.json 与 {uid}_sleep_events.json，
找出每日「父级事件 + AI主动干预事件」配对组，调用 LLM 重写每条的 detail 字段，
输出结构等长的二维事件数组。

作为独立脚本运行：
  python scripts/generate_data/generate_ai/generate_sleep_ai_intervention.py --uid <uid>

作为模块导入：
  from generate_sleep_ai_intervention import generate_ai_intervention_for_uid
  results = generate_ai_intervention_for_uid(uid, output_dir)
  # [{"uid":..., "record_date":..., "sleep_time_points":{...}, "sleep_events":[...]}, ...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
GEN_DATA_DIR = os.path.join(PROJECT_ROOT, "scripts", "generate_data")

for _p in (PROJECT_ROOT, GEN_DATA_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_ai.llm_resume import run_llm_date_batch  # noqa: E402
from generate_ai.multi_day_llm_helpers import apply_max_records  # noqa: E402
from generate_ai.runtime import PROJECT_ROOT, bootstrap_llm, iter_uids, load_health_rows  # noqa: E402

from sleep_report.sleep_helpers import build_sleep_events_index  # noqa: E402
from sleep_report.time_utils import format_time_to_hhmm, utc_to_local  # noqa: E402


def _build_intervention_event_groups(events: List[dict]) -> List[List[dict]]:
    """找出所有 AI 主动干预事件，通过 related_event_id 找到父级事件，
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


def _extract_sleep_time_points(health_row: dict) -> dict:
    raw = health_row.get("raw_data", {})
    return {
        "bed_time": format_time_to_hhmm(utc_to_local(raw.get("bed_time", ""))),
        "sleep_onset": format_time_to_hhmm(utc_to_local(raw.get("sleep_time", ""))),
        "wake_after_sleep": format_time_to_hhmm(utc_to_local(raw.get("wake_time", ""))),
        "out_of_bed": format_time_to_hhmm(utc_to_local(raw.get("wake_up_time", ""))),
    }


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def generate_ai_intervention_for_date(
    record_date: str,
    health_row: dict,
    events: List[dict],
) -> Optional[dict]:
    """为单日生成 AI 干预重写结果。

    Args:
        record_date: 日期字符串。
        health_row:  该日的 health 记录（用于提取睡眠时间点）。
        events:      该日全部睡眠事件列表。

    Returns:
        {"sleep_time_points": {...}, "sleep_events": [...]} 或 None（调用失败时）。
        sleep_events 为空列表时表示该日无干预事件，不算失败。
    """
    from generate_ai import llm_client

    sleep_time_points = _extract_sleep_time_points(health_row)
    event_groups = _build_intervention_event_groups(events)

    if not event_groups:
        return {"sleep_time_points": sleep_time_points, "sleep_events": []}

    instruction = llm_client.load_prompt_instruction("sleep_event_ai_intervention.md")
    if not instruction:
        print("  [警告] 未找到 sleep_event_ai_intervention.md", file=sys.stderr)
        return None

    model_input = {
        "sleep_time_points": sleep_time_points,
        "events": event_groups,
    }
    prompt = (
        "以下为指定日期睡眠过程中的睡眠时间点及异常事件与 AI 主动干预事件组（JSON 对象）。"
        "sleep_time_points 包含四个睡眠时间点，events 为二维数组，每个子数组包含一个父级事件及其关联的 AI 主动干预事件。"
        "请仅重写每条记录中的 detail 字段，保持其余所有字段与结构不变，"
        "输出严格等长的二维 JSON 数组（仅输出 events 部分），不要附加任何解释。\n\n"
        + json.dumps(model_input, ensure_ascii=False)
    )
    raw = llm_client.call_qwen_api(prompt, system_prompt=instruction, max_tokens=8192, sleep_report_llm=True)
    if not raw:
        return None
    try:
        result = llm_client.parse_json_from_response(raw)
    except Exception:
        result = llm_client.parse_model_json_array(raw)

    if not isinstance(result, list):
        return None
    return {"sleep_time_points": sleep_time_points, "sleep_events": result}


def generate_ai_intervention_for_uid(
    uid: str,
    output_dir: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    retry_delay: float = 0.5,
    max_records: Optional[int] = None,
    resume: bool = False,
) -> List[dict]:
    """为单个用户批量生成每日 AI 干预重写结果。

    Returns:
        [{"uid":..., "record_date":..., "sleep_time_points":{...}, "sleep_events":[...]}, ...]
    """
    try:
        health_rows = load_health_rows(uid, output_dir)
    except FileNotFoundError:
        print(f"  [跳过] uid={uid} 无 health 文件")
        return []

    health_by_date = {str(r.get("record_date")): r for r in health_rows if r.get("record_date")}
    events_index = build_sleep_events_index(uid, output_dir=output_dir)

    dates = sorted(health_by_date.keys())
    if start_date:
        dates = [d for d in dates if d >= start_date]
    if end_date:
        dates = [d for d in dates if d <= end_date]

    dates = apply_max_records(dates, max_records)

    out_path = os.path.join(output_dir, f"{uid}_sleep_ai_intervention.json")

    def _one(rd: str) -> Optional[dict]:
        res = generate_ai_intervention_for_date(
            record_date=rd,
            health_row=health_by_date[rd],
            events=events_index.get(rd, []),
        )
        if res is None:
            return None
        return {
            "uid": uid,
            "record_date": rd,
            "sleep_time_points": res["sleep_time_points"],
            "sleep_events": res["sleep_events"],
        }

    return run_llm_date_batch(
        uid=uid,
        output_path=out_path,
        resume=resume,
        dates=dates,
        process_date=_one,
        retry_delay=retry_delay,
        is_complete=lambda r: "sleep_time_points" in r,
    )


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output-dir", default=os.path.join(PROJECT_ROOT, "output"))
    p.add_argument("--out", default=os.path.join(PROJECT_ROOT, "output", "sleep_ai_intervention.json"))
    p.add_argument("--uid", default="")
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    p.add_argument("--retry-delay", type=float, default=0.5)
    p.add_argument(
        "--max-records",
        type=int,
        default=0,
        help="在日期过滤后最多处理 N 个锚点日（0 表示不限制）",
    )
    return p.parse_args()


def main() -> None:
    bootstrap_llm()
    args = _parse_args()
    output_dir = os.path.abspath(args.output_dir)
    uids = [args.uid.strip()] if args.uid.strip() else iter_uids(output_dir)
    all_results: List[dict] = []
    for uid in uids:
        print(f"\n处理 uid={uid} …")
        all_results.extend(
            generate_ai_intervention_for_uid(
                uid=uid,
                output_dir=output_dir,
                start_date=args.start_date.strip() or None,
                end_date=args.end_date.strip() or None,
                retry_delay=args.retry_delay,
                max_records=int(args.max_records) or None,
            )
        )
    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n已写入 {len(all_results)} 条 → {out_path}")


if __name__ == "__main__":
    main()
