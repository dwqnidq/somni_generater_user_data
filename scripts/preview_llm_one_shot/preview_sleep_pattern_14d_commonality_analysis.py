#!/usr/bin/env python3
"""单条预览：近14天睡眠共性分析（system = prompt/sleep_pattern_14d_commonality_analysis.md）。

会从 output 目录读取同用户的：
  {user_id}_health_data.json（睡眠）
  {user_id}_environment_data.json（环境；按 record_date 聚合为夜间摘要后入模）
  {user_id}_calendar_events.json（日程）
  {user_id}_daily_emotion_steps.json（情绪与步数）
缺失的侧车文件视为空列表，不中断运行。
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta

from dotenv import load_dotenv

from _shared import (
    PREVIEW_LLM_TEMPERATURE,
    PREVIEW_LLM_TOP_P,
    PROJECT_ROOT,
    base_arg_parser,
    bootstrap,
    default_out_path,
    force_doubao_env,
    load_health_row,
    write_result,
)

bootstrap()
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
force_doubao_env()

import generate_health_data as gh  # noqa: E402
from generate_ai.generate_sleep_pattern_commonality import (  # noqa: E402
    normalize_sleep_pattern_commonality_llm_dict,
)


def _load_health_rows(user_id: str, output_dir: str) -> list[dict]:
    path = os.path.join(PROJECT_ROOT, output_dir, f"{user_id}_health_data.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path} 无有效记录")
    return [r for r in rows if isinstance(r, dict) and r.get("record_date")]


def _load_json_list_optional(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def _rollup_environment_by_record_date(env_rows: list[dict]) -> list[dict]:
    """将 environment_data 明细按 record_date 聚合为夜间摘要，控制入模体积。"""
    by_rd: dict[str, list[dict]] = defaultdict(list)
    for r in env_rows:
        rd = str(r.get("record_date") or "")
        if rd:
            by_rd[rd].append(r)

    def _stats(values: list[float]) -> dict:
        if not values:
            return {}
        return {
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "mean": round(sum(values) / len(values), 2),
        }

    out: list[dict] = []
    for rd in sorted(by_rd.keys()):
        pts = by_rd[rd]
        uid = next((str(p.get("uid")) for p in pts if p.get("uid")), "")
        temp = [float(p["temperature"]) for p in pts if p.get("temperature") is not None]
        hum = [float(p["humidity"]) for p in pts if p.get("humidity") is not None]
        ill = [float(p["illuminance"]) for p in pts if p.get("illuminance") is not None]
        nz = [float(p["noise"]) for p in pts if p.get("noise") is not None]
        out.append(
            {
                "record_date": rd,
                "uid": uid,
                "sample_count": len(pts),
                "temperature": _stats(temp),
                "humidity": _stats(hum),
                "illuminance": _stats(ill),
                "noise": _stats(nz),
            }
        )
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
    rows: list[dict], date_key: str, start_date: str, end_date: str
) -> list[dict]:
    out = []
    for row in rows:
        d = str(row.get(date_key) or "")
        if start_date <= d <= end_date:
            out.append(row)
    out.sort(key=lambda x: str(x.get(date_key) or ""))
    return out


def _build_14d_payload(
    rows: list[dict],
    anchor_date: str,
    *,
    user_id: str,
    output_dir: str,
) -> dict:
    anchor_dt = datetime.strptime(anchor_date, "%Y-%m-%d")
    start_dt = anchor_dt - timedelta(days=13)
    start_date = start_dt.strftime("%Y-%m-%d")

    selected = []
    for row in rows:
        record_date = str(row.get("record_date"))
        if start_date <= record_date <= anchor_date:
            selected.append(row)
    selected.sort(key=lambda x: str(x.get("record_date") or ""))

    if not selected:
        raise ValueError(f"在 {start_date} ~ {anchor_date} 未找到睡眠数据")

    base = os.path.join(PROJECT_ROOT, output_dir)
    env_path = os.path.join(base, f"{user_id}_environment_data.json")
    cal_path = os.path.join(base, f"{user_id}_calendar_events.json")
    des_path = os.path.join(base, f"{user_id}_daily_emotion_steps.json")

    env_filtered = _filter_rows_by_date_key(
        _load_json_list_optional(env_path), "record_date", start_date, anchor_date
    )
    env_rollup = _rollup_environment_by_record_date(env_filtered)

    calendar_filtered = _filter_rows_by_date_key(
        _load_json_list_optional(cal_path), "event_date", start_date, anchor_date
    )
    emotion_filtered = _filter_rows_by_date_key(
        _load_json_list_optional(des_path), "event_date", start_date, anchor_date
    )

    return {
        "anchor_record_date": anchor_date,
        "analysis_period": {"start": start_date, "end": anchor_date},
        "sleep_records_14d": [_compact_sleep_record(r) for r in selected],
        # environment_data：同源 JSON 按窗口过滤后按睡眠日聚合（夜间摘要），避免原始采样条数过大
        "environment_data": env_rollup,
        "calendar_events": calendar_filtered,
        "daily_emotion_steps": emotion_filtered,
    }


def main():
    ap = base_arg_parser(__doc__ or "")
    ap.add_argument(
        "--system-prompt",
        default=os.path.join(PROJECT_ROOT, "prompt", "sleep_pattern_14d_commonality_analysis.md"),
        help="系统提示词文件路径",
    )
    args = ap.parse_args()
    gh.set_model_switch(True)

    _, rd = load_health_row(args.user_id, args.record_date, args.output_dir)
    rows = _load_health_rows(args.user_id, args.output_dir)
    payload = _build_14d_payload(
        rows,
        rd,
        user_id=args.user_id,
        output_dir=args.output_dir,
    )

    print("=== 传递给模型的数据 ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("======================")

    with open(args.system_prompt, "r", encoding="utf-8") as f:
        instruction = f.read().strip()
    if not instruction:
        raise SystemExit("系统提示词为空，请检查 --system-prompt")

    prompt = (
        "以下为真实输入数据（JSON），请严格按系统提示词仅输出 JSON 对象，"
        "且必须使用 items 数组承载一条或多条共性（每项含 highlight、analysis、type、list）；"
        "list 与 sleep_records_14d 同序、等长。仅一条时 items 长度为 1。不要 markdown 围栏和解释。\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    raw_out = gh.call_qwen_api(
        prompt,
        system_prompt=instruction,
        max_tokens=1536,
        temperature=PREVIEW_LLM_TEMPERATURE,
        top_p=PREVIEW_LLM_TOP_P,
        sleep_report_llm=True,
    )
    if not raw_out.strip():
        raise SystemExit("模型无返回（请检查密钥、模型配置与网络）")

    try:
        parsed = gh._parse_json_from_response(raw_out)
    except Exception as e:
        raise SystemExit(f"解析 JSON 失败: {e}\n原始输出:\n{raw_out[:800]}") from e
    if not isinstance(parsed, dict):
        raise SystemExit("模型输出不是 JSON 对象")

    out = args.out.strip() or default_out_path("preview_sleep_pattern_14d_commonality_analysis_one")
    blocks = normalize_sleep_pattern_commonality_llm_dict(parsed)
    write_result(
        out,
        {
            "record_date": rd,
            "sleep_pattern_commonality": blocks,
        },
    )


if __name__ == "__main__":
    main()
