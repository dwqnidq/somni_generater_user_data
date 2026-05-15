"""
批量生成睡眠共性洞察汇总（sleep_pattern_commonality_insight）。

读取 output/{uid}_sleep_pattern_commonality.json（每条含 sleep_pattern_commonality 数组），
对每条记录的 sleep_pattern_commonality 数组整体调用 LLM 汇总，
输出 { target, description, tips }。

输入文件结构（每条）：
{
  "uid": "...",
  "record_date": "YYYY-MM-DD",
  "sleep_pattern_commonality": [
    { "highlight": "...", "analysis": "...", "list": [ ... ] },
    ...
  ]
}

输出文件结构（每条）：
{
  "uid": "...",
  "record_date": "YYYY-MM-DD",
  "sleep_pattern_commonality_insight": {
    "target": "...",
    "description": "...",
    "tips": "..."
  }
}

作为独立脚本运行：
  python scripts/generate_data/generate_ai/generate_sleep_pattern_commonality_insight.py --uid <uid>

作为模块导入：
  from generate_sleep_pattern_commonality_insight import generate_insight_for_uid
  results = generate_insight_for_uid(uid, output_dir)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
GEN_DATA_DIR = os.path.join(PROJECT_ROOT, "scripts", "generate_data")

for _p in (PROJECT_ROOT, GEN_DATA_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_ai.multi_day_llm_helpers import (  # noqa: E402
    apply_max_records,
    backward_14_health_complete,
    merge_last_skipped,
)
from generate_ai.runtime import bootstrap_llm  # noqa: E402

DEFAULT_SYSTEM_PROMPT_PATH = os.path.join(
    PROJECT_ROOT, "prompt", "sleep_pattern_commonality_insight_summary.md"
)


def _load_commonality_by_record_date(uid: str, output_dir: str) -> dict:
    """读取 {uid}_sleep_pattern_commonality.json，按 record_date 索引。"""
    path = os.path.join(output_dir, f"{uid}_sleep_pattern_commonality.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return {}
    out: dict = {}
    for r in data:
        if isinstance(r, dict) and r.get("record_date"):
            out[str(r.get("record_date"))] = r
    return out


def _load_health_rows_insight(uid: str, output_dir: str) -> List[dict]:
    path = os.path.join(output_dir, f"{uid}_health_data.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("record_date")]


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def _coerce_commonality_to_list(commonality: object) -> list:
    """将 sleep_pattern_commonality 规范为列表（单对象历史数据包成一项）。"""
    if isinstance(commonality, list):
        return commonality
    if isinstance(commonality, dict):
        return [commonality]
    return []


def generate_insight_for_commonality(
    commonality: object,
    system_prompt_path: Optional[str] = None,
) -> Optional[dict]:
    """对单条记录的 sleep_pattern_commonality（**数组**，每项 highlight/analysis/list）调用 LLM 汇总。

    Args:
        commonality:        sleep_pattern_commonality 字段值（``list``；若为历史单 ``dict`` 会自动包成单元素列表）。
        system_prompt_path: 系统提示词路径；None 时使用默认路径。

    Returns:
        {"target": "...", "description": "...", "tips": "..."} 或 None。
    """
    from generate_ai import llm_client

    prompt_path = system_prompt_path or DEFAULT_SYSTEM_PROMPT_PATH
    if not os.path.isfile(prompt_path):
        raise FileNotFoundError(f"系统提示词不存在: {prompt_path}")
    with open(prompt_path, "r", encoding="utf-8") as f:
        instruction = f.read().strip()
    if not instruction:
        raise ValueError("系统提示词为空")

    blocks = _coerce_commonality_to_list(commonality)
    # 空数组直接返回空结果，避免无意义的 LLM 调用
    if len(blocks) == 0:
        return {"target": "", "description": "", "tips": ""}

    prompt = (
        "以下为真实输入数据（sleep_pattern_commonality 字段，**JSON 数组**，"
        "每项含 highlight、analysis，及可选的指标序列 list）。"
        "请严格按系统提示词仅输出 JSON 对象，"
        "字段固定为 target、description、tips，不要 markdown 围栏和解释。\n\n"
        + json.dumps(blocks, ensure_ascii=False)
    )
    raw = llm_client.call_qwen_api(
        prompt,
        system_prompt=instruction,
        max_tokens=512,
        temperature=0.35,
        sleep_report_llm=True,
    )
    if not raw or not raw.strip():
        return None
    try:
        parsed = llm_client.parse_json_from_response(raw)
    except Exception as e:
        print(f"  [警告] JSON 解析失败: {e}，原始: {raw[:200]}", file=sys.stderr)
        return None
    if not isinstance(parsed, dict):
        return None
    return {
        "target": parsed.get("target", ""),
        "description": parsed.get("description", ""),
        "tips": parsed.get("tips", ""),
    }


def generate_insight_for_uid(
    uid: str,
    output_dir: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    system_prompt_path: Optional[str] = None,
    retry_delay: float = 0.5,
    max_records: Optional[int] = None,
) -> Tuple[List[dict], Optional[str]]:
    """按 health 的逐日 record_date 生成 insight。

    须该日及向前连续 14 个自然日均有 health；且该日已有非空的 sleep_pattern_commonality 行。

    Returns:
        (rows, last_skipped_date)：last_skipped_date 为范围内跳过的最晚 record_date。
    """
    try:
        health_rows = _load_health_rows_insight(uid, output_dir)
    except FileNotFoundError:
        print(f"  [跳过] uid={uid} 无 health 文件")
        return [], None

    health_dates = {str(r.get("record_date")) for r in health_rows if r.get("record_date")}
    dates = sorted(health_dates)
    if start_date:
        dates = [d for d in dates if d >= start_date]
    if end_date:
        dates = [d for d in dates if d <= end_date]

    dates = apply_max_records(dates, max_records)

    try:
        by_rd = _load_commonality_by_record_date(uid, output_dir)
    except FileNotFoundError:
        print(f"  [跳过] uid={uid} 无 sleep_pattern_commonality 文件（请先运行 commonality 生成步骤）")
        return [], None

    results: List[dict] = []
    last_skipped: Optional[str] = None
    for i, rd in enumerate(dates):
        print(f"  [{i + 1}/{len(dates)}] uid={uid} date={rd} …", end=" ", flush=True)
        if not backward_14_health_complete(rd, health_dates):
            print("跳过（向前 14 天 health 不齐）")
            last_skipped = merge_last_skipped(last_skipped, rd)
            if i < len(dates) - 1:
                time.sleep(retry_delay)
            continue

        row = by_rd.get(rd)
        if not row:
            print("跳过（无 sleep_pattern_commonality 行）")
            last_skipped = merge_last_skipped(last_skipped, rd)
            if i < len(dates) - 1:
                time.sleep(retry_delay)
            continue

        commonality = row.get("sleep_pattern_commonality", [])
        blocks = _coerce_commonality_to_list(commonality)
        print(f"commonality条数={len(blocks)} …", end=" ", flush=True)
        if not blocks:
            print("跳过（共性条目为空）")
            last_skipped = merge_last_skipped(last_skipped, rd)
            if i < len(dates) - 1:
                time.sleep(retry_delay)
            continue

        try:
            insight = generate_insight_for_commonality(commonality, system_prompt_path)
        except (FileNotFoundError, ValueError) as e:
            print(f"失败（{e}）")
            last_skipped = merge_last_skipped(last_skipped, rd)
            if i < len(dates) - 1:
                time.sleep(retry_delay)
            continue

        if insight is None:
            print("LLM 调用失败（已跳过）")
            last_skipped = merge_last_skipped(last_skipped, rd)
        else:
            print("完成")
            results.append({
                "uid": uid,
                "record_date": rd,
                "sleep_pattern_commonality_insight": insight,
            })

        if i < len(dates) - 1:
            time.sleep(retry_delay)

    return results, last_skipped


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output-dir", default=os.path.join(PROJECT_ROOT, "output"))
    p.add_argument("--out", default=os.path.join(PROJECT_ROOT, "output", "sleep_pattern_commonality_insight.json"))
    p.add_argument("--uid", default="", help="仅处理该用户；默认处理目录下所有用户")
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    p.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT_PATH)
    p.add_argument("--retry-delay", type=float, default=0.5)
    p.add_argument(
        "--max-records",
        type=int,
        default=0,
        help="在日期过滤后最多处理 N 个锚点日（0 表示不限制）",
    )
    return p.parse_args()


def _iter_uids(output_dir: str) -> List[str]:
    return sorted(
        name.replace("_sleep_pattern_commonality.json", "")
        for name in os.listdir(output_dir)
        if name.endswith("_sleep_pattern_commonality.json")
    )


def main() -> None:
    bootstrap_llm()
    args = _parse_args()
    output_dir = os.path.abspath(args.output_dir)
    uids = [args.uid.strip()] if args.uid.strip() else _iter_uids(output_dir)
    if not uids:
        print("未找到任何 *_sleep_pattern_commonality.json", file=sys.stderr)
        sys.exit(1)

    all_results: List[dict] = []
    for uid in uids:
        print(f"\n处理 uid={uid} …")
        all_results.extend(
            generate_insight_for_uid(
                uid=uid,
                output_dir=output_dir,
                start_date=args.start_date.strip() or None,
                end_date=args.end_date.strip() or None,
                system_prompt_path=args.system_prompt or None,
                retry_delay=args.retry_delay,
                max_records=int(args.max_records) or None,
            )[0]
        )
    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n已写入 {len(all_results)} 条 → {out_path}")


if __name__ == "__main__":
    main()
