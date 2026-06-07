#!/usr/bin/env python3
"""仅为展会 processed 目录生成 ai_analysis_14d（单天或多天），不修改源备份与其它文件。

- 只写 backup/somni_all_data_展会版本_processed/（禁止写 展会版本/）
- 缺 14 天窗口 health 时：用 main.py 同款 generate_persona_health_data **追加**缺失日，不覆盖已有 health
- 只覆盖/写入 {uid}_ai_analysis_14d.json 中对应日期的 AI 字段（title / sleep_insight / schedule_insight）
- 不跑 morning_alarm、fusion_insight、hidden_discovery、translate

提示词：prompt/sleep_trend_14d_analysis.md

用法（项目根目录）:
  # 仅 6 月 6 日，单人
  python scripts/backup/generate_exhibition_ai_analysis.py \\
      --uid 69aea6d8af5e6cbf08027966 --date 2026-06-06

  # 日期区间
  python scripts/backup/generate_exhibition_ai_analysis.py \\
      --uid 69aea6d8af5e6cbf08027966 --start-date 2026-06-01 --end-date 2026-06-30

  # 八人格 6/6
  python scripts/backup/generate_exhibition_ai_analysis.py --date 2026-06-06 --all-personas

  # 预览
  python scripts/backup/generate_exhibition_ai_analysis.py --date 2026-06-06 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.backup.exhibition.fill_gaps import generate_ai_trend_only_for_uid  # noqa: E402
from scripts.backup.exhibition.shared import (  # noqa: E402
    DEFAULT_OUTPUT_REL,
    assert_writable_work_dir,
    list_persona_uids,
    resolve_path,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_REL, help="工作目录（默认 processed）")
    parser.add_argument("--uid", default="", help="单人 uid")
    parser.add_argument("--all-personas", action="store_true", help="八人格全部")
    parser.add_argument("--date", default="", help="单日 YYYY-MM-DD（与 start/end 二选一）")
    parser.add_argument("--start-date", default="", help="起始日 YYYY-MM-DD")
    parser.add_argument("--end-date", default="", help="结束日 YYYY-MM-DD")
    parser.add_argument("--weather-json", default="", help="天气 JSON，默认 output/qweather_monthly_data.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.date:
        start_date = end_date = args.date.strip()
    else:
        start_date = args.start_date.strip() or "2026-06-06"
        end_date = args.end_date.strip() or start_date

    output_dir = resolve_path(args.output_dir, DEFAULT_OUTPUT_REL)
    assert_writable_work_dir(output_dir)
    if not os.path.isdir(output_dir):
        raise FileNotFoundError(f"工作目录不存在，请先 copy: {output_dir}")

    if args.all_personas:
        uids = list_persona_uids(output_dir, None)
    elif args.uid.strip():
        uids = [args.uid.strip()]
    else:
        parser.error("请指定 --uid 或 --all-personas")

    weather_json = args.weather_json.strip() or None
    results: list[dict] = []
    for uid in uids:
        print(f"\n=== ai_analysis_14d uid={uid} {start_date}～{end_date} ===")
        results.append(
            generate_ai_trend_only_for_uid(
                uid,
                output_dir,
                start_date=start_date,
                end_date=end_date,
                weather_json=weather_json,
                dry_run=args.dry_run,
            )
        )

    print(json.dumps({"output_dir": output_dir, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
