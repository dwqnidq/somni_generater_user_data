"""为睡眠热力图模拟批量生成虚拟用户的 N 天数据 + 睡眠地图分数。

每个虚拟用户的输出文件（与现有 8 个真实用户共用 output/ 目录，按 user_id 区分）：
  - {uid}_health_data.json
  - {uid}_environment_data.json
  - {uid}_vitals_data.json
  - {uid}_sleep_events.json
  - {uid}_sleep_map_score.json

最后写出汇总索引：output/heatmap_users_index.json

用法：
  python scripts/generate_data/generate_heatmap_users.py
  python scripts/generate_data/generate_heatmap_users.py --count 200
  python scripts/generate_data/generate_heatmap_users.py --count 50 --start-date 2026-04-01 --days 14 --seed 42
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
import secrets
import sys
import traceback
from datetime import date, datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_ROOT)

from generate_health_data_by_persona_config import generate_persona_health_data  # noqa: E402
from generate_environment_data_by_persona import generate_environment_for_persona  # noqa: E402
from generate_vitals_data_by_persona import generate_vitals_for_persona  # noqa: E402
from generate_sleep_events_by_persona import generate_sleep_events_for_persona  # noqa: E402
from utils import atomic_write_json, calculate_sleep_map_score_window  # noqa: E402

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "health_data_personas_config.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
INDEX_FILE = os.path.join(OUTPUT_DIR, "heatmap_users_index.json")

DEFAULT_COUNT = 100
DEFAULT_DAYS = 14
DEFAULT_START_DATE = "2026-03-01"
GOOD_RATIO_RANGE = (0.2, 0.8)


def _new_user_id(taken: set[str]) -> str:
    while True:
        uid = secrets.token_hex(12)
        if uid not in taken:
            taken.add(uid)
            return uid


def _virtual_persona_from_template(
    template: dict, uid: str, start_date: date, days: int
) -> dict:
    persona = copy.deepcopy(template)
    persona["user_id"] = uid
    end_date = start_date + timedelta(days=days - 1)
    persona["date_range"] = {
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
    }
    persona["name"] = f"{template.get('name', '')}#{uid[:6]}"
    return persona


def _round_floats_to_int(obj):
    if isinstance(obj, float):
        return int(round(obj))
    if isinstance(obj, list):
        return [_round_floats_to_int(x) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("source_health", "source_sleep_events"):
                out[k] = v
            else:
                out[k] = _round_floats_to_int(v)
        return out
    return obj


def generate_one_virtual_user(
    template: dict,
    cfg: dict,
    *,
    start_date: date,
    days: int,
    good_ratio: float,
    taken_uids: set[str],
) -> dict | None:
    """串联 health → env → vitals → events → score；返回索引项 dict，失败返回 None。"""
    uid = _new_user_id(taken_uids)
    persona = _virtual_persona_from_template(template, uid, start_date, days)

    try:
        if not generate_persona_health_data(
            persona=persona,
            state_mode="mixed",
            good_ratio=good_ratio,
            overwrite=True,
        ):
            print(f"[{uid}] health 生成失败")
            return None
        if not generate_environment_for_persona(persona, cfg, overwrite=True):
            print(f"[{uid}] environment 生成失败")
            return None
        if not generate_vitals_for_persona(persona, cfg, overwrite=True):
            print(f"[{uid}] vitals 生成失败")
            return None
        if not generate_sleep_events_for_persona(persona, cfg, overwrite=True):
            print(f"[{uid}] sleep_events 生成失败")
            return None
    except Exception:
        print(f"[{uid}] 生成异常：")
        traceback.print_exc()
        return None

    score_result = calculate_sleep_map_score_window(
        start_date=start_date.isoformat(),
        uid=uid,
        days=days,
        output_dir=OUTPUT_DIR,
    )
    score_result = _round_floats_to_int(score_result)
    score_path = os.path.join(OUTPUT_DIR, f"{uid}_sleep_map_score.json")
    atomic_write_json(score_path, score_result)

    composite = (score_result.get("scores") or {}).get("composite_score")
    return {
        "uid": uid,
        "persona_code": template.get("code"),
        "template_name": template.get("name"),
        "good_ratio": round(good_ratio, 2),
        "composite_score": composite,
        "files": {
            "health_data": f"{uid}_health_data.json",
            "environment_data": f"{uid}_environment_data.json",
            "vitals_data": f"{uid}_vitals_data.json",
            "sleep_events": f"{uid}_sleep_events.json",
            "sleep_map_score": f"{uid}_sleep_map_score.json",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument(
        "--count", type=int, default=DEFAULT_COUNT,
        help=f"虚拟用户数量（默认 {DEFAULT_COUNT}）",
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS,
        help=f"每个用户的天数（默认 {DEFAULT_DAYS}）",
    )
    parser.add_argument(
        "--start-date", default=DEFAULT_START_DATE,
        help=f"统一起始日期 YYYY-MM-DD（默认 {DEFAULT_START_DATE}）",
    )
    parser.add_argument("--config", default=CONFIG_PATH, help="人格配置文件路径")
    parser.add_argument(
        "--seed", type=int, default=None,
        help="随机种子（同种子 + 同参数可复现，但 user_id 仍随机）",
    )
    args = parser.parse_args()

    if args.count <= 0:
        print("--count 必须为正整数")
        sys.exit(1)
    if args.days <= 0:
        print("--days 必须为正整数")
        sys.exit(1)

    if args.seed is not None:
        random.seed(args.seed)

    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    templates = cfg.get("personas") or []
    if not templates:
        print("配置中未找到任何人格模板")
        sys.exit(1)

    taken_uids: set[str] = {p.get("user_id", "") for p in templates if p.get("user_id")}

    n_templates = len(templates)
    counts_per_template = [args.count // n_templates] * n_templates
    for i in range(args.count % n_templates):
        counts_per_template[i] += 1

    summary: list[dict] = []
    total_target = args.count
    for tpl_idx, n in enumerate(counts_per_template):
        template = templates[tpl_idx]
        for _ in range(n):
            good_ratio = round(random.uniform(*GOOD_RATIO_RANGE), 2)
            seq = len(summary) + 1
            print(
                f"\n--- [{seq}/{total_target}] template={template.get('code')} "
                f"good_ratio={good_ratio} ---"
            )
            entry = generate_one_virtual_user(
                template, cfg,
                start_date=start_date,
                days=args.days,
                good_ratio=good_ratio,
                taken_uids=taken_uids,
            )
            if entry:
                summary.append(entry)

    index_payload = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "params": {
            "count": args.count,
            "days": args.days,
            "start_date": args.start_date,
            "seed": args.seed,
        },
        "user_count": len(summary),
        "users": summary,
    }
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    atomic_write_json(INDEX_FILE, index_payload)

    print(
        f"\n完成：成功 {len(summary)}/{total_target} 个虚拟用户；"
        f"汇总索引 → {INDEX_FILE}"
    )


if __name__ == "__main__":
    main()
