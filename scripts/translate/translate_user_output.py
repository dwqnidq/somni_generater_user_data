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
  python scripts/translate/translate_user_output.py --workers 3
  python scripts/translate/translate_user_output.py --all-personas
  python scripts/translate/translate_user_output.py --all-personas --workers 8 --dry-run

多文件默认并行（--workers）；--all-personas 时默认 8 路并行（八人格）。
每翻译完一条中文即写回对应 *_en.json，中断可保留已完成句子。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    has_chinese,
    load_translation_cache,
    save_translation_cache,
    translate_string_with_verification,
)
from scripts.translate.user_output_fields import (  # noqa: E402
    FIELD_HANDLERS,
    FIELD_LABELS,
    apply_translations,
    collect_translatable_strings,
    infer_basename_from_stem,
)

DEFAULT_USER_ID = "69aea6f3af5e6cbf0802796a"
DEFAULT_FILE_WORKERS = 3
DEFAULT_ALL_PERSONAS_WORKERS = 8
PERSONAS_CONFIG_PATH = PROJECT_ROOT / "config" / "health_data_personas_config.json"

OUTPUT_BASENAMES: tuple[str, ...] = tuple(FIELD_HANDLERS.keys())

_print_lock = threading.Lock()


def _resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def load_persona_user_ids(config_path: Path | None = None) -> list[str]:
    """从 config/health_data_personas_config.json 读取八人格 user_id。"""
    path = config_path or PERSONAS_CONFIG_PATH
    if not path.is_file():
        raise FileNotFoundError(f"人格配置不存在: {path}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    personas = cfg.get("personas") or []
    uids = [str(p["user_id"]) for p in personas if p.get("user_id")]
    if not uids:
        raise ValueError(f"配置中未找到 personas[].user_id: {path}")
    return uids


def _user_io_paths(user_id: str, basename: str, output_suffix: str) -> tuple[Path, Path]:
    stem = f"{user_id}_{basename}"
    parent = PROJECT_ROOT / "output"
    return parent / f"{stem}.json", parent / f"{stem}{output_suffix}.json"


def _build_jobs_for_users(
    user_ids: list[str],
    basenames: list[str],
    output_suffix: str,
    *,
    skip_missing: bool,
) -> list[tuple[Path, Path, str]]:
    jobs: list[tuple[Path, Path, str]] = []
    for user_id in user_ids:
        for basename in basenames:
            inp, out = _user_io_paths(user_id, basename, output_suffix)
            if not inp.is_file():
                if skip_missing:
                    print(
                        f"  [跳过] 不存在: {inp.relative_to(PROJECT_ROOT)}"
                    )
                    continue
            jobs.append((inp, out, basename))
    return jobs


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


def _log(file_tag: str, message: str) -> None:
    with _print_lock:
        print(f"[{file_tag}] {message}")


def _build_resume_translation_map(
    source_docs: list[dict[str, Any]],
    en_docs: list[dict[str, Any]],
    basename: str,
) -> dict[str, str]:
    """从已有 *_en.json 与中文源文件对齐，恢复已译 source→en 映射。"""
    extract_fn, _ = FIELD_HANDLERS[basename]
    mapping: dict[str, str] = {}
    pair_count = min(len(source_docs), len(en_docs))
    if len(source_docs) != len(en_docs):
        _log(
            "resume",
            f"源/en 记录数不一致 ({len(source_docs)} vs {len(en_docs)})，"
            f"仅对齐前 {pair_count} 条",
        )

    for src, en in zip(source_docs[:pair_count], en_docs[:pair_count]):
        src_strings = extract_fn(src)
        en_strings = extract_fn(en)
        for source_text, en_text in zip(src_strings, en_strings):
            if not has_chinese(source_text):
                continue
            if source_text in mapping:
                continue
            if not has_chinese(en_text) and en_text != source_text:
                mapping[source_text] = en_text
    return mapping


def _write_en_output(
    output_path: Path,
    docs: list[dict[str, Any]],
    basename: str,
    translation_map: dict[str, str],
) -> None:
    en_docs = apply_translations(docs, basename, translation_map)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(en_docs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _resolve_translation(
    source: str,
    cache: dict[str, str],
    cache_lock: threading.Lock,
    *,
    use_cache: bool,
    translate_passes: int,
    max_verify_rounds: int,
) -> str:
    if use_cache:
        with cache_lock:
            cached = cache.get(source)
        if cached is not None:
            return cached

    final = translate_string_with_verification(
        source,
        translate_passes=translate_passes,
        max_verify_rounds=max_verify_rounds,
    )
    with cache_lock:
        cache[source] = final
    return final


def translate_file(
    input_path: Path,
    output_path: Path,
    basename: str,
    cache: dict[str, str],
    cache_lock: threading.Lock,
    *,
    dry_run: bool = False,
    translate_passes: int = TRANSLATE_PASSES,
    max_verify_rounds: int = MAX_VERIFY_ROUNDS,
    use_cache: bool = True,
    resume: bool = False,
) -> int:
    if not input_path.is_file():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    docs = load_docs(input_path)
    translation_map: dict[str, str] = {}
    if resume and output_path.is_file():
        en_docs = load_docs(output_path)
        translation_map = _build_resume_translation_map(docs, en_docs, basename)
        remaining = list(
            dict.fromkeys(collect_translatable_strings(en_docs, basename))
        )
        unique_chinese = [s for s in remaining if s not in translation_map]
        if translation_map:
            _log(
                input_path.name,
                f"断点续翻：已恢复 {len(translation_map)} 条，待译 {len(unique_chinese)} 条",
            )
    else:
        chinese_strings = collect_translatable_strings(docs, basename)
        unique_chinese = list(dict.fromkeys(chinese_strings))
    field_desc = ", ".join(FIELD_LABELS.get(basename, ()))
    file_tag = input_path.name

    _log(file_tag, f"类型={basename}，记录 {len(docs)} 条")
    _log(file_tag, f"翻译字段: {field_desc}")
    _log(file_tag, f"待翻译（去重）: {len(unique_chinese)} 条")
    if unique_chinese == 0:
        _log(file_tag, "指定字段无中文，原样写出")
        if not dry_run:
            _write_en_output(output_path, docs, basename, {})
        return 0

    if dry_run:
        api_per_string = translate_passes + 1
        _log(
            file_tag,
            f"[dry-run] 每条约 {api_per_string} 次调用/轮，"
            f"最多 {max_verify_rounds} 轮校验 → {output_path.relative_to(PROJECT_ROOT)}",
        )
        return len(unique_chinese)

    if unique_chinese == 0 and translation_map:
        if not dry_run:
            _write_en_output(output_path, docs, basename, translation_map)
        _log(file_tag, "断点续翻：无剩余中文，已刷新输出")
        return 0

    total = len(unique_chinese)
    out_rel = output_path.relative_to(PROJECT_ROOT)

    for idx, source in enumerate(unique_chinese, start=1):
        preview = source if len(source) <= 48 else source[:48] + "…"
        if use_cache:
            with cache_lock:
                cached = cache.get(source)
            if cached is not None:
                translation_map[source] = cached
                _write_en_output(output_path, docs, basename, translation_map)
                _log(file_tag, f"[{idx}/{total}] 缓存命中，已写 {out_rel}: {preview}")
                continue

        _log(file_tag, f"[{idx}/{total}] 翻译: {preview}")
        final = _resolve_translation(
            source,
            cache,
            cache_lock,
            use_cache=False,
            translate_passes=translate_passes,
            max_verify_rounds=max_verify_rounds,
        )
        translation_map[source] = final
        _write_en_output(output_path, docs, basename, translation_map)
        _log(file_tag, f"[{idx}/{total}] 已写 {out_rel}")

    _log(file_tag, f"完成（{total} 条译文已落盘）")
    return total


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
        help=f"单用户 uid（默认 {DEFAULT_USER_ID}；与 --all-personas 互斥）",
    )
    parser.add_argument(
        "--all-personas",
        action="store_true",
        help="翻译配置中全部八人格（读取 health_data_personas_config.json）",
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
        help=(
            f"校验失败最大重试轮数（默认 {MAX_VERIFY_ROUNDS}；"
            "0=持续重试直至通过，上限见 TRANSLATE_VERIFY_SAFETY_MAX_ROUNDS）"
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="不使用已校验缓存",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="若 *_en.json 已存在，仅翻译其中仍含中文的字段（断点续翻）",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="忽略已有 *_en.json，按源文件全量重译",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="单文件失败时不中断其余并行任务（结束时以非零退出码汇总）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            f"并行翻译的文件任务数（单用户默认 {DEFAULT_FILE_WORKERS}，"
            f"--all-personas 默认 {DEFAULT_ALL_PERSONAS_WORKERS}，1=串行）"
        ),
    )
    args = parser.parse_args()

    if args.all_personas and args.input:
        parser.error("--all-personas 与 --input 不能同时使用")

    translate_passes = max(2, args.translate_passes)
    max_verify_rounds = args.max_verify_rounds
    use_cache = not args.no_cache
    auto_resume = not args.no_resume
    use_resume = args.resume or auto_resume
    if args.workers is not None:
        file_workers = max(1, args.workers)
    elif args.all_personas:
        file_workers = DEFAULT_ALL_PERSONAS_WORKERS
    else:
        file_workers = DEFAULT_FILE_WORKERS

    cache_path = (
        args.cache_file
        if args.cache_file.is_absolute()
        else PROJECT_ROOT / args.cache_file
    )
    cache = load_translation_cache(cache_path)
    cache_lock = threading.Lock()
    print(f"模型: {MODEL_NAME}")
    print(f"每条 {translate_passes} 次翻译 + 最多 {max_verify_rounds} 轮校验")
    scope = "八人格" if args.all_personas else f"用户 {args.user_id}"
    print(f"范围: {scope}")
    print(f"文件并行: {file_workers}（每条译文通过后立即写 *_en.json）")
    print(f"断点续翻: {'开启' if use_resume else '关闭'}")
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
        if args.all_personas:
            user_ids = load_persona_user_ids()
            print(f"八人格 uid: {', '.join(user_ids)}")
            jobs = _build_jobs_for_users(
                user_ids,
                basenames,
                args.output_suffix,
                skip_missing=True,
            )
        else:
            jobs = _build_jobs_for_users(
                [args.user_id],
                basenames,
                args.output_suffix,
                skip_missing=False,
            )

    if not jobs:
        print("无待翻译任务（输入文件均不存在或已被跳过）。")
        return
    print(f"任务数: {len(jobs)} 个文件")

    def _run_job(job: tuple[Path, Path, str]) -> int:
        inp, out, bn = job
        count = translate_file(
            inp,
            out,
            bn,
            cache,
            cache_lock,
            dry_run=args.dry_run,
            translate_passes=translate_passes,
            max_verify_rounds=max_verify_rounds,
            use_cache=use_cache,
            resume=use_resume,
        )
        if not args.dry_run:
            with cache_lock:
                save_translation_cache(cache_path, cache)
        return count

    total_strings = 0
    failed_jobs: list[tuple[tuple[Path, Path, str], BaseException]] = []

    def _handle_result(job: tuple[Path, Path, str], count: int) -> None:
        nonlocal total_strings
        total_strings += count

    def _handle_error(job: tuple[Path, Path, str], exc: BaseException) -> None:
        inp, _, _ = job
        with _print_lock:
            print(f"[{inp.name}] 失败: {exc}")
        failed_jobs.append((job, exc))

    if len(jobs) == 1 or file_workers == 1:
        for job in jobs:
            try:
                _handle_result(job, _run_job(job))
            except Exception as exc:
                if args.continue_on_error:
                    _handle_error(job, exc)
                else:
                    raise
    else:
        workers = min(file_workers, len(jobs))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_run_job, job): job for job in jobs}
            for future in as_completed(futures):
                job = futures[future]
                try:
                    _handle_result(job, future.result())
                except Exception as exc:
                    if args.continue_on_error:
                        _handle_error(job, exc)
                    else:
                        raise

    if not args.dry_run:
        with cache_lock:
            save_translation_cache(cache_path, cache)
        print(f"\n缓存已更新: {cache_path.relative_to(PROJECT_ROOT)}（{len(cache)} 条）")

    if failed_jobs:
        print(f"\n{len(failed_jobs)} 个文件失败，可带 --resume 重跑未完成的 *_en.json")
        for (inp, _, _), exc in failed_jobs:
            print(f"  - {inp.name}: {exc}")
        raise SystemExit(1)

    print("\n完成。")


if __name__ == "__main__":
    main()
