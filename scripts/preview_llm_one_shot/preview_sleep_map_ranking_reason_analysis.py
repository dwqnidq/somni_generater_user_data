#!/usr/bin/env python3
"""单条预览：睡眠地图排名原因（system = prompt/sleep_map_ranking_reason_analysis.md）。

从 output/{user_id}_somni_sleep_analysis.json 读取指定 stats_date 的五维得分与城市均分，
组装 user JSON 后调用模型，返回 {"ranking_reason": "..."}。

也可用 --input-json 直接传入与 prompt 一致的 user 载荷（跳过 somni 文件）。
"""

from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv

from _shared import (
    PREVIEW_LLM_TEMPERATURE,
    PREVIEW_LLM_TOP_P,
    PROJECT_ROOT,
    bootstrap,
    default_out_path,
    force_doubao_env,
    write_result,
)

bootstrap()
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
force_doubao_env()

from generate_ai.runtime import bootstrap_llm  # noqa: E402
from generate_sleep_map_ranking_reason import (  # noqa: E402
    build_ranking_reason_user_payload,
    generate_ranking_reason_from_user_payload,
    load_somni_sleep_analysis_row,
)


def _arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__ or "")
    p.add_argument("--user-id", default="", help="与 output 下文件名前缀一致")
    p.add_argument(
        "--stats-date",
        "--record-date",
        default="",
        dest="stats_date",
        help="YYYY-MM-DD；对应 somni_sleep_analysis.stats_date；省略则取该用户第一条",
    )
    p.add_argument(
        "--output-dir",
        default="output",
        help="相对工程根目录，默认 output",
    )
    p.add_argument(
        "--input-json",
        default="",
        help="直接指定 user 载荷 JSON 文件路径（五维分 + city_avg_score）",
    )
    p.add_argument(
        "--out",
        default="",
        help="写入路径；省略则 preview_llm_output/preview_sleep_map_ranking_reason_analysis_one.json",
    )
    return p


def _load_first_analysis_row(uid: str, output_dir: str) -> tuple[dict, str]:
    path = os.path.join(output_dir, f"{uid}_somni_sleep_analysis.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        raise ValueError(f"{path} 应为 JSON 数组")
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("uid") or "").strip() != uid:
            continue
        sd = str(row.get("stats_date") or "").strip()
        if sd:
            return row, sd
    raise ValueError(f"{path} 中无 uid={uid} 的有效记录")


def main() -> None:
    bootstrap_llm()
    args = _arg_parser().parse_args()
    output_dir = os.path.join(PROJECT_ROOT, args.output_dir)
    uid = ""
    stats_date = ""

    if args.input_json.strip():
        with open(os.path.abspath(args.input_json.strip()), "r", encoding="utf-8") as f:
            user_payload = json.load(f)
        if not isinstance(user_payload, dict):
            raise SystemExit("--input-json 须为 JSON 对象")
        uid = str(args.user_id or user_payload.get("uid") or "")
        stats_date = str(user_payload.get("stats_date") or "")
    else:
        uid = str(args.user_id or "").strip()
        if not uid:
            raise SystemExit("请提供 --user-id，或使用 --input-json")
        if args.stats_date.strip():
            analysis_row = load_somni_sleep_analysis_row(
                uid, args.stats_date.strip(), output_dir
            )
            stats_date = args.stats_date.strip()
        else:
            analysis_row, stats_date = _load_first_analysis_row(uid, output_dir)
        user_payload = build_ranking_reason_user_payload(analysis_row)

    result = generate_ranking_reason_from_user_payload(
        user_payload,
        temperature=PREVIEW_LLM_TEMPERATURE,
        top_p=PREVIEW_LLM_TOP_P,
    )
    if not result:
        raise SystemExit("生成失败（检查模板、密钥、somni 数据与模型返回 JSON）")

    out = args.out.strip() or default_out_path(
        "preview_sleep_map_ranking_reason_analysis_one"
    )
    write_result(
        out,
        {
            "uid": uid,
            "stats_date": stats_date,
            "user_payload": user_payload,
            **result,
        },
    )


if __name__ == "__main__":
    main()
