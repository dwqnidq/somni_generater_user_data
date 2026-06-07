#!/usr/bin/env python3
"""
将已翻译好的用户英文 output JSON 中「单个指定字段」润色为更自然、口语化、低 AI 味的英文，
并写回源 *_en.json（只改该字段的值，其余字段原样保留）。

支持的文件类型与字段（--field 必须与 --target 对应）：

  ai_analysis_14d:
    title, sleep_insight, schedule_insight, fusion_insight

  calendar_events:
    event_name

  somni_sleep_analysis:
    evaluation

  sleep_report:
    main.title, main.summary, sleep_summary.body_battery_status,
    pain_point_analysis.module[].title, pain_point_analysis.module[].description,
    pain_point_analysis.environment_summary.*.status,
    quality_analysis.module[].target, quality_analysis.module[].description,
    quality_analysis.hidden_discovery.module[].target,
    quality_analysis.hidden_discovery.module[].description,
    quality_analysis.hidden_discovery.discover[].title,
    quality_analysis.hidden_discovery.discover[].content

注意：somni_sleep_analysis.user_name（人格名）不润色；sleep_report.notice 由独立脚本处理。

依赖 .env：DOUBAO_API_KEY、BASE_URL、MODEL_NAME。

用法（项目根目录）:
  # 一键：八人格 × 4 类文件 × 全部字段，多线程并行（默认 8 路）
  python scripts/translate/polish_user_output.py --all-fields
  python scripts/translate/polish_user_output.py --all-fields --dry-run

  # 单字段
  python scripts/translate/polish_user_output.py --field sleep_insight --dry-run
  python scripts/translate/polish_user_output.py --target sleep_report --field main.summary
  python scripts/translate/polish_user_output.py --user-id <uid> --field evaluation

--target 默认 all。单字段模式下仅处理包含该字段的文件类型。
--all-fields 模式下按文件并行、文件内各字段顺序执行，写回源 *_en.json。
每润色完一条即写回，中断可保留已完成内容。
"""

from __future__ import annotations

import argparse
import copy
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
    MAX_VERIFY_ROUNDS,
    MODEL_NAME,
    TRANSLATE_PASSES,
    VerifyResult,
    _effective_verify_round_cap,
    _normalize_translation,
    _parse_verify_response,
    _pick_fallback_translation,
    call_model,
    has_chinese,
    load_translation_cache,
    render_prompt_template,
    save_translation_cache,
)

POLISH_PROMPT_TEMPLATE = "polish_english_natural__polish.md"
POLISH_VERIFY_TEMPLATE = "polish_english_natural__verify.md"
DEFAULT_POLISH_CACHE_FILE = PROJECT_ROOT / "output" / ".polish_verified_cache.json"

POLISH_TEMPERATURE = 0.4
VERIFY_TEMPERATURE = 0.0
MIN_VERIFY_CANDIDATES = 2
DEFAULT_FILE_WORKERS = 3
DEFAULT_ALL_PERSONAS_WORKERS = 8
PERSONAS_CONFIG_PATH = PROJECT_ROOT / "config" / "health_data_personas_config.json"
EN_SUFFIX = "_en"

# 每种类型可润色的字段路径（mini 路径语法：. 分层，key[] 遍历列表，* 遍历 dict 全部值）
TARGET_FIELDS: dict[str, tuple[str, ...]] = {
    "ai_analysis_14d": ("title", "sleep_insight", "schedule_insight", "fusion_insight"),
    "calendar_events": ("event_name",),
    "somni_sleep_analysis": ("evaluation",),
    "sleep_report": (
        "main.title",
        "main.summary",
        "sleep_summary.body_battery_status",
        "pain_point_analysis.module[].title",
        "pain_point_analysis.module[].description",
        "pain_point_analysis.environment_summary.*.status",
        "quality_analysis.module[].target",
        "quality_analysis.module[].description",
        "quality_analysis.hidden_discovery.module[].target",
        "quality_analysis.hidden_discovery.module[].description",
        "quality_analysis.hidden_discovery.discover[].title",
        "quality_analysis.hidden_discovery.discover[].content",
    ),
}
DEFAULT_TARGETS: tuple[str, ...] = tuple(TARGET_FIELDS)

_print_lock = threading.Lock()


def _log(file_tag: str, message: str) -> None:
    with _print_lock:
        print(f"[{file_tag}] {message}")


def basenames_for_field(field: str) -> list[str]:
    return [bn for bn, fields in TARGET_FIELDS.items() if field in fields]


def validate_field_for_target(target: str, field: str) -> None:
    if field not in TARGET_FIELDS[target]:
        allowed = ", ".join(TARGET_FIELDS[target])
        raise ValueError(
            f"字段 {field!r} 不属于 {target}；该类型可选: {allowed}"
        )


# --- 字段路径解析：返回 (容器, 键) 列表，容器[键] 为字符串叶子 ---
def _resolve_leaves(node: Any, segments: list[str]) -> list[tuple[Any, Any]]:
    seg = segments[0]
    rest = segments[1:]
    if not rest:
        if isinstance(node, dict) and isinstance(node.get(seg), str):
            return [(node, seg)]
        return []
    if seg.endswith("[]"):
        return _resolve_list(node, seg[:-2], rest)
    if seg == "*":
        return _resolve_wildcard(node, rest)
    child = node.get(seg) if isinstance(node, dict) else None
    return _resolve_leaves(child, rest) if child is not None else []


def _resolve_list(node: Any, key: str, rest: list[str]) -> list[tuple[Any, Any]]:
    leaves: list[tuple[Any, Any]] = []
    items = node.get(key) if isinstance(node, dict) else None
    if isinstance(items, list):
        for item in items:
            leaves.extend(_resolve_leaves(item, rest))
    return leaves


def _resolve_wildcard(node: Any, rest: list[str]) -> list[tuple[Any, Any]]:
    leaves: list[tuple[Any, Any]] = []
    if isinstance(node, dict):
        for value in node.values():
            leaves.extend(_resolve_leaves(value, rest))
    return leaves


def _is_polishable(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or has_chinese(text):
        return False
    return any("a" <= c.lower() <= "z" for c in text)


def extract_polishable_strings(docs: list[dict[str, Any]], field: str) -> list[str]:
    out: list[str] = []
    segments = field.split(".")
    for doc in docs:
        for container, key in _resolve_leaves(doc, segments):
            if _is_polishable(container[key]):
                out.append(container[key])
    return out


def apply_polish(
    docs: list[dict[str, Any]], field: str, mapping: dict[str, str]
) -> list[dict[str, Any]]:
    result = copy.deepcopy(docs)
    segments = field.split(".")
    for doc in result:
        for container, key in _resolve_leaves(doc, segments):
            value = container[key]
            if value in mapping:
                container[key] = mapping[value]
    return result


# --- 模型润色 + 校验 ---
def polish_once(source: str) -> str:
    prompt = render_prompt_template(POLISH_PROMPT_TEMPLATE, {"TEXT": source})
    return _normalize_translation(call_model(prompt, temperature=POLISH_TEMPERATURE))


def verify_polish(source: str, candidates: list[str]) -> VerifyResult:
    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(candidates))
    prompt = render_prompt_template(
        POLISH_VERIFY_TEMPLATE,
        {
            "SOURCE": source,
            "CANDIDATE_COUNT": str(len(candidates)),
            "CANDIDATES": numbered,
        },
    )
    content = call_model(prompt, temperature=VERIFY_TEMPERATURE)
    return _parse_verify_response(content)


def polish_string_with_verification(
    source: str,
    *,
    passes: int = TRANSLATE_PASSES,
    max_verify_rounds: int = MAX_VERIFY_ROUNDS,
    verify: bool = True,
) -> str:
    if not verify:
        return polish_once(source)

    passes = max(MIN_VERIFY_CANDIDATES, passes)
    round_cap = _effective_verify_round_cap(max_verify_rounds)
    unlimited = max_verify_rounds <= 0
    candidates: list[str] = []
    round_idx = 0
    while round_idx < round_cap:
        round_idx += 1
        candidates = [polish_once(source) for _ in range(passes)]
        verdict = verify_polish(source, candidates)
        if verdict.ok:
            return verdict.final
        _log("verify", f"第 {round_idx} 轮未通过: {verdict.reason}")
        if not unlimited and round_idx >= round_cap:
            break

    if not candidates:
        raise RuntimeError(f"润色未产生候选: {source[:80]}")
    return _pick_fallback_translation(candidates)


# --- 文件读写 ---
def load_docs(path: Path) -> tuple[list[dict[str, Any]], bool]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)], True
    if isinstance(raw, dict):
        return [raw], False
    raise ValueError(f"不支持的 JSON 根类型: {type(raw).__name__}")


def write_output(
    path: Path,
    docs: list[dict[str, Any]],
    field: str,
    mapping: dict[str, str],
    was_list: bool,
) -> None:
    polished = apply_polish(docs, field, mapping)
    payload: Any = polished if was_list else polished[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def polish_file(
    file_path: Path,
    basename: str,
    field: str,
    cache: dict[str, str],
    cache_lock: threading.Lock,
    *,
    dry_run: bool,
    passes: int,
    max_verify_rounds: int,
    verify: bool,
    use_cache: bool,
) -> int:
    if not file_path.is_file():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    validate_field_for_target(basename, field)
    docs, was_list = load_docs(file_path)
    sources = list(dict.fromkeys(extract_polishable_strings(docs, field)))
    file_tag = file_path.name
    rel = file_path.relative_to(PROJECT_ROOT)

    _log(file_tag, f"类型={basename}，字段={field}，记录 {len(docs)} 条，待润色（去重）{len(sources)} 条")
    if dry_run:
        _log(file_tag, f"[dry-run] 不调用 API，不写文件 → {rel}")
        return len(sources)
    if not sources:
        _log(file_tag, f"字段 {field} 无可润色英文，跳过")
        return 0

    mapping: dict[str, str] = {}
    total = len(sources)
    for idx, source in enumerate(sources, start=1):
        mapping[source] = _resolve_polish(
            source, cache, cache_lock,
            passes=passes, max_verify_rounds=max_verify_rounds,
            verify=verify, use_cache=use_cache,
        )
        write_output(file_path, docs, field, mapping, was_list)
        preview = source if len(source) <= 48 else source[:48] + "…"
        _log(file_tag, f"[{idx}/{total}] 已写回 {rel}: {preview}")

    _log(file_tag, f"完成（{total} 条，仅字段 {field} 已更新）")
    return total


def polish_file_all_fields(
    file_path: Path,
    basename: str,
    cache: dict[str, str],
    cache_lock: threading.Lock,
    *,
    dry_run: bool,
    passes: int,
    max_verify_rounds: int,
    verify: bool,
    use_cache: bool,
) -> int:
    """单文件内顺序润色该类型的全部字段（每字段写回后再处理下一字段）。"""
    fields = TARGET_FIELDS[basename]
    total = 0
    for field in fields:
        total += polish_file(
            file_path, basename, field, cache, cache_lock,
            dry_run=dry_run, passes=passes,
            max_verify_rounds=max_verify_rounds,
            verify=verify, use_cache=use_cache,
        )
    return total


def _resolve_polish(
    source: str,
    cache: dict[str, str],
    cache_lock: threading.Lock,
    *,
    passes: int,
    max_verify_rounds: int,
    verify: bool,
    use_cache: bool,
) -> str:
    if use_cache:
        with cache_lock:
            cached = cache.get(source)
        if cached is not None:
            return cached
    final = polish_string_with_verification(
        source, passes=passes, max_verify_rounds=max_verify_rounds, verify=verify
    )
    with cache_lock:
        cache[source] = final
    return final


# --- 任务编排 ---
def load_persona_user_ids(config_path: Path = PERSONAS_CONFIG_PATH) -> list[str]:
    if not config_path.is_file():
        raise FileNotFoundError(f"人格配置不存在: {config_path}")
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    uids = [str(p["user_id"]) for p in cfg.get("personas", []) if p.get("user_id")]
    if not uids:
        raise ValueError(f"配置中未找到 personas[].user_id: {config_path}")
    return uids


def _en_path(uid: str, basename: str) -> Path:
    return PROJECT_ROOT / "output" / f"{uid}_{basename}{EN_SUFFIX}.json"


def _build_jobs(user_ids: list[str], basenames: list[str]) -> list[tuple[Path, str]]:
    jobs: list[tuple[Path, str]] = []
    for uid in user_ids:
        for basename in basenames:
            path = _en_path(uid, basename)
            if not path.is_file():
                print(f"  [跳过] 不存在: {path.relative_to(PROJECT_ROOT)}")
                continue
            jobs.append((path, basename))
    return jobs


def _parse_targets_simple(raw: str) -> list[str]:
    if raw.strip().lower() == "all":
        return list(DEFAULT_TARGETS)
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    unknown = [k for k in keys if k not in TARGET_FIELDS]
    if unknown:
        raise ValueError(f"未知 target: {unknown}；可选: {', '.join(DEFAULT_TARGETS)}")
    return keys


def _parse_targets_for_field(raw: str, field: str) -> list[str]:
    matching = basenames_for_field(field)
    if not matching:
        raise ValueError(f"未知字段 {field!r}；可选字段见 --help")

    if raw.strip().lower() == "all":
        return matching

    keys = _parse_targets_simple(raw)
    for key in keys:
        validate_field_for_target(key, field)
    return keys


def count_all_field_strings(basenames: list[str], user_ids: list[str]) -> int:
    total = 0
    for uid in user_ids:
        for basename in basenames:
            path = _en_path(uid, basename)
            if not path.is_file():
                continue
            docs, _ = load_docs(path)
            for field in TARGET_FIELDS[basename]:
                total += len(dict.fromkeys(extract_polishable_strings(docs, field)))
    return total


def _format_field_help() -> str:
    lines: list[str] = []
    for basename, fields in TARGET_FIELDS.items():
        lines.append(f"  {basename}: {', '.join(fields)}")
    return "\n".join(lines)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="润色用户英文 output JSON 的单个指定字段，并写回源 *_en.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"各类型可用字段:\n{_format_field_help()}",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--field",
        help="润色单个字段（必须与 --target 对应，见下方 epilog）",
    )
    mode.add_argument(
        "--all-fields", action="store_true",
        help="一键润色所选类型的全部字段（八人格默认多线程并行）",
    )
    parser.add_argument("--user-id", default=None, help="单用户 uid（默认全部八人格）")
    parser.add_argument(
        "--target", default="all",
        help=f"文件类型，逗号分隔；默认 all。可选: {', '.join(DEFAULT_TARGETS)}",
    )
    parser.add_argument(
        "--passes", type=int, default=TRANSLATE_PASSES,
        help=f"每条润色生成候选数（默认 {TRANSLATE_PASSES}）",
    )
    parser.add_argument(
        "--max-verify-rounds", type=int, default=MAX_VERIFY_ROUNDS,
        help=f"校验失败最大重试轮数（默认 {MAX_VERIFY_ROUNDS}）",
    )
    parser.add_argument("--no-verify", action="store_true", help="关闭润色后校验")
    parser.add_argument("--no-cache", action="store_true", help="不使用润色缓存")
    parser.add_argument(
        "--cache-file", type=Path, default=DEFAULT_POLISH_CACHE_FILE,
        help=f"润色缓存（默认 {DEFAULT_POLISH_CACHE_FILE.relative_to(PROJECT_ROOT)}）",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="只统计待润色字段，不调用 API、不写文件",
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


def _resolve_workers(explicit: int | None, all_personas: bool) -> int:
    if explicit is not None:
        return max(1, explicit)
    return DEFAULT_ALL_PERSONAS_WORKERS if all_personas else DEFAULT_FILE_WORKERS


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


def _record_failure(
    job: tuple[Path, str],
    exc: BaseException,
    continue_on_error: bool,
    failed: list[tuple[Path, BaseException]],
) -> None:
    if not continue_on_error:
        raise exc
    with _print_lock:
        print(f"[{job[0].name}] 失败: {exc}")
    failed.append((job[0], exc))


def main() -> None:
    args = _build_arg_parser().parse_args()
    all_fields_mode = args.all_fields
    field = args.field.strip() if args.field else ""
    all_personas = args.user_id is None
    passes = max(MIN_VERIFY_CANDIDATES, args.passes) if not args.no_verify else max(1, args.passes)
    use_cache = not args.no_cache
    verify = not args.no_verify
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
    if all_fields_mode:
        field_count = sum(len(TARGET_FIELDS[bn]) for bn in basenames)
        print(f"模式: 全部字段（{field_count} 个字段路径 / 类型）")
    else:
        print(f"字段: {field}")
    print(f"范围: {'八人格' if all_personas else '用户 ' + args.user_id}，类型: {', '.join(basenames)}")
    print(f"写回: 源 *_en.json（仅更新目标字段）")
    print(f"校验: {'开启' if verify else '关闭'}；缓存: {'开启' if use_cache else '关闭'}；并行: {workers}")
    print(f"任务数: {len(jobs)} 个文件")
    if all_fields_mode and jobs:
        est = count_all_field_strings(basenames, user_ids)
        print(f"预估待润色文案（去重）: {est} 条")
    if not jobs:
        print("无待处理任务。")
        return

    def _run_one(job: tuple[Path, str]) -> None:
        path, basename = job
        if all_fields_mode:
            polish_file_all_fields(
                path, basename, cache, cache_lock,
                dry_run=args.dry_run, passes=passes,
                max_verify_rounds=args.max_verify_rounds,
                verify=verify, use_cache=use_cache,
            )
        else:
            polish_file(
                path, basename, field, cache, cache_lock,
                dry_run=args.dry_run, passes=passes,
                max_verify_rounds=args.max_verify_rounds,
                verify=verify, use_cache=use_cache,
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
