"""
批量生成「近14天睡眠共性分析」（sleep_pattern_commonality）。

读取 output 目录下每个用户的健康、环境、日历、情绪步数数据，
以每个 record_date 为锚点向前取 14 天窗口，调用大模型生成
共性条目列表（每项为 { highlight, analysis, type, list }），汇总写入输出文件。

输出格式（列表，每元素对应一个 uid × record_date）：
[
  {
    "uid": "...",
    "record_date": "YYYY-MM-DD",
    "sleep_pattern_commonality": [
      { "highlight": "...", "analysis": "...", "type": "sleep_latency", "list": [ ... ] },
      ...
    ]
  },
  ...
]

作为独立脚本运行：
  python scripts/generate_data/generate_sleep_pattern_commonality.py
  python scripts/generate_data/generate_sleep_pattern_commonality.py \\
      --output-dir output --uid <uid> \\
      --out output/sleep_pattern_commonality.json \\
      --start-date 2025-01-01 --end-date 2025-01-31

作为模块导入（在其他脚本中使用）：
  from generate_sleep_pattern_commonality import (
      generate_commonality_for_date,   # 生成单条
      generate_for_uid,                # 生成某用户所有日期
      build_14d_payload,               # 仅构建数据载荷（不调 LLM）
  )

  # 示例：在已初始化好环境的批量脚本中直接调用
  result = generate_commonality_for_date(
      uid="abc123",
      anchor_date="2025-01-15",
      output_dir="/path/to/output",
  )
  # result -> [{"highlight": "...", "analysis": "...", "list": [...]}, ...] 或 None
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# generate_ai/ -> generate_data/ -> scripts/ -> project_root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
GEN_DATA_DIR = os.path.join(PROJECT_ROOT, "scripts", "generate_data")

# 将工程根与 generate_data 加入 sys.path，支持直接运行与被导入两种场景
for _p in (PROJECT_ROOT, GEN_DATA_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_ai.llm_resume import run_llm_date_batch  # noqa: E402
from generate_ai.multi_day_llm_helpers import (  # noqa: E402
    apply_max_records,
    backward_14_health_complete,
    merge_last_skipped,
)
from generate_ai.runtime import bootstrap_llm  # noqa: E402

DEFAULT_SYSTEM_PROMPT_PATH = os.path.join(
    PROJECT_ROOT, "prompt", "sleep_pattern_14d_commonality_analysis.md"
)


# ---------------------------------------------------------------------------
# LLM 输出归一化
# ---------------------------------------------------------------------------


def _coerce_commonality_metric_list(val) -> List[Optional[float]]:
    """将模型返回的 ``list`` 规范为与 JSON 兼容的数值序列（缺失为 None）。"""
    if not isinstance(val, list):
        return []
    out: List[Optional[float]] = []
    for x in val:
        if x is None:
            out.append(None)
        elif isinstance(x, bool):
            out.append(None)
        elif isinstance(x, (int, float)):
            out.append(float(x))
        elif isinstance(x, str):
            s = x.strip()
            if not s:
                out.append(None)
            else:
                try:
                    out.append(float(s))
                except ValueError:
                    out.append(None)
        else:
            out.append(None)
    return out


def normalize_sleep_pattern_commonality_llm_dict(parsed: dict) -> List[dict]:
    """将模型返回的 JSON 对象规范为 [{highlight, analysis, type, list}, ...]（持久化字段始终为数组）。

    优先使用 ``items`` 数组；若 ``items`` 为显式空列表 ``[]``，返回 ``[]``（无稳健共性）。
    若无 ``items`` 键或非列表，或 ``items`` 内无有效元素，则回退顶层 ``highlight`` / ``analysis``（兼容旧模型输出，归一为单元素数组）。
    ``list`` 为与 ``sleep_records_14d`` 同序的指标数值；缺失或非数组时得到空列表。
    ``type`` 为 list 中数据类型的枚举标识；缺失时为空字符串。
    """
    if not isinstance(parsed, dict):
        return []
    items = parsed.get("items")
    if isinstance(items, list):
        if not items:
            return []
        out: List[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            out.append({
                "highlight": str(it.get("highlight", "")),
                "analysis": str(it.get("analysis", "")),
                "type": str(it.get("type", "")),
                "list": _coerce_commonality_metric_list(it.get("list")),
            })
        if out:
            return out
    return [{
        "highlight": str(parsed.get("highlight", "")),
        "analysis": str(parsed.get("analysis", "")),
        "type": str(parsed.get("type", "")),
        "list": _coerce_commonality_metric_list(parsed.get("list")),
    }]


# ---------------------------------------------------------------------------
# 数据加载与预处理
# ---------------------------------------------------------------------------

def _load_health_rows(uid: str, output_dir: str) -> List[dict]:
    path = os.path.join(output_dir, f"{uid}_health_data.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("record_date")]


def _load_json_list_optional(path: str) -> List[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def _rollup_environment_by_record_date(env_rows: List[dict]) -> List[dict]:
    by_rd: Dict[str, list] = defaultdict(list)
    for r in env_rows:
        rd = str(r.get("record_date") or "")
        if rd:
            by_rd[rd].append(r)

    def _stats(values: list) -> dict:
        if not values:
            return {}
        return {
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "mean": round(sum(values) / len(values), 2),
        }

    out: List[dict] = []
    for rd in sorted(by_rd.keys()):
        pts = by_rd[rd]
        uid_val = next((str(p.get("uid")) for p in pts if p.get("uid")), "")
        temp = [float(p["temperature"]) for p in pts if p.get("temperature") is not None]
        hum = [float(p["humidity"]) for p in pts if p.get("humidity") is not None]
        ill = [float(p["illuminance"]) for p in pts if p.get("illuminance") is not None]
        nz = [float(p["noise"]) for p in pts if p.get("noise") is not None]
        out.append({
            "record_date": rd,
            "uid": uid_val,
            "sample_count": len(pts),
            "temperature": _stats(temp),
            "humidity": _stats(hum),
            "illuminance": _stats(ill),
            "noise": _stats(nz),
        })
    return out


def _compact_sleep_record(row: dict) -> dict:
    raw = row.get("raw_data") or {}
    return {
        "record_date": row.get("record_date"),
        "apnea_count": raw.get("apnea_count"),
        "average_heartbeat": raw.get("average_heartbeat"),
        "average_respiration": raw.get("average_respiration"),
        "awake_ratio": raw.get("awake_ratio"),
        "deep_sleep_ratio": raw.get("deep_sleep_ratio"),
        "light_sleep_ratio": raw.get("light_sleep_ratio"),
        "rem_ratio": raw.get("rem_ratio"),
        "sleep_score": raw.get("sleep_score"),
        "total_sleep_minutes": raw.get("total_sleep_minutes"),
        "sleep_time": raw.get("sleep_time"),
        "wake_time": raw.get("wake_time"),
        "sleep_latency": raw.get("sleep_latency"),
        "sleep_efficiency": raw.get("sleep_efficiency"),
    }


def _filter_rows_by_date_key(
    rows: List[dict], date_key: str, start_date: str, end_date: str
) -> List[dict]:
    out = [
        row for row in rows
        if start_date <= str(row.get(date_key) or "") <= end_date
    ]
    out.sort(key=lambda x: str(x.get(date_key) or ""))
    return out


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def build_14d_payload(
    uid: str,
    anchor_date: str,
    output_dir: str,
    health_rows: Optional[List[dict]] = None,
) -> dict:
    """构建传给 LLM 的 14 天数据载荷（不调用 LLM）。

    Args:
        uid:          用户 ID。
        anchor_date:  锚点日期（YYYY-MM-DD），窗口为 [anchor-13d, anchor]。
        output_dir:   健康数据目录（绝对路径或相对 cwd）。
        health_rows:  可选，已加载的 health 行列表；为 None 时自动从文件读取。

    Returns:
        包含 sleep_records_14d / environment_data / calendar_events /
        daily_emotion_steps 的字典。
    """
    if health_rows is None:
        health_rows = _load_health_rows(uid, output_dir)

    anchor_dt = datetime.strptime(anchor_date, "%Y-%m-%d")
    start_date = (anchor_dt - timedelta(days=13)).strftime("%Y-%m-%d")

    selected = [
        row for row in health_rows
        if start_date <= str(row.get("record_date")) <= anchor_date
    ]
    selected.sort(key=lambda x: str(x.get("record_date") or ""))

    if not selected:
        raise ValueError(f"uid={uid} 在 {start_date}~{anchor_date} 无睡眠数据")

    env_path = os.path.join(output_dir, f"{uid}_environment_data.json")
    cal_path = os.path.join(output_dir, f"{uid}_calendar_events.json")
    des_path = os.path.join(output_dir, f"{uid}_daily_emotion_steps.json")

    env_rollup = _rollup_environment_by_record_date(
        _filter_rows_by_date_key(
            _load_json_list_optional(env_path), "record_date", start_date, anchor_date
        )
    )

    return {
        "anchor_record_date": anchor_date,
        "analysis_period": {"start": start_date, "end": anchor_date},
        "sleep_records_14d": [_compact_sleep_record(r) for r in selected],
        "environment_data": env_rollup,
        "calendar_events": _filter_rows_by_date_key(
            _load_json_list_optional(cal_path), "event_date", start_date, anchor_date
        ),
        "daily_emotion_steps": _filter_rows_by_date_key(
            _load_json_list_optional(des_path), "event_date", start_date, anchor_date
        ),
    }


def generate_commonality_for_date(
    uid: str,
    anchor_date: str,
    output_dir: str,
    system_prompt_path: Optional[str] = None,
    health_rows: Optional[List[dict]] = None,
) -> Optional[List[dict]]:
    """为单个 uid × anchor_date 生成 sleep_pattern_commonality。

    Args:
        uid:                用户 ID。
        anchor_date:        锚点日期（YYYY-MM-DD）。
        output_dir:         健康数据目录。
        system_prompt_path: 系统提示词文件路径；None 时使用默认路径。
        health_rows:        可选，已加载的 health 行列表，避免重复 IO。

    Returns:
        [{ "highlight": "...", "analysis": "...", "list": [...] }, ...] 或 None（调用失败时）。
    """
    from generate_ai import llm_client

    prompt_path = system_prompt_path or DEFAULT_SYSTEM_PROMPT_PATH
    if not os.path.isfile(prompt_path):
        raise FileNotFoundError(f"系统提示词不存在: {prompt_path}")
    with open(prompt_path, "r", encoding="utf-8") as f:
        instruction = f.read().strip()
    if not instruction:
        raise ValueError("系统提示词为空")

    payload = build_14d_payload(uid, anchor_date, output_dir, health_rows=health_rows)

    user_prompt = (
        "以下为真实输入数据（JSON），请严格按系统提示词仅输出 JSON 对象，"
        "且必须使用 items 数组承载一条或多条共性（每项含 highlight、analysis、list）；"
        "list 为与 sleep_records_14d 同日期顺序的数值数组，长度须与睡眠序列条数一致，"
        "元素对该条聚焦的指标从输入逐日抄录（尽量为 number，缺失用 null）。"
        "仅一条时 items 长度为 1。不要 markdown 围栏和解释。\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    raw_out = llm_client.call_qwen_api(
        user_prompt,
        system_prompt=instruction,
        max_tokens=1536,
        temperature=0.35,
        sleep_report_llm=True,
    )
    if not raw_out or not raw_out.strip():
        return None
    try:
        parsed = llm_client.parse_json_from_response(raw_out)
    except Exception as e:
        print(f"  [警告] JSON 解析失败: {e}，原始: {raw_out[:200]}", file=sys.stderr)
        return None
    if not isinstance(parsed, dict):
        return None
    return normalize_sleep_pattern_commonality_llm_dict(parsed)


def generate_for_uid(
    uid: str,
    output_dir: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    system_prompt_path: Optional[str] = None,
    retry_delay: float = 0.5,
    max_records: Optional[int] = None,
    resume: bool = False,
) -> Tuple[List[dict], Optional[str]]:
    """为单个用户的所有（或过滤后的）日期批量生成 sleep_pattern_commonality。

    Args:
        uid:                用户 ID。
        output_dir:         健康数据目录。
        start_date:         仅处理 >= 该日期（含），None 表示不限。
        end_date:           仅处理 <= 该日期（含），None 表示不限。
        system_prompt_path: 系统提示词文件路径；None 时使用默认路径。
        retry_delay:        每次 LLM 调用后的间隔秒数。
        max_records:        在 start/end 过滤后最多处理 N 个锚点日；None 或 <=0 表示不限制。

    Returns:
        (rows, last_skipped_date)：rows 为
        [{"uid": ..., "record_date": ..., "sleep_pattern_commonality": [...]}, ...]；
        last_skipped_date 为窗口内因数据窗口不足或 LLM 失败而跳过的最晚 record_date。
    """
    try:
        health_rows = _load_health_rows(uid, output_dir)
    except FileNotFoundError:
        print(f"  [跳过] uid={uid} 无 health 文件")
        return [], None

    if not health_rows:
        print(f"  [跳过] uid={uid} health 文件为空")
        return [], None

    dates = sorted({str(r.get("record_date")) for r in health_rows if r.get("record_date")})
    if start_date:
        dates = [d for d in dates if d >= start_date]
    if end_date:
        dates = [d for d in dates if d <= end_date]

    dates = apply_max_records(dates, max_records)

    health_dates = {str(r.get("record_date")) for r in health_rows if r.get("record_date")}
    skipped_14: list[str] = []
    eligible: list[str] = []
    for d in dates:
        if backward_14_health_complete(d, health_dates):
            eligible.append(d)
        else:
            skipped_14.append(d)
    if skipped_14:
        print(
            f"  [共性] 跳过 {len(skipped_14)} 日（锚点及向前 14 个自然日 health 不齐）: "
            f"{skipped_14[0]} … {skipped_14[-1]}",
            flush=True,
        )
    dates = eligible

    if not dates:
        print(
            f"  [跳过] uid={uid} 过滤后无可用锚点日"
            f"（start={start_date or '—'} end={end_date or '—'}；"
            f"或全部因 14 日窗口不足被跳过）",
            flush=True,
        )
        return [], None

    out_path = os.path.join(output_dir, f"{uid}_sleep_pattern_commonality.json")
    last_skipped: Optional[str] = None
    if skipped_14:
        last_skipped = skipped_14[-1]

    def _on_soft_fail(d: str) -> None:
        nonlocal last_skipped
        last_skipped = merge_last_skipped(last_skipped, d)

    def _one(anchor_date: str) -> Optional[dict]:
        try:
            commonality = generate_commonality_for_date(
                uid=uid,
                anchor_date=anchor_date,
                output_dir=output_dir,
                system_prompt_path=system_prompt_path,
                health_rows=health_rows,
            )
        except ValueError:
            return None
        if commonality is None:
            return None
        return {
            "uid": uid,
            "record_date": anchor_date,
            "sleep_pattern_commonality": commonality,
        }

    results = run_llm_date_batch(
        uid=uid,
        output_path=out_path,
        resume=resume,
        dates=dates,
        process_date=_one,
        retry_delay=retry_delay,
        on_soft_fail=_on_soft_fail,
        is_complete=lambda r: isinstance(r.get("sleep_pattern_commonality"), list)
        and len(r.get("sleep_pattern_commonality")) > 0,
    )
    return results, last_skipped


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _iter_uids(output_dir: str) -> List[str]:
    return sorted(
        name.replace("_health_data.json", "")
        for name in os.listdir(output_dir)
        if name.endswith("_health_data.json")
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--output-dir", default=os.path.join(PROJECT_ROOT, "output"),
                   help="健康数据目录，默认 output/")
    p.add_argument("--out", default=os.path.join(PROJECT_ROOT, "output", "sleep_pattern_commonality.json"),
                   help="输出 JSON 文件路径")
    p.add_argument("--uid", default="", help="仅处理该用户；默认处理目录下所有用户")
    p.add_argument("--start-date", default="", help="仅处理 >= 该日期（YYYY-MM-DD）")
    p.add_argument("--end-date", default="", help="仅处理 <= 该日期（YYYY-MM-DD）")
    p.add_argument("--retry-delay", type=float, default=0.5,
                   help="每次 LLM 调用后的间隔秒数，默认 0.5")
    p.add_argument("--system-prompt", default=DEFAULT_SYSTEM_PROMPT_PATH,
                   help="系统提示词文件路径")
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
    if not os.path.isdir(output_dir):
        print(f"错误：目录不存在 {output_dir}", file=sys.stderr)
        sys.exit(1)

    uids = [args.uid.strip()] if args.uid.strip() else _iter_uids(output_dir)
    if not uids:
        print("未找到任何 *_health_data.json", file=sys.stderr)
        sys.exit(1)

    start_date = args.start_date.strip() or None
    end_date = args.end_date.strip() or None

    all_results: List[dict] = []
    for uid in uids:
        print(f"\n处理 uid={uid} …")
        all_results.extend(
            generate_for_uid(
                uid=uid,
                output_dir=output_dir,
                start_date=start_date,
                end_date=end_date,
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
