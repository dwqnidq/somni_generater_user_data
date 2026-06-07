"""缩短 sleep_report.hidden_discovery 的 title/content，补 icon，去掉 module.tips。"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.patch_sleep_report_discover_icons import (  # noqa: E402
    ICON_BY_TYPE,
    MODULE_FIELD_ORDER,
    MODULE_ICON_URL,
    _ordered_discover_item,
    _ordered_item,
)

from scripts.backup.exhibition.shared import (  # noqa: E402
    CONTENT_MAX_LEN,
    TITLE_MAX_LEN,
    load_json_list,
    save_json_list,
    sleep_report_paths,
)

TITLE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("入睡潜伏期", "入睡速度"),
    ("睡眠浅睡占比", "浅睡占比"),
    ("睡眠效率", "睡眠效率"),
    ("深睡占比", "深睡占比"),
)

NUMERIC_TAIL_RE = re.compile(r"[，,；;]\s*(平均|约|\d).*$")


def shorten_discover_title(raw: str) -> str:
    text = str(raw or "").strip()
    for src, dst in TITLE_REPLACEMENTS:
        text = text.replace(src, dst)
    text = NUMERIC_TAIL_RE.sub("", text)
    if "，" in text and re.search(r"\d", text.split("，", 1)[1]):
        text = text.split("，", 1)[0]
    if len(text) > TITLE_MAX_LEN:
        text = text[:TITLE_MAX_LEN]
    return text


def shorten_discover_content(raw: str) -> str:
    text = str(raw or "").strip()
    for sep in ("。", "；", ";"):
        if sep in text:
            text = text.split(sep, 1)[0] + "。"
            break
    if len(text) > CONTENT_MAX_LEN:
        text = text[: CONTENT_MAX_LEN - 1] + "…"
    return text


def _normalize_module_item(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    row.pop("tips", None)
    if isinstance(row.get("target"), str):
        row["target"] = shorten_discover_title(row["target"])
    if isinstance(row.get("description"), str):
        row["description"] = shorten_discover_content(row["description"])
    return _ordered_item(row, MODULE_FIELD_ORDER, MODULE_ICON_URL)


def _normalize_discover_item(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    metric_type = str(row.get("type") or "").strip()
    if isinstance(row.get("title"), str):
        row["title"] = shorten_discover_title(row["title"])
    if isinstance(row.get("content"), str):
        row["content"] = shorten_discover_content(row["content"])
    icon_url = ICON_BY_TYPE.get(metric_type, row.get("icon") or "")
    if icon_url:
        return _ordered_discover_item(row, icon_url)
    return row


def patch_hidden_discovery(hd: dict[str, Any]) -> bool:
    changed = False
    module = hd.get("module")
    if isinstance(module, list) and module:
        new_module = []
        for item in module:
            if not isinstance(item, dict):
                new_module.append(item)
                continue
            patched = _normalize_module_item(item)
            if patched != item or "tips" in item:
                changed = True
            new_module.append(patched)
        hd["module"] = new_module

    discover = hd.get("discover")
    if isinstance(discover, list) and discover:
        new_discover = []
        for item in discover:
            if not isinstance(item, dict):
                new_discover.append(item)
                continue
            patched = _normalize_discover_item(item)
            if patched != item:
                changed = True
            new_discover.append(patched)
        hd["discover"] = new_discover
    return changed


def shorten_sleep_report_file(path: str, *, dry_run: bool = False) -> dict[str, Any]:
    rows = load_json_list(path)
    days_updated = 0
    for row in rows:
        qa = row.get("quality_analysis")
        if not isinstance(qa, dict):
            continue
        hd = qa.get("hidden_discovery")
        if not isinstance(hd, dict):
            continue
        if patch_hidden_discovery(hd):
            days_updated += 1
    if days_updated and not dry_run:
        save_json_list(path, rows)
    return {"file": path, "days_updated": days_updated, "dry_run": dry_run}


def shorten_all_sleep_reports(data_dir: str, *, uid: str | None = None, dry_run: bool = False) -> list[dict]:
    stats: list[dict] = []
    for path in sleep_report_paths(data_dir, uid):
        stats.append(shorten_sleep_report_file(path, dry_run=dry_run))
    return stats
