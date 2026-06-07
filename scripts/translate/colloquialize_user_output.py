#!/usr/bin/env python3
"""
将 output 目录中 *_en.json 的英文字段改写为更口语化、对话感的英文，并写回源文件。

复用 polish_user_output 的字段路径与多线程编排；默认单次模型调用（无校验轮次），
配合缓存与文件级并行，为最快批量改写路径。

依赖 .env：DOUBAO_API_KEY、BASE_URL、MODEL_NAME。

用法（项目根目录）:
  # 八人格 × 全部类型 × 全部字段，默认 8 路并行
  python scripts/translate/colloquialize_user_output.py --all-fields
  python scripts/translate/colloquialize_user_output.py --all-fields --dry-run

  # 单字段 / 单用户
  python scripts/translate/colloquialize_user_output.py --field sleep_insight
  python scripts/translate/colloquialize_user_output.py --user-id <uid> --field evaluation
  python scripts/translate/colloquialize_user_output.py --target sleep_report --all-fields --workers 4
"""

from __future__ import annotations

import argparse
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
    MODEL_NAME,
    _normalize_translation,
    call_model,
    load_translation_cache,
    render_prompt_template,
    save_translation_cache,
)
from scripts.translate.polish_user_output import (  # noqa: E402
    DEFAULT_ALL_PERSONAS_WORKERS,
    DEFAULT_FILE_WORKERS,
    DEFAULT_TARGETS,
    TARGET_FIELDS,
    _build_jobs,
    _format_field_help,
    _log,
    _parse_targets_for_field,
    _parse_targets_simple,
    _record_failure,
    _resolve_workers,
    count_all_field_strings,
    extract_polishable_strings,
    load_docs,
    load_persona_user_ids,
    validate_field_for_target,
    write_output,
)

COLLOQUIAL_PROMPT_TEMPLATE = "polish_english_colloquial__polish.md"
DEFAULT_COLLOQUIAL_CACHE_FILE = PROJECT_ROOT / "output" / ".colloquial_cache.json"
COLLOQUIAL_TEMPERATURE = 0.5

def colloquialize_once(source: str) -> str:
    prompt = render_prompt_template(COLLOQUIAL_PROMPT_TEMPLATE, {"TEXT": source})
    return _normalize_translation(call_model(prompt, temperature=COLLOQUIAL_TEMPERATURE))


def _resolve_colloquial(
    source: str,
    cache: dict[str, str],
    cache_lock: threading.Lock,
    *,
    use_cache: bool,
) -> str:
    if use_cache:
        with cache_lock:
            cached = cache.get(source)
        if cached is not None:
            return cached
    final = colloquialize_once(source)
    with cache_lock:
        cache[source] = final
    return final


def colloquialize_file(
    file_path: Path,
    basename: str,
    field: str,
    cache: dict[str, str],
    cache_lock: threading.Lock,
    *,
    dry_run: bool,
    use_cache: bool,
) -> int:
    if not file_path.is_file():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    validate_field_for_target(basename, field)
    docs, was_list = load_docs(file_path)
    sources = list(dict.fromkeys(extract_polishable_strings(docs, field)))
    file_tag = file_path.name
    rel = file_path.relative_to(PROJECT_ROOT)

    _log(file_tag, f"类型={basename}，字段={field}，记录 {len(docs)} 条，待改写（去重）{len(sources)} 条")
    if dry_run:
        _log(file_tag, f"[dry-run] 不调用 API，不写文件 → {rel}")
        return len(sources)
    if not sources:
        _log(file_tag, f"字段 {field} 无可改写英文，跳过")
        return 0

    mapping: dict[str, str] = {}
    total = len(sources)
    for idx, source in enumerate(sources, start=1):
        mapping[source] = _resolve_colloquial(source, cache, cache_lock, use_cache=use_cache)
        write_output(file_path, docs, field, mapping, was_list)
        preview = source if len(source) <= 48 else source[:48] + "…"
        _log(file_tag, f"[{idx}/{total}] 已写回 {rel}: {preview}")

    _log(file_tag, f"完成（{total} 条，仅字段 {field} 已更新）")
    return total


def colloquialize_file_all_fields(
    file_path: Path,
    basename: str,
    cache: dict[str, str],
    cache_lock: threading.Lock,
    *,
    dry_run: bool,
    use_cache: bool,
) -> int:
    total = 0
    for field in TARGET_FIELDS[basename]:
        total += colloquialize_file(
            file_path, basename, field, cache, cache_lock,
            dry_run=dry_run, use_cache=use_cache,
        )
    return total


def _run_jobs(
    jobs: list[tuple[Path, str]],
    workers: int,
    run_one: Any,
    continue_on_error: bool,
) -> list[tuple[Path, BaseException]]:
    failed: list[tuple[Path, BaseException]] = []
    if len(jobs) == 1 or workers == 1:
        for job in jobs:
            _safe_run(job, run_one, continue_on_error, failed)
        return failed
    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as executor:
        futures = {executor.submit(run_one, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001
                _record_failure(job, exc, continue_on_error, failed)
    return failed


def _safe_run(
    job: tuple[Path, str],
    run_one: Any,
    continue_on_error: bool,
    failed: list[tuple[Path, BaseException]],
) -> None:
    try:
        run_one(job)
    except Exception as exc:  # noqa: BLE001
        _record_failure(job, exc, continue_on_error, failed)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将 output *_en.json 英文字段改写为口语化英文并写回源文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"各类型可用字段:\n{_format_field_help()}",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--field", help="改写单个字段（必须与 --target 对应）")
    mode.add_argument(
        "--all-fields", action="store_true",
        help="一键改写所选类型的全部字段（默认多线程按文件并行）",
    )
    parser.add_argument("--user-id", default=None, help="单用户 uid（默认全部八人格）")
    parser.add_argument(
        "--target", default="all",
        help=f"文件类型，逗号分隔；默认 all。可选: {', '.join(DEFAULT_TARGETS)}",
    )
    parser.add_argument("--no-cache", action="store_true", help="不使用改写缓存")
    parser.add_argument(
        "--cache-file", type=Path, default=DEFAULT_COLLOQUIAL_CACHE_FILE,
        help=f"改写缓存（默认 {DEFAULT_COLLOQUIAL_CACHE_FILE.relative_to(PROJECT_ROOT)}）",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只统计待改写字段，不调用 API、不写文件",
    )
    parser.add_argument(
        "--continue-on-error", action="store_true",
        help="单文件失败不中断其余任务",
    )
    parser.add_argument(
        "--workers", type=int, default=None,
        help=f"并行文件数（单用户默认 {DEFAULT_FILE_WORKERS}，全人格默认 {DEFAULT_ALL_PERSONAS_WORKERS}）",
    )
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()
    all_fields_mode = args.all_fields
    field = args.field.strip() if args.field else ""
    all_personas = args.user_id is None
    use_cache = not args.no_cache
    workers = _resolve_workers(args.workers, all_personas)

    cache_path = (
        args.cache_file if args.cache_file.is_absolute()
        else PROJECT_ROOT / args.cache_file
    )
    cache = load_translation_cache(cache_path)
    cache_lock = threading.Lock()

    if all_fields_mode:
        basenames = _parse_targets_simple(args.target)
    else:
        basenames = _parse_targets_for_field(args.target, field)

    user_ids = load_persona_user_ids() if all_personas else [args.user_id]
    jobs = _build_jobs(user_ids, basenames)

    print(f"模型: {MODEL_NAME}")
    print(f"Prompt: {COLLOQUIAL_PROMPT_TEMPLATE}")
    if all_fields_mode:
        field_count = sum(len(TARGET_FIELDS[bn]) for bn in basenames)
        print(f"模式: 全部字段（{field_count} 个字段路径 / 类型）")
    else:
        print(f"字段: {field}")
    print(f"范围: {'八人格' if all_personas else '用户 ' + args.user_id}，类型: {', '.join(basenames)}")
    print(f"写回: 源 *_en.json（仅更新目标字段）")
    print(f"缓存: {'开启' if use_cache else '关闭'}；并行: {workers}；校验: 关闭（单次调用，最快）")
    print(f"任务数: {len(jobs)} 个文件")
    if all_fields_mode and jobs:
        est = count_all_field_strings(basenames, user_ids)
        print(f"预估待改写文案（去重）: {est} 条")
    if not jobs:
        print("无待处理任务。")
        return

    def _run_one(job: tuple[Path, str]) -> None:
        path, basename = job
        if all_fields_mode:
            colloquialize_file_all_fields(
                path, basename, cache, cache_lock,
                dry_run=args.dry_run, use_cache=use_cache,
            )
        else:
            colloquialize_file(
                path, basename, field, cache, cache_lock,
                dry_run=args.dry_run, use_cache=use_cache,
            )
        if not args.dry_run:
            with cache_lock:
                save_translation_cache(cache_path, cache)

    failed = _run_jobs(jobs, workers, _run_one, args.continue_on_error)

    if not args.dry_run:
        with cache_lock:
            save_translation_cache(cache_path, cache)
        print(f"\n缓存已更新: {cache_path.relative_to(PROJECT_ROOT)}（{len(cache)} 条）")
    if failed:
        print(f"\n{len(failed)} 个文件失败：")
        for path, exc in failed:
            print(f"  - {path.name}: {exc}")
        raise SystemExit(1)
    print("\n完成。")


if __name__ == "__main__":
    main()
