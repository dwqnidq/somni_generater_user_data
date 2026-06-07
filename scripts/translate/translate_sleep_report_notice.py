#!/usr/bin/env python3
"""
翻译 sleep_report_en.json 中 notice.title / notice.content 仍含中文的字段，原地写回同一文件。

不修改、不删除中文源文件（*_sleep_report.json）。仅处理 output/*_sleep_report_en.json。

依赖 .env：DOUBAO_API_KEY、BASE_URL、MODEL_NAME。

用法（项目根目录）:
  python scripts/translate/translate_sleep_report_notice.py --dry-run
  python scripts/translate/translate_sleep_report_notice.py --workers 8
  python scripts/translate/translate_sleep_report_notice.py --all-personas --workers 8
  python scripts/translate/translate_sleep_report_notice.py --user-id 69aea593af5e6cbf08027964
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

DEFAULT_USER_ID = "69aea593af5e6cbf08027964"
DEFAULT_FILE_WORKERS = 3
DEFAULT_ALL_PERSONAS_WORKERS = 8
PERSONAS_CONFIG_PATH = PROJECT_ROOT / "config" / "health_data_personas_config.json"
NOTICE_FIELDS = ("title", "content")

_print_lock = threading.Lock()


def load_persona_user_ids(config_path: Path | None = None) -> list[str]:
    path = config_path or PERSONAS_CONFIG_PATH
    if not path.is_file():
        raise FileNotFoundError(f"人格配置不存在: {path}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    personas = cfg.get("personas") or []
    uids = [str(p["user_id"]) for p in personas if p.get("user_id")]
    if not uids:
        raise ValueError(f"配置中未找到 personas[].user_id: {path}")
    return uids


def discover_sleep_report_en_files(user_ids: list[str] | None = None) -> list[Path]:
    output_dir = PROJECT_ROOT / "output"
    if user_ids is not None:
        paths: list[Path] = []
        for uid in user_ids:
            path = output_dir / f"{uid}_sleep_report_en.json"
            if path.is_file():
                paths.append(path)
        return paths
    return sorted(output_dir.glob("*_sleep_report_en.json"))


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


def extract_notice_strings(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    notice = doc.get("notice")
    if not isinstance(notice, dict):
        return out
    for key in NOTICE_FIELDS:
        value = notice.get(key)
        if isinstance(value, str) and has_chinese(value):
            out.append(value)
    return out


def apply_notice_translations(
    doc: dict[str, Any], mapping: dict[str, str]
) -> dict[str, Any]:
    result = dict(doc)
    notice = result.get("notice")
    if not isinstance(notice, dict):
        return result
    patched = dict(notice)
    for key in NOTICE_FIELDS:
        value = patched.get(key)
        if isinstance(value, str):
            patched[key] = mapping.get(value, value)
    result["notice"] = patched
    return result


def collect_notice_chinese(docs: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for doc in docs:
        out.extend(extract_notice_strings(doc))
    return list(dict.fromkeys(out))


def write_docs(path: Path, docs: list[dict[str, Any]], mapping: dict[str, str]) -> None:
    translated = [apply_notice_translations(doc, mapping) for doc in docs]
    path.write_text(
        json.dumps(translated, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _resolve_translation(
    source: str,
    cache: dict[str, str],
    cache_lock: threading.Lock,
    *,
    translate_passes: int,
    max_verify_rounds: int,
) -> str:
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


def translate_notice_in_file(
    path: Path,
    cache: dict[str, str],
    cache_lock: threading.Lock,
    *,
    dry_run: bool = False,
    translate_passes: int = TRANSLATE_PASSES,
    max_verify_rounds: int = MAX_VERIFY_ROUNDS,
) -> int:
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")

    docs = load_docs(path)
    unique_chinese = collect_notice_chinese(docs)
    file_tag = path.name

    _log(file_tag, f"记录 {len(docs)} 条，notice 待译（去重）: {len(unique_chinese)} 条")
    if not unique_chinese:
        _log(file_tag, "notice 无中文，跳过")
        return 0

    if dry_run:
        api_per_string = max(2, translate_passes) + 1
        _log(
            file_tag,
            f"[dry-run] 每条约 {api_per_string} 次调用/轮，"
            f"最多 {max_verify_rounds} 轮校验",
        )
        return len(unique_chinese)

    translation_map: dict[str, str] = {}
    total = len(unique_chinese)
    out_rel = path.relative_to(PROJECT_ROOT)

    for idx, source in enumerate(unique_chinese, start=1):
        preview = source if len(source) <= 48 else source[:48] + "…"
        with cache_lock:
            cached = cache.get(source)
        if cached is not None:
            translation_map[source] = cached
            write_docs(path, docs, translation_map)
            _log(file_tag, f"[{idx}/{total}] 缓存命中，已写 {out_rel}: {preview}")
            continue

        _log(file_tag, f"[{idx}/{total}] 翻译: {preview}")
        final = _resolve_translation(
            source,
            cache,
            cache_lock,
            translate_passes=translate_passes,
            max_verify_rounds=max_verify_rounds,
        )
        translation_map[source] = final
        write_docs(path, docs, translation_map)
        _log(file_tag, f"[{idx}/{total}] 已写 {out_rel}")

    _log(file_tag, f"完成（{total} 条 notice 译文已落盘）")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="翻译 sleep_report_en.json 中 notice 字段的中文内容（原地写回）",
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help=f"单用户 uid（默认处理全部 *_sleep_report_en.json；与 --all-personas 互斥）",
    )
    parser.add_argument(
        "--all-personas",
        action="store_true",
        help="仅处理配置中八人格的 sleep_report_en.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计待翻译 notice 字段，不写文件、不调用 API",
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
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="单文件失败时不中断其余并行任务",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            f"并行文件数（默认 {DEFAULT_FILE_WORKERS}，"
            f"--all-personas 默认 {DEFAULT_ALL_PERSONAS_WORKERS}，1=串行）"
        ),
    )
    args = parser.parse_args()

    if args.all_personas and args.user_id:
        parser.error("--all-personas 与 --user-id 不能同时使用")

    translate_passes = max(2, args.translate_passes)
    max_verify_rounds = args.max_verify_rounds

    if args.workers is not None:
        file_workers = max(1, args.workers)
    elif args.all_personas:
        file_workers = DEFAULT_ALL_PERSONAS_WORKERS
    else:
        file_workers = DEFAULT_FILE_WORKERS

    cache_path = DEFAULT_CACHE_FILE
    cache = {} if args.no_cache else load_translation_cache(cache_path)
    cache_lock = threading.Lock()

    if args.all_personas:
        user_ids = load_persona_user_ids()
        print(f"八人格 uid: {', '.join(user_ids)}")
        paths = discover_sleep_report_en_files(user_ids)
    elif args.user_id:
        paths = discover_sleep_report_en_files([args.user_id.strip()])
    else:
        paths = discover_sleep_report_en_files()

    if not paths:
        print("无待处理文件（未找到 *_sleep_report_en.json）。")
        return

    print(f"模型: {MODEL_NAME}")
    print(f"每条 {translate_passes} 次翻译 + 最多 {max_verify_rounds} 轮校验")
    print(f"文件并行: {file_workers}（每条译文通过后立即写回 *_sleep_report_en.json）")
    print(f"已校验缓存: {cache_path.relative_to(PROJECT_ROOT)}（{len(cache)} 条）")
    print(f"任务数: {len(paths)} 个文件")

    def _run_job(path: Path) -> int:
        count = translate_notice_in_file(
            path,
            cache,
            cache_lock,
            dry_run=args.dry_run,
            translate_passes=translate_passes,
            max_verify_rounds=max_verify_rounds,
        )
        if not args.dry_run and not args.no_cache:
            with cache_lock:
                save_translation_cache(cache_path, cache)
        return count

    total_strings = 0
    failed_jobs: list[tuple[Path, BaseException]] = []

    if len(paths) == 1 or file_workers == 1:
        for path in paths:
            try:
                total_strings += _run_job(path)
            except Exception as exc:
                if args.continue_on_error:
                    failed_jobs.append((path, exc))
                    _log(path.name, f"失败: {exc}")
                else:
                    raise
    else:
        workers = min(file_workers, len(paths))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_run_job, path): path for path in paths}
            for future in as_completed(futures):
                path = futures[future]
                try:
                    total_strings += future.result()
                except Exception as exc:
                    if args.continue_on_error:
                        failed_jobs.append((path, exc))
                        _log(path.name, f"失败: {exc}")
                    else:
                        raise

    if not args.dry_run and not args.no_cache:
        with cache_lock:
            save_translation_cache(cache_path, cache)
        print(f"\n缓存已更新: {cache_path.relative_to(PROJECT_ROOT)}（{len(cache)} 条）")

    if failed_jobs:
        print(f"\n{len(failed_jobs)} 个文件失败，可重跑未完成的文件")
        for path, exc in failed_jobs:
            print(f"  - {path.name}: {exc}")
        raise SystemExit(1)

    if args.dry_run:
        print(f"\n[dry-run] 合计待译 notice 字符串: {total_strings} 条")
    print("\n完成。")


if __name__ == "__main__":
    main()
