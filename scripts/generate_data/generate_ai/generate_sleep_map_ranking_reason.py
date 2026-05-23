"""
睡眠地图个人排名原因（ranking_reason）LLM 生成。

输入：五大维度得分 + 城市综合均分（见 prompt/sleep_map_ranking_reason_analysis.md）。
输出：{"ranking_reason": "..."}，可写入 somni_sleep_analysis.evaluation。

数据来源：output/{uid}_somni_sleep_analysis.json 中单条 stats_date 记录的 dimensions。

库用法：
  from generate_sleep_map_ranking_reason import (
      build_ranking_reason_user_payload,
      generate_ranking_reason_for_analysis_row,
  )

预览：
  python scripts/preview_llm_one_shot/preview_sleep_map_ranking_reason_analysis.py \\
      --user-id <uid> --stats-date 2026-04-18
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

from generate_ai.llm_resume import run_llm_date_batch  # noqa: E402
from generate_ai.multi_day_llm_helpers import apply_max_records  # noqa: E402
from generate_ai.runtime import bootstrap_llm, iter_uids  # noqa: E402

DIMENSION_KEYS = (
    "deep_sleep",
    "sleep_duration",
    "sleep_efficiency",
    "abnormal_events",
    "routine_regularity",
)

WEIGHTS = {
    "deep_sleep": 0.25,
    "sleep_duration": 0.25,
    "abnormal_events": 0.20,
    "sleep_efficiency": 0.15,
    "routine_regularity": 0.15,
}


def weighted_composite_score(scores: Dict[str, float]) -> int:
    total = sum(float(scores.get(k) or 0) * WEIGHTS[k] for k in DIMENSION_KEYS)
    return int(round(total))


def _dim_score(dimensions: dict, key: str) -> Optional[int]:
    block = dimensions.get(key) if isinstance(dimensions, dict) else None
    if not isinstance(block, dict):
        return None
    try:
        return int(block.get("score"))
    except (TypeError, ValueError):
        return None


def _dim_city_score(dimensions: dict, key: str) -> Optional[int]:
    block = dimensions.get(key) if isinstance(dimensions, dict) else None
    if not isinstance(block, dict):
        return None
    try:
        return int(block.get("city_score"))
    except (TypeError, ValueError):
        return None


def build_ranking_reason_user_payload(analysis_row: dict) -> dict:
    """从 somni_sleep_analysis 单条记录组装 LLM user JSON。"""
    dimensions = analysis_row.get("dimensions")
    if not isinstance(dimensions, dict):
        raise ValueError("analysis_row 缺少有效的 dimensions")

    payload: Dict[str, int] = {}
    city_scores: Dict[str, int] = {}
    for key in DIMENSION_KEYS:
        sc = _dim_score(dimensions, key)
        if sc is None:
            raise ValueError(f"dimensions.{key}.score 无效或缺失")
        payload[key] = sc
        city_sc = _dim_city_score(dimensions, key)
        if city_sc is None:
            raise ValueError(f"dimensions.{key}.city_score 无效或缺失")
        city_scores[key] = city_sc
        payload[f"city_{key}"] = city_sc

    payload["city_avg_score"] = weighted_composite_score(
        {k: float(v) for k, v in city_scores.items()}
    )
    return payload


def load_somni_sleep_analysis_row(
    uid: str,
    stats_date: str,
    output_dir: str,
) -> dict:
    path = os.path.join(output_dir, f"{uid}_somni_sleep_analysis.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        raise ValueError(f"{path} 应为 JSON 数组")
    sd = str(stats_date or "").strip()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("uid") or "").strip() != uid:
            continue
        if str(row.get("stats_date") or "").strip() == sd:
            return row
    raise ValueError(f"{path} 中未找到 uid={uid} stats_date={sd} 的记录")


def generate_ranking_reason_from_user_payload(
    user_payload: dict,
    *,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> Optional[dict]:
    """对已组装的 user JSON 调用 LLM，返回 {"ranking_reason": "..."}。"""
    from generate_ai import llm_client

    if not llm_client.sleep_report_llm_enabled:
        return None

    instruction = llm_client.load_prompt_instruction("sleep_map_ranking_reason_analysis.md")
    if not instruction:
        print("  [警告] 读取模板失败或为空: sleep_map_ranking_reason_analysis.md")
        return None

    eff_temperature = 0.35 if temperature is None else temperature
    raw = llm_client.call_qwen_api(
        json.dumps(user_payload, ensure_ascii=False),
        system_prompt=instruction,
        max_tokens=512,
        temperature=eff_temperature,
        top_p=top_p,
        sleep_report_llm=True,
    )
    if not raw.strip():
        return None
    try:
        parsed = llm_client.parse_json_from_response(raw)
    except Exception as e:
        print(f"  [警告] 解析 ranking_reason 返回失败: {e}")
        return None
    if not isinstance(parsed, dict):
        return None
    reason = str(parsed.get("ranking_reason") or "").strip()
    if not reason:
        return None
    return {"ranking_reason": reason}


def generate_ranking_reason_for_analysis_row(
    analysis_row: dict,
    *,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> Optional[dict]:
    """对单条 somni_sleep_analysis 调用 LLM，返回 {"ranking_reason": "..."}。"""
    try:
        user_payload = build_ranking_reason_user_payload(analysis_row)
    except ValueError as e:
        print(f"  [警告] 组装排名原因输入失败: {e}")
        return None
    return generate_ranking_reason_from_user_payload(
        user_payload, temperature=temperature, top_p=top_p
    )


def generate_ranking_reason_for_uid(
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
    path = os.path.join(output_dir, f"{uid}_somni_sleep_analysis.json")
    if not os.path.isfile(path):
        print(f"  [跳过] uid={uid} 无 {path}")
        return []

    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        print(f"  [跳过] {path} 格式无效")
        return []

    candidates = [
        r
        for r in rows
        if isinstance(r, dict) and str(r.get("uid") or "").strip() == uid
    ]
    candidates.sort(key=lambda r: str(r.get("stats_date") or ""))
    if start_date:
        candidates = [r for r in candidates if str(r.get("stats_date") or "") >= start_date]
    if end_date:
        candidates = [r for r in candidates if str(r.get("stats_date") or "") <= end_date]
    candidates = apply_max_records(candidates, max_records)

    by_sd = {str(r.get("stats_date") or ""): r for r in candidates if r.get("stats_date")}
    out_path = os.path.join(output_dir, f"{uid}_sleep_map_ranking_reason.json")

    def _one(sd: str) -> Optional[dict]:
        result = generate_ranking_reason_for_analysis_row(
            by_sd[sd], temperature=temperature, top_p=top_p
        )
        if not result:
            return None
        return {"uid": uid, "stats_date": sd, **result}

    return run_llm_date_batch(
        uid=uid,
        output_path=out_path,
        resume=resume,
        dates=sorted(by_sd.keys()),
        process_date=_one,
        date_key="stats_date",
        retry_delay=retry_delay,
        is_complete=lambda r: bool(str(r.get("ranking_reason") or "").strip()),
    )


def main() -> None:
    bootstrap_llm()
    ap = argparse.ArgumentParser(description="批量生成睡眠地图排名原因 ranking_reason")
    ap.add_argument("--uid", default="", help="用户 ID；省略则处理 output 下全部 uid")
    ap.add_argument("--output-dir", default="output", help="相对工程根目录")
    ap.add_argument("--start-date", default="", help="stats_date 下限 YYYY-MM-DD")
    ap.add_argument("--end-date", default="", help="stats_date 上限 YYYY-MM-DD")
    ap.add_argument("--max-records", type=int, default=0, help="最多处理条数，0 表示不限制")
    args = ap.parse_args()

    output_dir = os.path.join(PROJECT_ROOT, args.output_dir)
    uids = [args.uid.strip()] if args.uid.strip() else list(iter_uids(output_dir))
    max_records = args.max_records or None
    start = args.start_date.strip() or None
    end = args.end_date.strip() or None

    all_results: List[dict] = []
    for uid in uids:
        rows = generate_ranking_reason_for_uid(
            uid,
            output_dir,
            start_date=start,
            end_date=end,
            max_records=max_records,
        )
        all_results.extend(rows)
        print(f"uid={uid} 生成 {len(rows)} 条 ranking_reason")

    out_path = os.path.join(output_dir, "sleep_map_ranking_reason_llm.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"已写入 {out_path}（共 {len(all_results)} 条）")


if __name__ == "__main__":
    main()
