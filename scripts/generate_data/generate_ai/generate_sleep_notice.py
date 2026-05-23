"""
批量生成睡眠 notice。

读取 output/{uid}_health_data.json 与 {uid}_sleep_events.json，
对每条记录调用 gh.generate_notice_via_qwen(...) 生成 report.notice。

作为独立脚本运行：
  python scripts/generate_data/generate_ai/generate_sleep_notice.py --uid <uid>

作为模块导入：
  from generate_sleep_notice import generate_notice_for_uid
  results = generate_notice_for_uid(uid, output_dir)
  # [{"uid":..., "record_date":..., "notice": {"title":..., "content":...}}, ...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
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

from sleep_report.auditory import (  # noqa: E402
    _compact_sleep_events_for_auditory_prompt,
    _environment_samples_for_auditory_prompt,
    _sleep_metrics_for_auditory_prompt,
)
from sleep_report.environment_summary import generate_environment_summary  # noqa: E402
from sleep_report.notice import build_notice_yesterday_sleep_block  # noqa: E402
from sleep_report.sleep_helpers import build_sleep_events_index  # noqa: E402


def _load_personality_type(uid: str) -> str:
    config_path = os.path.join(PROJECT_ROOT, "config", "health_data_personas_config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return "M-L-C"

    for persona in cfg.get("personas") or []:
        if not isinstance(persona, dict) or str(persona.get("user_id") or "") != uid:
            continue
        code = str(persona.get("code") or "").strip()
        if code:
            return code
        personal_info = persona.get("personalInformation") or {}
        p_type = str(personal_info.get("type") or "").strip()
        if p_type:
            return p_type
    return "M-L-C"


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def generate_notice_for_date(
    uid: str,
    sleep_data: dict,
    sleep_events_index: dict,
    output_dir: str,
    personality_type: str,
    health_rows_by_date: Optional[Dict[str, dict]] = None,
) -> Optional[dict]:
    """为单条 health 数据生成 notice。"""
    from generate_ai import llm_client

    rd = str(sleep_data.get("record_date") or "")
    if not rd:
        return None

    prev_sleep_data = None
    if health_rows_by_date:
        try:
            prev_date = (datetime.strptime(rd, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            prev_sleep_data = health_rows_by_date.get(prev_date)
        except ValueError:
            pass
    if not prev_sleep_data:
        return None

    if not llm_client.sleep_report_llm_enabled:
        return None

    environment_summary = generate_environment_summary(uid, rd, output_dir=output_dir)
    sleep_metrics = _sleep_metrics_for_auditory_prompt(sleep_data)
    yesterday_block = build_notice_yesterday_sleep_block(uid, rd, output_dir=output_dir)
    env_samples = _environment_samples_for_auditory_prompt(uid, rd, max_n=80, output_dir=output_dir)
    auditory_events = _compact_sleep_events_for_auditory_prompt(
        sleep_events_index.get(rd, []), max_n=80
    )
    system = llm_client.render_prompt_template(
        "generate_health_data__notice.md",
        {
            "RECORD_DATE": rd,
            "PERSONALITY_TYPE": personality_type,
            "YESTERDAY_SLEEP_BLOCK": yesterday_block,
            "SLEEP_METRICS_JSON": json.dumps(sleep_metrics, ensure_ascii=False),
            "ENVIRONMENT_SUMMARY_JSON": json.dumps(environment_summary or {}, ensure_ascii=False),
            "ENVIRONMENT_SAMPLES_JSON": json.dumps(env_samples, ensure_ascii=False),
            "AUDITORY_EVENTS_JSON": json.dumps(auditory_events, ensure_ascii=False),
        },
    )
    if prev_sleep_data:
        prev_rd = str(prev_sleep_data.get("record_date") or "")
        system += f"\n\n前一日（{prev_rd}）睡眠数据：\n{json.dumps(prev_sleep_data, ensure_ascii=False)}"

    raw = llm_client.call_qwen_api(
        "请严格按系统说明仅输出一个 JSON 对象，不要 markdown 围栏或解释。",
        system_prompt=system,
        max_tokens=512,
        temperature=0.35,
        sleep_report_llm=True,
    )
    if not raw.strip():
        return None
    try:
        notice = llm_client.parse_json_from_response(raw)
    except Exception as e:
        print(f"  [警告] 解析 notice 返回内容失败: {e}")
        return None
    if not isinstance(notice, dict):
        return None
    content = str(notice.get("content") or "").strip()
    if not content:
        return None
    notice["title"] = "Bio-OS 算法已进化"
    notice["content"] = content
    return notice


def generate_notice_for_uid(
    uid: str,
    output_dir: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    personality_type: Optional[str] = None,
    retry_delay: float = 0.5,
    max_records: Optional[int] = None,
    resume: bool = False,
) -> List[dict]:
    """为单个用户批量生成每日 notice。

    Returns:
        [{"uid":..., "record_date":..., "notice": {"title":..., "content":...}}, ...]
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
    p_type = (personality_type or "").strip() or _load_personality_type(uid)

    rows = sorted(health_rows, key=lambda r: str(r.get("record_date") or ""))
    if start_date:
        rows = [r for r in rows if str(r.get("record_date") or "") >= start_date]
    if end_date:
        rows = [r for r in rows if str(r.get("record_date") or "") <= end_date]

    rows = apply_max_records(rows, max_records)

    # 构建日期索引，用于快速查找前一日数据
    health_rows_by_date: Dict[str, dict] = {
        str(r.get("record_date")): r for r in health_rows if r.get("record_date")
    }

    by_rd = {str(r.get("record_date") or ""): r for r in rows if r.get("record_date")}
    out_path = os.path.join(output_dir, f"{uid}_sleep_notice.json")

    def _one(rd: str) -> Optional[dict]:
        notice = generate_notice_for_date(
            uid=uid,
            sleep_data=by_rd[rd],
            sleep_events_index=sleep_events_index,
            output_dir=output_dir,
            personality_type=p_type,
            health_rows_by_date=health_rows_by_date,
        )
        if notice is None:
            return None
        return {"uid": uid, "record_date": rd, "notice": notice}

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
    p.add_argument("--out", default=os.path.join(PROJECT_ROOT, "output", "sleep_notice.json"))
    p.add_argument("--uid", default="")
    p.add_argument("--start-date", default="")
    p.add_argument("--end-date", default="")
    p.add_argument("--personality-type", default="")
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
            generate_notice_for_uid(
                uid=uid,
                output_dir=output_dir,
                start_date=args.start_date.strip() or None,
                end_date=args.end_date.strip() or None,
                personality_type=args.personality_type.strip() or None,
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
