#!/usr/bin/env python3
"""单条预览：notice（system = 完整 prompt + 昨夜完整睡眠数据）。"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv

from _shared import (
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


def main():
    ap = base_arg_parser(__doc__ or "")
    args = ap.parse_args()
    gh.set_model_switch(True)
    sleep_data, rd = load_health_row(args.user_id, args.record_date, args.output_dir)

    prompt = gh.render_prompt_template("generate_health_data__notice.md")
    sleep_data_json = json.dumps(sleep_data, ensure_ascii=False)
    system = f"{prompt}\n\n昨夜完整睡眠数据：\n{sleep_data_json}"
    user_msg = "请严格按系统说明仅输出一个 JSON 对象，不要 markdown 围栏或解释。"
    raw = gh.call_qwen_api(
        user_msg,
        system_prompt=system,
        max_tokens=512,
        temperature=0.35,
        sleep_report_llm=True,
    )
    if not raw.strip():
        raise SystemExit(
            "模型无返回（检查 DASHSCOPE_API_KEY、TRANSLATE_BASE_URL、TRANSLATE_MODEL_NAME）"
        )
    try:
        parsed = gh._parse_json_from_response(raw)
    except Exception as e:
        raise SystemExit(f"解析 JSON 失败: {e}\n原始:\n{raw[:800]}") from e
    out = args.out.strip() or default_out_path("preview_sleep_notice_one")
    write_result(out, {"record_date": rd, "notice": parsed})


if __name__ == "__main__":
    main()
