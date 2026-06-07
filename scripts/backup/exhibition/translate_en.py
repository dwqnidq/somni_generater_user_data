"""将展会 processed 目录复制为 *_en.json 并翻译中文字段。"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.translate._common import (  # noqa: E402
    DEFAULT_CACHE_FILE,
    has_chinese,
    load_translation_cache,
    save_translation_cache,
    translate_string_with_verification,
)
from scripts.translate.user_output_fields import (  # noqa: E402
    FIELD_HANDLERS,
    apply_translations,
    collect_translatable_strings,
    infer_basename_from_stem,
)
from scripts.backup.exhibition.shared import EN_SUFFIX, save_json_list  # noqa: E402

COPY_ONLY_STEMS = frozenset({"sleep_art"})
POOL_FILENAME = "sleep_analysis_pool.json"
POOL_EN_FILENAME = f"sleep_analysis_pool{EN_SUFFIX}.json"

# 备份文件名后缀 → user_output_fields 的 basename
STEM_ALIASES: dict[str, str] = {
    "sleep_art": "sleep_art_data",
    "sleep_analysis": "somni_sleep_analysis",
}

_print_lock = threading.Lock()


def _log(tag: str, message: str) -> None:
    with _print_lock:
        print(f"[{tag}] {message}")


def _resolve_basename(stem: str) -> str | None:
    if stem.endswith(EN_SUFFIX):
        stem = stem[: -len(EN_SUFFIX)]
    if stem in STEM_ALIASES:
        return STEM_ALIASES[stem]
    known = tuple(FIELD_HANDLERS.keys())
    return infer_basename_from_stem(stem, known)


def _en_path_for(src: Path) -> Path:
    return src.parent / f"{src.stem}{EN_SUFFIX}.json"


def _copy_only(src: Path, dst: Path) -> None:
    shutil.copy2(src, dst)


def _load_docs(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        return [raw]
    raise ValueError(f"不支持的 JSON 根类型: {path}")


def _translate_pool_file(src: Path, dst: Path, cache: dict[str, str], *, dry_run: bool) -> dict[str, Any]:
    docs = _load_docs(src)
    strings = []
    for doc in docs:
        for key in ("user_name", "evaluation"):
            val = doc.get(key)
            if isinstance(val, str) and has_chinese(val):
                strings.append(val)
    unique = list(dict.fromkeys(strings))
    if dry_run:
        return {"file": str(src), "strings": len(unique), "dry_run": True}
    mapping: dict[str, str] = {}
    for source in unique:
        if source in cache:
            mapping[source] = cache[source]
            continue
        en = translate_string_with_verification(source)
        mapping[source] = en
        cache[source] = en
    out_docs = []
    for doc in docs:
        row = dict(doc)
        for key in ("user_name", "evaluation"):
            if isinstance(row.get(key), str):
                row[key] = mapping.get(row[key], row[key])
        if "language" in row or True:
            row["language"] = "en"
        out_docs.append(row)
    save_json_list(str(dst), out_docs)
    return {"file": str(src), "strings": len(unique), "written": str(dst)}


def _translate_file(src: Path, basename: str, cache: dict[str, str], *, dry_run: bool) -> dict[str, Any]:
    dst = _en_path_for(src)
    docs = _load_docs(src)
    strings = collect_translatable_strings(docs, basename)
    unique = list(dict.fromkeys(strings))
    if dry_run:
        return {"file": str(src), "basename": basename, "strings": len(unique), "dry_run": True}
    mapping: dict[str, str] = {}
    for source in unique:
        if source in cache:
            mapping[source] = cache[source]
            continue
        en = translate_string_with_verification(source)
        mapping[source] = en
        cache[source] = en
    out_docs = apply_translations(docs, basename, mapping)
    save_json_list(str(dst), out_docs)
    return {"file": str(src), "basename": basename, "strings": len(unique), "written": str(dst)}


def copy_all_en_stubs(data_dir: str) -> list[str]:
    """先把全部 .json 复制为 *_en.json（后续再覆盖需翻译的文件）。"""
    root = Path(data_dir)
    written: list[str] = []
    for src in sorted(root.glob("*.json")):
        if src.stem.endswith(EN_SUFFIX):
            continue
        dst = _en_path_for(src)
        shutil.copy2(src, dst)
        written.append(str(dst))
    return written


def translate_exhibition_dir(
    data_dir: str,
    *,
    dry_run: bool = False,
    skip_copy: bool = False,
) -> list[dict[str, Any]]:
    root = Path(data_dir)
    if not root.is_dir():
        raise FileNotFoundError(data_dir)

    if not skip_copy and not dry_run:
        copy_all_en_stubs(data_dir)

    cache = load_translation_cache(DEFAULT_CACHE_FILE)
    results: list[dict[str, Any]] = []

    pool_src = root / POOL_FILENAME
    if pool_src.is_file():
        pool_dst = root / POOL_EN_FILENAME
        if dry_run:
            results.append(_translate_pool_file(pool_src, pool_dst, cache, dry_run=True))
        else:
            results.append(_translate_pool_file(pool_src, pool_dst, cache, dry_run=False))

    for src in sorted(root.glob("*.json")):
        if src.name in (POOL_FILENAME, POOL_EN_FILENAME):
            continue
        if src.stem.endswith(EN_SUFFIX):
            continue

        parts = src.stem.split("_", 1)
        suffix = parts[1] if len(parts) == 2 else src.stem

        if suffix in COPY_ONLY_STEMS:
            dst = _en_path_for(src)
            if not dry_run:
                _copy_only(src, dst)
            results.append({"file": str(src), "action": "copy_only", "dry_run": dry_run})
            continue

        basename = _resolve_basename(suffix)
        if basename is None:
            dst = _en_path_for(src)
            if not dry_run:
                _copy_only(src, dst)
            results.append({"file": str(src), "action": "copy_fallback", "dry_run": dry_run})
            continue

        results.append(_translate_file(src, basename, cache, dry_run=dry_run))

    if not dry_run:
        save_translation_cache(DEFAULT_CACHE_FILE, cache)
    return results
