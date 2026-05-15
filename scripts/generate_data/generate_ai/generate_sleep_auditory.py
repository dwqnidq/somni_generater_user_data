"""
批量生成睡眠听觉分析模块（quality_analysis.auditory.module）。

对 output/{uid}_health_data.json 中每条记录组装听觉上下文并调用大模型，
输出每日 auditory_module 结果。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
GEN_DATA_DIR = os.path.join(PROJECT_ROOT, "scripts", "generate_data")

for _p in (PROJECT_ROOT, GEN_DATA_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_ai.multi_day_llm_helpers import apply_max_records  # noqa: E402
from generate_ai.runtime import PROJECT_ROOT, bootstrap_llm, iter_uids, load_health_rows  # noqa: E402

from sleep_report.auditory import (  # noqa: E402
    _compact_sleep_events_for_auditory_prompt,
    _environment_samples_for_auditory_prompt,
    _normalize_auditory_module_list,
    _sleep_metrics_for_auditory_prompt,
    _snoring_data_points_for_auditory_prompt,
    generate_auditory,
    index_environment_noise_rows_by_record_date,
    rebuild_auditory_audios_and_snoring_data_points,
)
from sleep_report.sleep_helpers import build_sleep_events_index  # noqa: E402


def _build_auditory_context(
    uid: str,
    sleep_data: dict,
    record_date: str,
    output_dir: str,
    events_index: Dict[str, List[dict]],
) -> dict:
    auditory = generate_auditory(
        sleep_data,
        user_id=uid,
        sleep_events_index=events_index,
        output_dir=output_dir,
    )
    env_rows_by_date = index_environment_noise_rows_by_record_date(uid, output_dir=output_dir)
    all_events = [event for events in events_index.values() for event in events]
    audios, data_points = rebuild_auditory_audios_and_snoring_data_points(
        record_date,
        uid,
        all_events,
        sleep_data,
        env_rows_by_date.get(record_date, []),
    )
    auditory["audios"] = audios
    auditory["snoring_analysis"] = {"data_points": data_points}
    return auditory


def _generate_auditory_module_llm(
    uid: str,
    record_date: str,
    sleep_data: dict,
    sleep_events_for_date: List[dict],
    auditory_dict: dict,
    output_dir: str,
    *,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> Optional[list]:
    from generate_ai import llm_client

    if not llm_client.sleep_report_llm_enabled:
        return None
    instruction = llm_client.load_prompt_instruction("sleep_audio_analysis.md")
    if not instruction:
        print("  [警告] 读取睡眠听觉分析模板失败或为空: sleep_audio_analysis.md")
        return None

    aud = auditory_dict if isinstance(auditory_dict, dict) else {}
    sleep_metrics = _sleep_metrics_for_auditory_prompt(sleep_data)
    payload = {
        "user_id": uid,
        "record_date": record_date,
        "sleep_events": _compact_sleep_events_for_auditory_prompt(sleep_events_for_date),
        "sleep_metrics": sleep_metrics,
        "apnea_count": sleep_metrics.get("apnea_count"),
        "data_points": _snoring_data_points_for_auditory_prompt(aud),
        "environment_samples": _environment_samples_for_auditory_prompt(
            uid, record_date, output_dir=output_dir
        ),
        "auditory_audios": [
            {
                "type": a.get("type"),
                "time": a.get("time"),
                "duration_sec": a.get("duration_sec"),
            }
            for a in (aud.get("audios") or [])
            if isinstance(a, dict)
        ],
    }
    prompt = (
        "以下为本晚真实输入数据（JSON）。请仅依据这些数据进行分析，"
        "返回仅包含 1 条元素的 JSON 数组（字段仅限 `target`、`description`），不要附加任何解释。\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    eff_temperature = 0.35 if temperature is None else temperature
    result = llm_client.call_qwen_api(
        prompt,
        system_prompt=instruction,
        max_tokens=2048,
        temperature=eff_temperature,
        top_p=top_p,
        sleep_report_llm=True,
    )
    if not result:
        return None
    arr = llm_client.parse_model_json_array(result)
    normalized = _normalize_auditory_module_list(arr)
    if not normalized:
        return None
    return normalized[:1]


def generate_auditory_for_date(
    uid: str,
    sleep_data: dict,
    output_dir: str,
    *,
    events_index: Optional[Dict[str, List[dict]]] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> Optional[list]:
    rd = str(sleep_data.get("record_date") or "").strip()
    if not rd:
        return None
    if events_index is None:
        events_index = build_sleep_events_index(uid, output_dir=output_dir)
    ev = events_index.get(rd, [])
    auditory = _build_auditory_context(uid, sleep_data, rd, output_dir, events_index)
    return _generate_auditory_module_llm(
        uid, rd, sleep_data, ev, auditory, output_dir,
        temperature=temperature, top_p=top_p,
    )


def generate_auditory_for_uid(
    uid: str,
    output_dir: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    retry_delay: float = 0.5,
    max_records: Optional[int] = None,
    *,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> List[dict]:
    try:
        health_rows = load_health_rows(uid, output_dir)
    except FileNotFoundError:
        print(f"  [跳过] uid={uid} 无 health 文件")
        return []

    if not health_rows:
        print(f"  [跳过] uid={uid} health 文件为空")
        return []

    events_index = build_sleep_events_index(uid, output_dir=output_dir)
    rows = sorted(health_rows, key=lambda r: str(r.get("record_date") or ""))
    if start_date:
        rows = [r for r in rows if str(r.get("record_date") or "") >= start_date]
    if end_date:
        rows = [r for r in rows if str(r.get("record_date") or "") <= end_date]
    rows = apply_max_records(rows, max_records)

    results: List[dict] = []
    for i, row in enumerate(rows):
        rd = str(row.get("record_date") or "")
        print(f"  [{i + 1}/{len(rows)}] uid={uid} date={rd} …", end=" ", flush=True)
        mod = generate_auditory_for_date(
            uid, row, output_dir,
            events_index=events_index,
            temperature=temperature, top_p=top_p,
        )
        if mod is None:
            print("失败（已跳过）")
        else:
            print("完成")
            results.append({"uid": uid, "record_date": rd, "auditory_module": mod})
        if i < len(rows) - 1:
            time.sleep(retry_delay)
    return results


def main() -> None:
    bootstrap_llm()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output-dir", default=os.path.join(PROJECT_ROOT, "output"))
    p.add_argument("--out", default=os.path.join(PROJECT_ROOT, "output", "sleep_auditory.json"))
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
            generate_auditory_for_uid(
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
