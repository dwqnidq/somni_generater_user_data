#!/usr/bin/env python3
"""调用 utils.calculate_sleep_map_score_window 计算睡眠地图得分，并将结果（含 nightly 原始 health / sleep_events）写入 JSON。"""

from __future__ import annotations

import argparse
import json
import os
import sys


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils import atomic_write_json, calculate_sleep_map_score_window  # noqa: E402


def _round_all_floats_to_int(obj):
    """递归把结果中的 float 转为 int（四舍五入）；保留 source_health / source_sleep_events 内原始数值。"""
    if isinstance(obj, float):
        return int(round(obj))
    if isinstance(obj, list):
        return [_round_all_floats_to_int(item) for item in obj]
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key in ("source_health", "source_sleep_events"):
                out[key] = value
            else:
                out[key] = _round_all_floats_to_int(value)
        return out
    return obj


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument(
        "--start-date",
        required=True,
        help="起始日期，格式 YYYY-MM-DD（默认统计后14天，含起始日）",
    )
    parser.add_argument("--uid", default="", help="用户ID；不传则统计所有用户")
    parser.add_argument("--days", type=int, default=14, help="统计天数，默认 14")
    parser.add_argument(
        "--output-dir",
        default=os.path.join(PROJECT_ROOT, "output"),
        help="读取 *_health_data.json / *_sleep_events.json 的数据目录；默认也是默认 JSON 结果写入目录之一",
    )
    parser.add_argument(
        "--out",
        default="",
        help="睡眠地图得分结果 JSON 路径；未指定则写入 "
        "<output-dir>/{uid或all_users}_sleep_map_score_{start-date}_{days}d.json",
    )
    return parser


def main():
    args = build_parser().parse_args()
    result = calculate_sleep_map_score_window(
        start_date=args.start_date,
        uid=args.uid.strip() or None,
        days=args.days,
        output_dir=args.output_dir,
    )
    result = _round_all_floats_to_int(result)

    if args.out.strip():
        out_path = os.path.abspath(args.out.strip())
    else:
        uid_label = args.uid.strip() or "all_users"
        file_name = f"{uid_label}_sleep_map_score_{args.start_date}_{args.days}d.json"
        out_path = os.path.join(os.path.abspath(args.output_dir), file_name)

    atomic_write_json(out_path, result)

    print(f"已写入: {out_path}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
