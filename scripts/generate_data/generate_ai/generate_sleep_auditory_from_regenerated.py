"""
从 output_regenerated_auditory/*_sleep_auditory.json 读取听觉数据，
调用 LLM 生成 auditory.module，并回写到原文件对应日期。
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
from generate_ai.runtime import bootstrap_llm  # noqa: E402
from sleep_report.auditory import (  # noqa: E402
    _normalize_auditory_module_list,
)

_EMPTY_AUDITORY_MODULE = [{"target": "", "description": ""}]
_APNEA_RE = re.compile(r"出现(\d+)次呼吸暂停")


def _load_env_file_fallback(env_path: str) -> None:
    """在 python-dotenv 不可用时，最小化解析 .env 并写入进程环境。"""
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                key = k.strip()
                if not key or key in os.environ:
                    continue
                val = v.strip()
                if (val.startswith('"') and val.endswith('"')) or (
                    val.startswith("'") and val.endswith("'")
                ):
                    val = val[1:-1]
                os.environ[key] = val
    except OSError:
        return


def _atomic_write_json(path: str, data: list) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _iter_uids_from_regenerated_dir(output_dir: str) -> List[str]:
    return sorted(
        name[: -len("_sleep_auditory.json")]
        for name in os.listdir(output_dir)
        if name.endswith("_sleep_auditory.json")
    )


def _load_regenerated_rows(uid: str, output_dir: str) -> List[dict]:
    path = os.path.join(output_dir, f"{uid}_sleep_auditory.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict) and r.get("record_date")]


def _extract_apnea_count_from_auditory(auditory: dict) -> int:
    if not isinstance(auditory, dict):
        return 0
    risk_alert = str(auditory.get("risk_alert") or "")
    m = _APNEA_RE.search(risk_alert)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


def _snoring_data_points_for_prompt(auditory: dict) -> List[dict]:
    """为提示词拼装 data_points，并补充同分钟 Snore 的 duration_sec。"""
    audios = auditory.get("audios") or []
    duration_by_time = {}
    for audio in audios:
        if not isinstance(audio, dict):
            continue
        if (audio.get("type") or "") != "Snore":
            continue
        t = str(audio.get("time") or "").strip()
        if not t:
            continue
        duration_by_time[t] = audio.get("duration_sec")

    out = []
    dps = ((auditory.get("snoring_analysis") or {}).get("data_points") or [])
    for p in dps:
        if not isinstance(p, dict):
            continue
        t = str(p.get("time") or "").strip()
        if not t:
            continue
        out.append(
            {
                "time": t,
                "value": p.get("value"),
                "duration_sec": duration_by_time.get(t),
            }
        )
    return out


def _should_return_empty_auditory_module(data_points: List[dict], apnea_count: Any) -> bool:
    no_snoring = not data_points
    try:
        apnea = int(apnea_count if apnea_count is not None else 0)
    except (TypeError, ValueError):
        apnea = 0
    return no_snoring and apnea < 5


def _generate_module_for_row(
    uid: str,
    row: dict,
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

    rd = str(row.get("record_date") or "").strip()
    auditory = row.get("auditory") or {}
    apnea_count = _extract_apnea_count_from_auditory(auditory)
    data_points = _snoring_data_points_for_prompt(auditory)
    payload = {
        "user_id": uid,
        "record_date": rd,
        "sleep_events": [],
        "sleep_metrics": {
            "record_date": rd,
            "apnea_count": apnea_count,
        },
        "apnea_count": apnea_count,
        "data_points": data_points,
        "environment_samples": [],
        "auditory_audios": [
            {
                "type": a.get("type"),
                "time": a.get("time"),
                "duration_sec": a.get("duration_sec"),
            }
            for a in (auditory.get("audios") or [])
            if isinstance(a, dict)
        ],
    }
    if _should_return_empty_auditory_module(data_points, apnea_count):
        return list(_EMPTY_AUDITORY_MODULE)

    prompt = (
        "以下为本晚真实输入数据（JSON）。请仅依据这些数据进行分析，"
        "返回仅包含 1 条元素的 JSON 数组（字段仅限 `target`、`description`）。"
        "仅当 data_points 为空且 apnea_count 同时小于 5 时，target 与 description 才均为空字符串；"
        "有鼾声或 apnea_count≥5 时须输出非空分析。不要附加任何解释。\n\n"
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


def refresh_uid_module_in_place(
    uid: str,
    output_dir: str,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    retry_delay: float = 0.5,
    max_records: Optional[int] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
) -> dict:
    path = os.path.join(output_dir, f"{uid}_sleep_auditory.json")
    rows = _load_regenerated_rows(uid, output_dir)
    if not rows:
        return {"uid": uid, "updated_days": 0, "path": path, "skipped": "empty_or_missing"}

    filtered = sorted(rows, key=lambda r: str(r.get("record_date") or ""))
    if start_date:
        filtered = [r for r in filtered if str(r.get("record_date") or "") >= start_date]
    if end_date:
        filtered = [r for r in filtered if str(r.get("record_date") or "") <= end_date]
    filtered = apply_max_records(filtered, max_records)
    by_date = {str(r.get("record_date") or ""): r for r in rows}

    updated = 0
    n = len(filtered)
    for i, row in enumerate(filtered):
        rd = str(row.get("record_date") or "")
        if not rd:
            continue
        print(f"  [{i + 1}/{n}] uid={uid} date={rd} …", end=" ", flush=True)
        module = _generate_module_for_row(
            uid=uid,
            row=row,
            output_dir=output_dir,
            temperature=temperature,
            top_p=top_p,
        )
        if not module:
            print("失败（已跳过）")
        else:
            target_row = by_date.get(rd)
            if not isinstance(target_row, dict):
                print("失败（记录丢失）")
            else:
                aud = target_row.get("auditory")
                if not isinstance(aud, dict):
                    aud = {}
                    target_row["auditory"] = aud
                aud["module"] = module
                updated += 1
                print("完成")
                _atomic_write_json(path, [by_date[d] for d in sorted(by_date.keys())])
        if i < n - 1 and retry_delay > 0:
            time.sleep(retry_delay)

    return {"uid": uid, "updated_days": updated, "path": path}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--output-dir",
        default=os.path.join(PROJECT_ROOT, "output_regenerated_auditory"),
        help="regenerated 听觉文件目录",
    )
    p.add_argument("--uid", default="", help="指定单个 uid；不传则处理目录内全部 uid")
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    p.add_argument("--retry-delay", type=float, default=0.5)
    p.add_argument("--max-records", type=int, default=0)
    p.add_argument("--temperature", type=float, default=None)
    p.add_argument("--top-p", type=float, default=None)
    args = p.parse_args()
    _load_env_file_fallback(os.path.join(PROJECT_ROOT, ".env"))
    bootstrap_llm()

    output_dir = os.path.abspath(args.output_dir)
    uids = [args.uid.strip()] if args.uid.strip() else _iter_uids_from_regenerated_dir(output_dir)
    results: List[dict] = []
    for uid in uids:
        print(f"\n处理 uid={uid} …")
        results.append(
            refresh_uid_module_in_place(
                uid=uid,
                output_dir=output_dir,
                start_date=args.start_date.strip() or None,
                end_date=args.end_date.strip() or None,
                retry_delay=args.retry_delay,
                max_records=int(args.max_records) or None,
                temperature=args.temperature,
                top_p=args.top_p,
            )
        )
    print("\n完成：")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
