"""
批量生成睡眠事件环境干预分析（pain_point_analysis_module）。

对 output/{uid}_sleep_events.json 中每日「异常事件 + AI主动干预」配对组
调用 LLM，输出 pain_point_analysis_module（至多 1 条，与 prompt 模板一致，多组合并）。

作为独立脚本运行：
  python scripts/generate_data/generate_ai/generate_sleep_event_environment_intervention.py --uid <uid>

作为模块导入：
  from generate_sleep_event_environment_intervention import generate_intervention_for_uid
  results = generate_intervention_for_uid(uid, output_dir)
  # [{"uid":..., "record_date":..., "pain_point_analysis_module": [最多1个元素]}, ...]
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
from generate_ai.runtime import PROJECT_ROOT, bootstrap_llm, iter_uids  # noqa: E402

from sleep_report.sleep_helpers import build_sleep_events_index  # noqa: E402


def _build_pain_point_event_groups(events: List[dict]) -> List[List[dict]]:
    """筛选「异常事件 + 对应 AI主动干预事件」配对组。"""
    event_by_id = {
        e.get("_id"): e
        for e in events
        if isinstance(e, dict) and e.get("_id")
    }
    groups = []
    for e in events:
        if not isinstance(e, dict) or e.get("event_type") != "AI主动干预":
            continue
        related = event_by_id.get(e.get("related_event_id"))
        if related and related.get("type") == "abnormal":
            groups.append([related, e])
    return groups


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def generate_intervention_for_date(
    record_date: str,
    events: List[dict],
) -> Optional[List[dict]]:
    """为单日睡眠事件列表生成 pain_point_analysis_module。

    Args:
        record_date: 日期字符串，仅用于构造 payload。
        events:      该日全部睡眠事件。

    Returns:
        pain_point_analysis_module 列表（至多 1 条），或 None（调用失败时）。
    """
    from generate_ai import llm_client

    sleep_events = _build_pain_point_event_groups(events)
    if not sleep_events:
        return []

    instruction = llm_client.load_prompt_instruction("sleep_pain_point_analysis_template.md")
    if not instruction:
        print("  [警告] 未找到 sleep_pain_point_analysis_template.md", file=sys.stderr)
        return None

    payload = {"record_date": record_date, "sleep_events": sleep_events}
    prompt = (
        "以下为指定日期睡眠过程中检测到的异常事件与 AI 主动干预事件组（JSON 二维数组）。"
        "每个子数组包含一个异常事件及其关联的 AI 主动干预事件。"
        "请仅依据这些事件组生成 JSON 数组：须恰好 1 个元素（单个对象含 title、description），"
        "多组事件必须融合在该对象的 description 中，不得拆成多条；不要附加解释。\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    raw = llm_client.call_qwen_api(prompt, system_prompt=instruction, max_tokens=4096, sleep_report_llm=True)
    if not raw:
        return None
    try:
        mod = llm_client.parse_json_from_response(raw)
    except Exception:
        mod = llm_client.parse_model_json_array(raw)
    if isinstance(mod, list):
        return mod[:1]
    return None


def generate_intervention_for_uid(
    uid: str,
    output_dir: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    retry_delay: float = 0.5,
    max_records: Optional[int] = None,
    resume: bool = False,
) -> List[dict]:
    """为单个用户批量生成每日 pain_point_analysis_module。

    Returns:
        [{"uid":..., "record_date":..., "pain_point_analysis_module": [...]}, ...]
    """
    events_index = build_sleep_events_index(uid, output_dir=output_dir)
    if not events_index:
        print(f"  [跳过] uid={uid} 无睡眠事件数据")
        return []

    dates = sorted(events_index.keys())
    if start_date:
        dates = [d for d in dates if d >= start_date]
    if end_date:
        dates = [d for d in dates if d <= end_date]

    dates = apply_max_records(dates, max_records)

    out_path = os.path.join(output_dir, f"{uid}_sleep_event_environment_intervention.json")

    def _one(rd: str) -> Optional[dict]:
        mod = generate_intervention_for_date(rd, events_index.get(rd, []))
        if mod is None:
            return None
        return {"uid": uid, "record_date": rd, "pain_point_analysis_module": mod}

    return run_llm_date_batch(
        uid=uid,
        output_path=out_path,
        resume=resume,
        dates=dates,
        process_date=_one,
        retry_delay=retry_delay,
    )


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output-dir", default=os.path.join(PROJECT_ROOT, "output"))
    p.add_argument("--out", default=os.path.join(PROJECT_ROOT, "output", "sleep_event_environment_intervention.json"))
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
            generate_intervention_for_uid(
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
