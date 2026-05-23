#!/usr/bin/env python3
"""
将睡眠干预方案 JSON 中的中文文案翻译为英文，并输出独立英文文件。

流程（每条中文独立处理，全部通过校验后才写 JSON）：
  1. 对同一句调用模型翻译 N 次（默认 2，环境变量 TRANSLATE_PASSES）
  2. 再调用模型做质检，输出 VERDICT: OK + FINAL 才采纳
  3. 未通过则重新翻译+校验，最多 M 轮（默认 3，TRANSLATE_MAX_VERIFY_ROUNDS）
  4. 当前文件所有句子均通过后，才写入 _en.json

默认输入：
  - output/sleep_intervention_schemes.json
  - output/sleep_intervention_schemes_interv.json

默认输出：
  - output/sleep_intervention_schemes_en.json
  - output/sleep_intervention_schemes_interv_en.json

依赖 .env：DOUBAO_API_KEY、BASE_URL、MODEL_NAME。

用法（在项目根目录）:
  python scripts/translate/translate_sleep_intervention_schemes.py
  python scripts/translate/translate_sleep_intervention_schemes.py --target init
  python scripts/translate/translate_sleep_intervention_schemes.py --dry-run
  python scripts/translate/translate_sleep_intervention_schemes.py --translate-passes 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from scripts.translate._common import (  # noqa: E402
    DEFAULT_CACHE_FILE,
    MAX_VERIFY_ROUNDS,
    MODEL_NAME,
    TRANSLATE_PASSES,
    collect_chinese_strings,
    load_translation_cache,
    save_translation_cache,
    translate_document_list,
)

DEFAULT_TARGETS: dict[str, tuple[str, str]] = {
    "init": (
        "output/sleep_intervention_schemes.json",
        "output/sleep_intervention_schemes_en.json",
    ),
    "interv": (
        "output/sleep_intervention_schemes_interv.json",
        "output/sleep_intervention_schemes_interv_en.json",
    ),
}


def _resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _output_path_for_input(input_path: Path, suffix: str) -> Path:
    stem = input_path.stem
    if stem.endswith(suffix):
        return input_path.parent / f"{stem}.json"
    return input_path.parent / f"{stem}{suffix}.json"


def load_docs(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        return [raw]
    raise ValueError(f"不支持的 JSON 根类型: {type(raw).__name__}")


def translate_file(
    input_path: Path,
    output_path: Path,
    cache: dict[str, str],
    *,
    dry_run: bool = False,
    translate_passes: int = TRANSLATE_PASSES,
    max_verify_rounds: int = MAX_VERIFY_ROUNDS,
    use_cache: bool = True,
) -> int:
    if not input_path.is_file():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    docs = load_docs(input_path)
    chinese_strings: list[str] = []
    collect_chinese_strings(docs, chinese_strings)
    unique_count = len(dict.fromkeys(chinese_strings))

    print(f"\n[{input_path.name}] 记录 {len(docs)} 条，中文字符串 {unique_count} 条")
    if unique_count == 0:
        print("  无中文，跳过")
        return 0

    if dry_run:
        api_per_string = translate_passes + 1
        print(
            f"  [dry-run] 每条约 {api_per_string} 次调用/轮，"
            f"最多 {max_verify_rounds} 轮校验 → {output_path.relative_to(PROJECT_ROOT)}"
        )
        return unique_count

    en_docs = translate_document_list(
        docs,
        cache,
        translate_passes=translate_passes,
        max_verify_rounds=max_verify_rounds,
        use_cache=use_cache,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(en_docs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  已写入 {output_path.relative_to(PROJECT_ROOT)}（{len(en_docs)} 条）")
    return unique_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="翻译睡眠干预方案 JSON（init / interv）为英文版本",
    )
    parser.add_argument(
        "--target",
        choices=["init", "interv", "both"],
        default="both",
        help="要翻译的方案类型：init=完整方案，interv=干预专用（默认 both）",
    )
    parser.add_argument(
        "--input",
        type=str,
        help="自定义输入 JSON（指定后仅处理该文件，需配合 --output）",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="自定义输出 JSON（与 --input 一起使用）",
    )
    parser.add_argument(
        "--output-suffix",
        default="_en",
        help="输出文件名后缀，默认 _en → xxx_en.json",
    )
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=DEFAULT_CACHE_FILE,
        help=f"翻译缓存路径（默认 {DEFAULT_CACHE_FILE.relative_to(PROJECT_ROOT)}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计待翻译字符串，不写文件、不调用 API",
    )
    parser.add_argument(
        "--translate-passes",
        type=int,
        default=TRANSLATE_PASSES,
        help=f"每条中文独立翻译次数（默认 {TRANSLATE_PASSES}，至少 2）",
    )
    parser.add_argument(
        "--max-verify-rounds",
        type=int,
        default=MAX_VERIFY_ROUNDS,
        help=f"校验失败后的最大重试轮数（默认 {MAX_VERIFY_ROUNDS}）",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="不使用已校验缓存，全部重新翻译+校验",
    )
    args = parser.parse_args()

    translate_passes = max(2, args.translate_passes)
    max_verify_rounds = max(1, args.max_verify_rounds)
    use_cache = not args.no_cache

    cache_path = (
        args.cache_file
        if args.cache_file.is_absolute()
        else PROJECT_ROOT / args.cache_file
    )
    cache = load_translation_cache(cache_path)
    print(f"模型: {MODEL_NAME}")
    print(f"每条 {translate_passes} 次翻译 + 最多 {max_verify_rounds} 轮校验")
    print(f"已校验缓存: {cache_path.relative_to(PROJECT_ROOT)}（{len(cache)} 条）")

    jobs: list[tuple[Path, Path]] = []

    if args.input:
        inp = _resolve_path(args.input)
        if args.output:
            out = _resolve_path(args.output)
        else:
            out = _output_path_for_input(inp, args.output_suffix)
        jobs.append((inp, out))
    else:
        keys = list(DEFAULT_TARGETS) if args.target == "both" else [args.target]
        for key in keys:
            rel_in, rel_out = DEFAULT_TARGETS[key]
            jobs.append((_resolve_path(rel_in), _resolve_path(rel_out)))

    total_strings = 0
    for inp, out in jobs:
        total_strings += translate_file(
            inp,
            out,
            cache,
            dry_run=args.dry_run,
            translate_passes=translate_passes,
            max_verify_rounds=max_verify_rounds,
            use_cache=use_cache,
        )

    if not args.dry_run and total_strings > 0:
        save_translation_cache(cache_path, cache)
        print(f"\n缓存已更新: {cache_path.relative_to(PROJECT_ROOT)}（{len(cache)} 条）")

    print("\n完成。")


if __name__ == "__main__":
    main()
