#!/usr/bin/env python3
"""
将指定用户的 output JSON 中「明确定义的中文字段」翻译为英文，其余字段原样保留。

每种类型输出独立 *_en.json。仅翻译 user_output_fields.FIELD_LABELS 中列出的字段。

默认输入/输出见 OUTPUT_BASENAMES。

依赖 .env：DOUBAO_API_KEY、BASE_URL、MODEL_NAME。

用法（项目根目录）:
  python scripts/translate/translate_user_output.py
  python scripts/translate/translate_user_output.py --user-id 69aea6f3af5e6cbf0802796a
  python scripts/translate/translate_user_output.py --target ai_analysis_14d,morning_alarm_insight
  python scripts/translate/translate_user_output.py --dry-run
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
    build_verified_translation_map,
    load_translation_cache,
    save_translation_cache,
)
from scripts.translate.user_output_fields import (  # noqa: E402
    FIELD_HANDLERS,
    FIELD_LABELS,
    apply_translations,
    collect_translatable_strings,
    infer_basename_from_stem,
)

DEFAULT_USER_ID = "69aea6f3af5e6cbf0802796a"

OUTPUT_BASENAMES: tuple[str, ...] = tuple(FIELD_HANDLERS.keys())


def _resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _user_io_paths(user_id: str, basename: str, output_suffix: str) -> tuple[Path, Path]:
    stem = f"{user_id}_{basename}"
    parent = PROJECT_ROOT / "output"
    return parent / f"{stem}.json", parent / f"{stem}{output_suffix}.json"


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
    basename: str,
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
    chinese_strings = collect_translatable_strings(docs, basename)
    unique_chinese = list(dict.fromkeys(chinese_strings))
    field_desc = ", ".join(FIELD_LABELS.get(basename, ()))

    print(f"\n[{input_path.name}] 类型={basename}，记录 {len(docs)} 条")
    print(f"  翻译字段: {field_desc}")
    print(f"  待翻译（去重）: {len(unique_chinese)} 条")
    if unique_chinese == 0:
        print("  指定字段无中文，原样写出")
        if not dry_run:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(docs, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return 0

    if dry_run:
        api_per_string = translate_passes + 1
        print(
            f"  [dry-run] 每条约 {api_per_string} 次调用/轮，"
            f"最多 {max_verify_rounds} 轮校验 → {output_path.relative_to(PROJECT_ROOT)}"
        )
        return len(unique_chinese)

    translation_map = build_verified_translation_map(
        unique_chinese,
        cache,
        translate_passes=translate_passes,
        max_verify_rounds=max_verify_rounds,
        use_cache=use_cache,
    )
    en_docs = apply_translations(docs, basename, translation_map)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(en_docs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  已写入 {output_path.relative_to(PROJECT_ROOT)}（{len(en_docs)} 条）")
    return len(unique_chinese)


def _parse_target_list(raw: str) -> list[str]:
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    unknown = [k for k in keys if k not in OUTPUT_BASENAMES]
    if unknown:
        raise ValueError(
            f"未知 target: {unknown}；可选: {', '.join(OUTPUT_BASENAMES)}"
        )
    return keys


def _basename_for_job(input_path: Path, explicit: str | None) -> str:
    if explicit:
        if explicit not in FIELD_HANDLERS:
            raise ValueError(f"未知类型: {explicit}")
        return explicit
    inferred = infer_basename_from_stem(input_path.stem, OUTPUT_BASENAMES)
    if inferred:
        return inferred
    raise ValueError(
        f"无法从文件名推断类型: {input_path.name}；"
        f"请使用 --basename 指定，或改用标准命名 {{uid}}_<type>.json"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="按字段白名单翻译用户 output JSON 为英文（独立 *_en.json）",
    )
    parser.add_argument(
        "--user-id",
        default=DEFAULT_USER_ID,
        help=f"用户 uid（默认 {DEFAULT_USER_ID}）",
    )
    parser.add_argument(
        "--target",
        default="all",
        help=(
            "要翻译的文件类型，逗号分隔；默认 all。"
            f"可选: {', '.join(OUTPUT_BASENAMES)}"
        ),
    )
    parser.add_argument(
        "--input",
        type=str,
        help="自定义输入 JSON（需能推断类型或配合 --basename）",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="自定义输出 JSON",
    )
    parser.add_argument(
        "--basename",
        type=str,
        choices=list(OUTPUT_BASENAMES),
        help="显式指定 JSON 类型（用于 --input 时文件名无法推断）",
    )
    parser.add_argument(
        "--output-suffix",
        default="_en",
        help="输出文件名后缀，默认 _en",
    )
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=DEFAULT_CACHE_FILE,
        help=f"翻译缓存（默认 {DEFAULT_CACHE_FILE.relative_to(PROJECT_ROOT)}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计待翻译字段，不写文件、不调用 API",
    )
    parser.add_argument(
        "--translate-passes",
        type=int,
        default=TRANSLATE_PASSES,
        help=f"每条中文翻译次数（默认 {TRANSLATE_PASSES}）",
    )
    parser.add_argument(
        "--max-verify-rounds",
        type=int,
        default=MAX_VERIFY_ROUNDS,
        help=f"校验失败最大重试轮数（默认 {MAX_VERIFY_ROUNDS}）",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="不使用已校验缓存",
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

    jobs: list[tuple[Path, Path, str]] = []

    if args.input:
        inp = _resolve_path(args.input)
        out = _resolve_path(args.output) if args.output else _output_path_for_input(
            inp, args.output_suffix
        )
        bn = _basename_for_job(inp, args.basename)
        jobs.append((inp, out, bn))
    else:
        if args.target.strip().lower() == "all":
            basenames = list(OUTPUT_BASENAMES)
        else:
            basenames = _parse_target_list(args.target)
        for basename in basenames:
            inp, out = _user_io_paths(args.user_id, basename, args.output_suffix)
            jobs.append((inp, out, basename))

    total_strings = 0
    for inp, out, basename in jobs:
        total_strings += translate_file(
            inp,
            out,
            basename,
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
