#!/usr/bin/env python3
"""按指定日期范围一键生成睡眠地图大表与区级聚合表。

流程（与 README 一致，合并为一条命令）：
  1. generate_sleep_map_multi_user.py  → 中间文件（默认 beijing_sleep_map_multi_user.json）
  2. generate_sleep_map_aggregated.py → somni_sleep_analysis.json + somni_sleep_district.json

用法：
  python scripts/generate_data/generate_sleep_map_pool.py \\
      --start-date 2026-04-01 --end-date 2026-05-31

  python scripts/generate_data/generate_sleep_map_pool.py \\
      --start 2026-01-01 --end 2026-03-31 --users-per-district 13 --seed 42

  # 已有中间 JSON，仅重新聚合（须与 --start/--end 日期范围一致）：
  python scripts/generate_data/generate_sleep_map_pool.py \\
      --aggregated-only --start-date 2026-04-01 --end-date 2026-05-31
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
MULTI_USER_SCRIPT = os.path.join(SCRIPT_DIR, "generate_sleep_map_multi_user.py")
AGGREGATED_SCRIPT = os.path.join(SCRIPT_DIR, "generate_sleep_map_aggregated.py")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="按日期范围生成 output/somni_sleep_analysis.json 与 somni_sleep_district.json",
    )
    p.add_argument(
        "--start-date",
        "--start",
        dest="start_date",
        default="2026-01-01",
        metavar="YYYY-MM-DD",
        help="起始 stats_date（含，默认 2026-01-01）",
    )
    p.add_argument(
        "--end-date",
        "--end",
        dest="end_date",
        default="2026-03-31",
        metavar="YYYY-MM-DD",
        help="结束 stats_date（含，默认 2026-03-31）",
    )
    p.add_argument(
        "--users-per-district",
        type=int,
        default=13,
        help="每区虚拟用户数（默认 13，传给 multi_user）",
    )
    p.add_argument("--seed", type=int, default=42, help="随机种子（默认 42）")
    p.add_argument(
        "--intermediate",
        default="output/beijing_sleep_map_multi_user.json",
        help="中间多用户 JSON（相对项目根目录）",
    )
    p.add_argument(
        "--out-analysis",
        default="output/somni_sleep_analysis.json",
        help="个人×日输出路径（默认 output/somni_sleep_analysis.json）",
    )
    p.add_argument(
        "--out-district",
        default="output/somni_sleep_district.json",
        help="区×日输出路径（默认 output/somni_sleep_district.json）",
    )
    p.add_argument(
        "--aggregated-only",
        action="store_true",
        help="跳过 multi_user，仅从已有中间文件聚合（建议同时指定与中间文件一致的日期范围）",
    )
    return p.parse_args()


def _run_step(label: str, cmd: list[str]) -> None:
    print("\n" + "=" * 64)
    print(label)
    print("  $ " + " ".join(cmd))
    print("=" * 64)
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def main() -> None:
    args = _parse_args()
    if args.end_date < args.start_date:
        print("错误：--end-date 早于 --start-date", file=sys.stderr)
        sys.exit(1)

    py = sys.executable
    intermediate = args.intermediate

    if not args.aggregated_only:
        _run_step(
            "步骤 1/2：多用户逐日数据（中间文件）",
            [
                py,
                MULTI_USER_SCRIPT,
                "--start",
                args.start_date,
                "--end",
                args.end_date,
                "--users-per-district",
                str(args.users_per_district),
                "--seed",
                str(args.seed),
                "--output",
                intermediate,
            ],
        )
    else:
        mid_path = os.path.join(PROJECT_ROOT, intermediate)
        if not os.path.isfile(mid_path):
            print(f"错误：--aggregated-only 但中间文件不存在: {mid_path}", file=sys.stderr)
            sys.exit(1)
        print(f"跳过 multi_user，使用已有中间文件: {mid_path}")

    agg_cmd = [
        py,
        AGGREGATED_SCRIPT,
        "--input",
        intermediate,
        "--out-analysis",
        args.out_analysis,
        "--out-district",
        args.out_district,
        "--seed",
        str(args.seed),
        "--start",
        args.start_date,
        "--end",
        args.end_date,
    ]
    _run_step("步骤 2/2：聚合 somni_sleep_analysis + somni_sleep_district", agg_cmd)

    print("\n完成。日期范围: {} ~ {}".format(args.start_date, args.end_date))
    print("  → {}".format(os.path.join(PROJECT_ROOT, args.out_analysis)))
    print("  → {}".format(os.path.join(PROJECT_ROOT, args.out_district)))


if __name__ == "__main__":
    main()
