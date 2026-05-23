"""
批量生成睡眠质量分析模块（quality_analysis_module）。
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

from sleep_report.sleep_score import sleep_report_score_from_sleep_data  # noqa: E402
from sleep_report.time_utils import format_time_to_hhmm, utc_to_local  # noqa: E402


def generate_quality_for_date(
    sleep_data: dict,
    *,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> Optional[list]:
    from generate_ai import llm_client

    if not llm_client.sleep_report_llm_enabled:
        return None
    instruction = llm_client.load_prompt_instruction("sleep_quality_analysis_template.md")
    if not instruction:
        print("  [警告] 读取质量分析模板失败或为空: sleep_quality_analysis_template.md")
        return None

    raw = sleep_data.get("raw_data", {})
    bed_time_local = utc_to_local(raw.get("bed_time", ""))
    wake_up_time_local = utc_to_local(raw.get("wake_up_time", ""))
    payload = {
        "sleep_data": {
            "record_date": sleep_data.get("record_date", ""),
            "apnea_count": raw.get("apnea_count", 0),
            "average_heartbeat": raw.get("average_heartbeat", 0),
            "average_respiration": raw.get("average_respiration", 0),
            "awake_ratio": raw.get("awake_ratio", 0),
            "deep_sleep_ratio": raw.get("deep_sleep_ratio", 0),
            "light_sleep_ratio": raw.get("light_sleep_ratio", 0),
            "rem_ratio": raw.get("rem_ratio", 0),
            "sleep_score": sleep_report_score_from_sleep_data(sleep_data),
            "total_sleep_minutes": raw.get("total_sleep_minutes", 0),
            "bed_time": format_time_to_hhmm(bed_time_local),
            "wake_up_time": format_time_to_hhmm(wake_up_time_local),
            "sleep_latency": raw.get("sleep_latency", 0),
            "sleep_efficiency": raw.get("sleep_efficiency", 0),
        },
        "schedule_data": [],
        "environment_data": [],
        "vitals_data": [],
    }
    prompt = (
        "以下为本晚真实输入数据（JSON）。请仅依据这些数据进行分析，"
        "返回仅包含 1 条元素的 JSON 数组（字段仅限 `target`、`description`），不要附加任何解释。\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    result = llm_client.call_qwen_api(
        prompt,
        system_prompt=instruction,
        max_tokens=4096,
        temperature=temperature,
        top_p=top_p,
        sleep_report_llm=True,
    )
    if not result:
        return None
    try:
        parsed = llm_client.parse_json_from_response(result)
    except Exception as e:
        print(f"  [警告] 解析质量分析返回内容失败，尝试宽松提取: {e}")
        parsed = llm_client.parse_model_json_array(result)
    if parsed is None:
        parsed = llm_client.parse_model_json_array(result)
    if not isinstance(parsed, list):
        print("  [警告] 质量分析模型返回不是 JSON 数组")
        return None
    normalized = llm_client.normalize_target_description_modules(parsed)
    if not normalized:
        return None
    return normalized[:1]


def generate_quality_for_uid(
    uid: str,
    output_dir: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    retry_delay: float = 0.5,
    max_records: Optional[int] = None,
    *,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    resume: bool = False,
) -> List[dict]:
    try:
        health_rows = load_health_rows(uid, output_dir)
    except FileNotFoundError:
        print(f"  [跳过] uid={uid} 无 health 文件")
        return []
    if not health_rows:
        print(f"  [跳过] uid={uid} health 文件为空")
        return []

    rows = sorted(health_rows, key=lambda r: str(r.get("record_date") or ""))
    if start_date:
        rows = [r for r in rows if str(r.get("record_date") or "") >= start_date]
    if end_date:
        rows = [r for r in rows if str(r.get("record_date") or "") <= end_date]
    rows = apply_max_records(rows, max_records)

    by_rd = {str(r.get("record_date") or ""): r for r in rows if r.get("record_date")}
    out_path = os.path.join(output_dir, f"{uid}_sleep_quality.json")

    def _one(rd: str) -> Optional[dict]:
        mod = generate_quality_for_date(
            by_rd[rd], temperature=temperature, top_p=top_p
        )
        if mod is None:
            return None
        return {"uid": uid, "record_date": rd, "quality_analysis_module": mod}

    return run_llm_date_batch(
        uid=uid,
        output_path=out_path,
        resume=resume,
        dates=sorted(by_rd.keys()),
        process_date=_one,
        retry_delay=retry_delay,
    )


def main() -> None:
    bootstrap_llm()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output-dir", default=os.path.join(PROJECT_ROOT, "output"))
    p.add_argument("--out", default=os.path.join(PROJECT_ROOT, "output", "sleep_quality.json"))
    p.add_argument("--uid", default="")
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    p.add_argument("--retry-delay", type=float, default=0.5)
    p.add_argument("--max-records", type=int, default=0)
    args = p.parse_args()
    output_dir = os.path.abspath(args.output_dir)
    uids = [args.uid.strip()] if args.uid.strip() else iter_uids(output_dir)
    all_results: List[dict] = []
    for uid in uids:
        print(f"\n处理 uid={uid} …")
        all_results.extend(
            generate_quality_for_uid(
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
