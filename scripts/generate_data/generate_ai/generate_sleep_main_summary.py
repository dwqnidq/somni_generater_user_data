"""
批量生成睡眠主摘要（main.summary）。

读取 output/{uid}_health_data.json 与 {uid}_sleep_events.json，
对每条记录调用 LLM 生成 main.summary。

主摘要标签与睡眠报告一致：默认从 **output/{uid}_sleep_report.json** 中
同 ``record_date`` 条目的 ``main.title`` 读取；不再读取 config/config.json。

标签解析顺序（每条 record_date）：
  1. ``{uid}_sleep_report.json`` → ``main.title``；
  2. 参数 ``main_title_by_record_date``（显式覆盖睡眠报告标题，可选）；
  3. health 行 ``main_title`` / ``sleep_main_label``；
  4. 若仍为空，回退 ``pick_main_title("M-L-C", ...)``。

作为独立脚本运行：
  python scripts/generate_data/generate_ai/generate_sleep_main_summary.py --uid <uid>

作为模块导入：
  from generate_sleep_main_summary import generate_main_summary_for_uid
  results = generate_main_summary_for_uid(uid, output_dir)
  # [{"uid":..., "record_date":..., "main": {"title":..., "summary":...}}, ...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Dict, List, Optional

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
from sleep_report.sleep_score import sleep_report_structure_minutes_and_percents  # noqa: E402
from sleep_report.title_summary import pick_main_title  # noqa: E402


def _load_sleep_report_main_titles(uid: str, output_dir: str) -> Dict[str, str]:
    """从 output/{uid}_sleep_report.json 提取 record_date -> main.title。"""
    path = os.path.join(output_dir, f"{uid}_sleep_report.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, list):
        return {}
    out: Dict[str, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        rd = str(item.get("record_date") or "").strip()
        if not rd:
            continue
        main = item.get("main")
        if not isinstance(main, dict):
            continue
        t = str(main.get("title") or "").strip()
        if t:
            out[rd] = t
    return out


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def generate_main_summary_for_date(
    uid: str,
    sleep_data: dict,
    sleep_events_index: dict,
    main_title: Optional[str] = None,
) -> Optional[dict]:
    """为单条 health 数据生成 main.title + main.summary。

    Args:
        uid:                用户 ID（部分函数需要）。
        sleep_data:         一条 health 记录。
        sleep_events_index: 由 gh.build_sleep_events_index 生成的按日期索引。
        main_title:         主摘要标签（中文短标题）。若为空则回退 pick_main_title("M-L-C", …)。

    Returns:
        {"title": "...", "summary": "..."} 或 None。
    """
    from generate_ai import llm_client

    rd = str(sleep_data.get("record_date") or "")
    date_sleep_events = sleep_events_index.get(rd, [])

    raw = sleep_data.get("raw_data") or {}
    (
        _a, _d, _l, _r,
        _pie_aw, _pie_d, _pie_l, _pie_r,
        _awake_percent, deep_percent, light_percent, rem_percent,
    ) = sleep_report_structure_minutes_and_percents(sleep_data)

    sleep_latency = raw.get("sleep_latency", 0)
    sleep_efficiency = raw.get("sleep_efficiency", 0)
    apnea_count = int(raw.get("apnea_count", 0) or 0)

    label = (main_title or "").strip()
    if not label:
        label = pick_main_title(
            "M-L-C",
            sleep_data,
            light_percent,
            deep_percent,
            rem_percent,
            sleep_latency,
            sleep_efficiency,
            apnea_count,
            recent_titles=None,
        )

    event_lines = [
        {
            "time": e.get("event_timestamp", ""),
            "type": e.get("event_type", ""),
            "code": e.get("code", ""),
            "detail": e.get("detail", {}),
        }
        for e in date_sleep_events
    ]

    sleep_data_for_prompt = {
        "title": label,
        "sleep_data": {
            "raw_data": sleep_data.get("raw_data", {}),
            "idf_data": sleep_data.get("idf_data", []),
        },
        "sleep_events": event_lines,
    }

    system = llm_client.render_prompt_template(
        "generate_health_data__main_summary_general.md",
        {
            "SLEEP_LABEL": label,
            "SLEEP_DATA_JSON": json.dumps(sleep_data_for_prompt, ensure_ascii=False),
        },
    )
    user_msg = (
        '请严格依据 system 提示末尾「本次任务的真实输入」中的 JSON 作答；'
        '仅输出 JSON：{"title":"...","summary":"..."}；'
        "title 须与输入 JSON 顶层的 title 完全一致；"
        "summary 中的数字须来自该 JSON，勿照抄文档示例；"
        "不要 markdown 围栏或解释。"
    )
    raw_out = llm_client.call_qwen_api(
        user_msg,
        system_prompt=system,
        max_tokens=512,
        temperature=0.35,
        enable_thinking=False,
        sleep_report_llm=True,
    )
    if not raw_out or not raw_out.strip():
        return None
    try:
        parsed = llm_client.parse_json_from_response(raw_out)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    summary = str(parsed.get("summary", "")).strip()
    if not summary:
        return None
    parsed_title = str(parsed.get("title", "")).strip()
    final_title = parsed_title if parsed_title == label else label
    return {"title": final_title, "summary": summary}


def generate_main_summary_for_uid(
    uid: str,
    output_dir: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    main_title_by_record_date: Optional[Dict[str, str]] = None,
    retry_delay: float = 0.5,
    max_records: Optional[int] = None,
    resume: bool = False,
) -> List[dict]:
    """为单个用户批量生成每日 main summary。

    Args:
        main_title_by_record_date: 可选，``record_date -> 标签``；若提供则**覆盖**同日期睡眠报告中的 ``main.title``。

    Returns:
        [{"uid":..., "record_date":..., "main": {"title":..., "summary":...}}, ...]
    """
    try:
        health_rows = load_health_rows(uid, output_dir)
    except FileNotFoundError:
        print(f"  [跳过] uid={uid} 无 health 文件")
        return []

    if not health_rows:
        print(f"  [跳过] uid={uid} health 文件为空")
        return []

    sleep_events_index = build_sleep_events_index(uid, output_dir=output_dir)
    titles_map = main_title_by_record_date or {}
    report_titles = _load_sleep_report_main_titles(uid, output_dir)

    rows = sorted(health_rows, key=lambda r: str(r.get("record_date") or ""))
    if start_date:
        rows = [r for r in rows if str(r.get("record_date") or "") >= start_date]
    if end_date:
        rows = [r for r in rows if str(r.get("record_date") or "") <= end_date]

    rows = apply_max_records(rows, max_records)

    by_rd = {str(r.get("record_date") or ""): r for r in rows if r.get("record_date")}
    out_path = os.path.join(output_dir, f"{uid}_sleep_main_summary.json")

    def _one(rd: str) -> Optional[dict]:
        row = by_rd[rd]
        passed_title = str(report_titles.get(rd) or "").strip()
        if not passed_title:
            passed_title = str(titles_map.get(rd) or "").strip()
        if not passed_title:
            passed_title = str(row.get("main_title") or row.get("sleep_main_label") or "").strip()
        main = generate_main_summary_for_date(
            uid, row, sleep_events_index, main_title=passed_title or None
        )
        if main is None:
            return None
        return {"uid": uid, "record_date": rd, "main": main}

    return run_llm_date_batch(
        uid=uid,
        output_path=out_path,
        resume=resume,
        dates=sorted(by_rd.keys()),
        process_date=_one,
        retry_delay=retry_delay,
    )


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output-dir", default=os.path.join(PROJECT_ROOT, "output"))
    p.add_argument("--out", default=os.path.join(PROJECT_ROOT, "output", "sleep_main_summary.json"))
    p.add_argument("--uid", default="")
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    p.add_argument("--retry-delay", type=float, default=0.5)
    p.add_argument(
        "--max-records",
        type=int,
        default=0,
        help="在日期过滤后最多处理 N 条 health 行（0 表示不限制）",
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
            generate_main_summary_for_uid(
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
