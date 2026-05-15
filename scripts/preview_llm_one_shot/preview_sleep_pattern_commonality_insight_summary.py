#!/usr/bin/env python3
"""单条预览：睡眠共性洞察汇总（system = prompt/sleep_pattern_commonality_insight_summary.md）。

读取已生成的 sleep_pattern_commonality 数据（**JSON 数组**，每项含 highlight、analysis，及可选的 list 指标序列），
可仅 1 项或多项；历史文件若为单对象会自动包成单元素数组），综合归并后输出：
  { target, description, tips }

输入来源（优先级从高到低）：
  1. --input-file 指定的 JSON 文件（含 sleep_pattern_commonality 字段）
  2. preview_llm_output/preview_sleep_pattern_14d_commonality_analysis_one.json（默认）
"""

from __future__ import annotations

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

import generate_health_data as gh  # noqa: E402

DEFAULT_INPUT = os.path.join(
    PROJECT_ROOT,
    "preview_llm_output",
    "preview_sleep_pattern_14d_commonality_analysis_one.json",
)


def _load_commonality(input_file: str) -> tuple[list, str]:
    """从指定 JSON 文件读取 sleep_pattern_commonality，返回 (数组, record_date)。"""
    if not os.path.isfile(input_file):
        raise FileNotFoundError(f"输入文件不存在：{input_file}")
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("输入文件顶层应为 JSON 对象")
    raw = data.get("sleep_pattern_commonality")
    if raw is None:
        raise ValueError("输入文件中缺少 sleep_pattern_commonality 字段")
    if isinstance(raw, dict):
        commonality = [raw]
    elif isinstance(raw, list):
        commonality = raw
    else:
        raise ValueError(
            "sleep_pattern_commonality 须为 JSON 数组（每项含 highlight、analysis，建议含 list），"
            "或为兼容历史的单对象"
        )
    if not commonality:
        raise ValueError("sleep_pattern_commonality 数组不能为空，至少须有一条共性")
    record_date = str(data.get("record_date") or "")
    return commonality, record_date


def main():
    import argparse

    ap = argparse.ArgumentParser(description=__doc__ or "")
    ap.add_argument(
        "--input-file",
        default="",
        help=(
            "含 sleep_pattern_commonality 字段的 JSON 文件路径；"
            f"省略则读取默认位置 {DEFAULT_INPUT}"
        ),
    )
    ap.add_argument(
        "--system-prompt",
        default=os.path.join(PROJECT_ROOT, "prompt", "sleep_pattern_commonality_insight_summary.md"),
        help="系统提示词文件路径",
    )
    ap.add_argument(
        "--out",
        default="",
        help="写入的 JSON 路径；省略则写入 ./preview_llm_output/<默认名>",
    )
    args = ap.parse_args()
    gh.set_model_switch(True)

    input_file = args.input_file.strip() or DEFAULT_INPUT
    commonality, record_date = _load_commonality(input_file)

    print("=== sleep_pattern_commonality 输入内容 ===")
    print(json.dumps(commonality, ensure_ascii=False, indent=2))
    print("==========================================")

    with open(args.system_prompt, "r", encoding="utf-8") as f:
        instruction = f.read().strip()
    if not instruction:
        raise SystemExit("系统提示词为空，请检查 --system-prompt")

    prompt = (
        "以下为真实输入数据（sleep_pattern_commonality 字段，**JSON 数组**，"
        "每项为 highlight 与 analysis；可能仅含 1 项或多项）。"
        "请严格按系统提示词仅输出 JSON 对象，"
        "字段固定为 target、description、tips，不要 markdown 围栏和解释。\n\n"
        + json.dumps(commonality, ensure_ascii=False)
    )

    raw_out = gh.call_qwen_api(
        prompt,
        system_prompt=instruction,
        max_tokens=512,
        temperature=PREVIEW_LLM_TEMPERATURE,
        top_p=PREVIEW_LLM_TOP_P,
        sleep_report_llm=True,
    )
    if not raw_out.strip():
        raise SystemExit("模型无返回（请检查密钥、模型配置与网络）")

    print("=== 模型原始输出 ===")
    print(raw_out)
    print("====================")

    try:
        parsed = gh._parse_json_from_response(raw_out)
    except Exception as e:
        raise SystemExit(f"解析 JSON 失败: {e}\n原始输出:\n{raw_out[:800]}") from e
    if not isinstance(parsed, dict):
        raise SystemExit("模型输出不是 JSON 对象")

    out = args.out.strip() or default_out_path("preview_sleep_pattern_commonality_insight_summary_one")
    saved = write_result(
        out,
        {
            "record_date": record_date,
            "sleep_pattern_commonality_insight": {
                "target": parsed.get("target", ""),
                "description": parsed.get("description", ""),
                "tips": parsed.get("tips", ""),
            },
        },
    )
    print(f"\n结果已写入：{saved}")


if __name__ == "__main__":
    main()
