#!/usr/bin/env python3
"""一键按人格生成四类数据（顺序固定）。

生成顺序：
  1. 睡眠健康数据（health_data）— scripts/generate_data/generate_health_data_by_persona_config.py
  2. 环境数据（environment_data）
  3. 体征数据（vitals_data）
  4. 睡眠事件（sleep_events）

依赖 config/health_data_personas_config.json；输出在 output/ 目录。

用法：
  python main.py
  python main.py --user-id 69aea6d8af5e6cbf08027966 --overwrite
  python main.py --state good --start-date 2026-03-01 --end-date 2026-03-15
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_GEN = os.path.join(ROOT, "scripts", "generate_data")
for _p in (ROOT, _SCRIPTS_GEN):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.chdir(ROOT)

from generate_environment_data_by_persona import generate_environment_for_persona  # noqa: E402
from generate_health_data_by_persona_config import generate_persona_health_data  # noqa: E402
from apply_event_impacts_by_persona import apply_event_impacts_for_persona  # noqa: E402
from generate_sleep_events_by_persona import generate_sleep_events_for_persona  # noqa: E402
from generate_vitals_data_by_persona import generate_vitals_for_persona  # noqa: E402

DEFAULT_CONFIG = os.path.join(ROOT, "config", "health_data_personas_config.json")


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


def run_for_persona(
    persona: dict,
    cfg: dict,
    *,
    state_mode: str,
    good_ratio: float,
    start_date: date | None,
    end_date: date | None,
    overwrite: bool,
) -> None:
    name = persona.get("name", persona.get("user_id", ""))
    uid = persona.get("user_id", "")
    print(f"\n========== {name} ({uid}) ==========")

    print("[1/5] 睡眠健康数据 (health_data) …")
    health_path = generate_persona_health_data(
        persona,
        state_mode=state_mode,
        good_ratio=good_ratio,
        start_date_override=start_date,
        end_date_override=end_date,
        overwrite=overwrite,
    )
    if not health_path:
        print("  跳过后续步骤（健康数据未生成）。")
        return

    print("[2/5] 环境数据 (environment_data) …")
    generate_environment_for_persona(
        persona, cfg, start_date=start_date, end_date=end_date, overwrite=overwrite
    )

    print("[3/5] 体征数据 (vitals_data) …")
    generate_vitals_for_persona(
        persona, cfg, start_date=start_date, end_date=end_date, overwrite=overwrite
    )

    print("[4/5] 睡眠事件 (sleep_events) …")
    generate_sleep_events_for_persona(
        persona, cfg, start_date=start_date, end_date=end_date, overwrite=overwrite
    )

    print("[5/5] 事件影响回写 (events -> environment/vitals) …")
    apply_event_impacts_for_persona(
        persona,
        start_date.isoformat() if start_date else None,
        end_date.isoformat() if end_date else None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="按人格配置依次生成 health → environment → vitals → sleep_events → 事件回写",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="health_data_personas_config.json 路径",
    )
    parser.add_argument("--user-id", default=None, help="仅处理该 user_id，不填则处理全部人格")
    parser.add_argument(
        "--state",
        choices=["good", "bad", "mixed"],
        default="mixed",
        help="睡眠健康数据日状态模式（默认 mixed）",
    )
    parser.add_argument(
        "--good-ratio",
        type=float,
        default=0.5,
        help="mixed 模式下好夜占比 0.0～1.0（默认 0.5）",
    )
    parser.add_argument("--start-date", default=None, help="覆盖起始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="覆盖结束日期 YYYY-MM-DD")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已存在的 health/environment/vitals/sleep_events 输出",
    )
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    personas = cfg.get("personas") or []
    if args.user_id:
        personas = [p for p in personas if p.get("user_id") == args.user_id]
        if not personas:
            print(f"未找到 user_id={args.user_id}")
            sys.exit(1)

    start_date = _parse_date(args.start_date)
    end_date = _parse_date(args.end_date)

    print(
        "生成顺序：health_data → environment_data → vitals_data → sleep_events → events_impact_apply\n"
        f"配置：{args.config}  人格数：{len(personas)}  overwrite={args.overwrite}"
    )

    for persona in personas:
        run_for_persona(
            persona,
            cfg,
            state_mode=args.state,
            good_ratio=args.good_ratio,
            start_date=start_date,
            end_date=end_date,
            overwrite=args.overwrite,
        )

    print("\n全部完成。")


if __name__ == "__main__":
    main()
