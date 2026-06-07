#!/usr/bin/env python3
"""为 sleep_report 中 hidden_discovery 写入 icon：module 固定灯泡图标，discover 按 type 映射。"""

from __future__ import annotations

import argparse
import glob
import json
import os
import tempfile
from typing import Any

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)


def _atomic_write_json(path: str, data: object) -> None:
    path = os.path.abspath(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.isfile(tmp_path):
            os.remove(tmp_path)
        raise

MODULE_ICON_URL = "https://cdn.fulai.tech/somni/icon/1780648135_gBi6dGgE9EI.svg"

ICON_BY_TYPE: dict[str, str] = {
    "deep_sleep_ratio": "https://cdn.fulai.tech/somni/icon/1780647263_7PVCXLus4bl.svg",
    "sleep_latency": "https://cdn.fulai.tech/somni/icon/1780647264_kylpiDoXkrG.svg",
    "light_sleep_ratio": "https://cdn.fulai.tech/somni/icon/1780647264_VsY7fa0nPUI.svg",
    "sleep_efficiency": "https://cdn.fulai.tech/somni/icon/1780647264_KtQ3acWoe6r.svg",
}

MODULE_FIELD_ORDER = ("target", "description", "tips", "icon")
DISCOVER_FIELD_ORDER = ("title", "content", "icon", "confidence", "type", "list")


def _ordered_item(item: dict[str, Any], field_order: tuple[str, ...], icon_url: str) -> dict[str, Any]:
    ordered: dict[str, Any] = {}
    for key in field_order:
        if key == "icon":
            ordered[key] = icon_url
        elif key in item:
            ordered[key] = item[key]
    for key, value in item.items():
        if key not in ordered and key != "icon":
            ordered[key] = value
    return ordered


def _ordered_discover_item(item: dict[str, Any], icon_url: str) -> dict[str, Any]:
    return _ordered_item(item, DISCOVER_FIELD_ORDER, icon_url)


def _patch_module_list(module: list[Any]) -> tuple[list[dict[str, Any]], int]:
    patched: list[dict[str, Any]] = []
    updated = 0

    for raw in module:
        if not isinstance(raw, dict):
            patched.append(raw)
            continue
        if raw.get("icon") == MODULE_ICON_URL:
            patched.append(raw)
            continue
        patched.append(_ordered_item(raw, MODULE_FIELD_ORDER, MODULE_ICON_URL))
        updated += 1

    return patched, updated


def _patch_discover_list(discover: list[Any]) -> tuple[list[dict[str, Any]], int, int, list[str]]:
    patched: list[dict[str, Any]] = []
    updated = 0
    skipped = 0
    unknown_types: list[str] = []

    for raw in discover:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        metric_type = str(raw.get("type") or "").strip()
        icon_url = ICON_BY_TYPE.get(metric_type)
        if not icon_url:
            skipped += 1
            if metric_type and metric_type not in unknown_types:
                unknown_types.append(metric_type)
            patched.append(raw)
            continue
        if raw.get("icon") == icon_url:
            patched.append(raw)
            continue
        patched.append(_ordered_discover_item(raw, icon_url))
        updated += 1

    return patched, updated, skipped, unknown_types


def patch_sleep_report_file(path: str, dry_run: bool = False) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        return {"file": path, "error": "root_not_list"}

    file_updated = 0
    module_updated = 0
    discover_updated = 0
    discover_skipped = 0
    unknown_types: list[str] = []
    changed = False

    for row in rows:
        if not isinstance(row, dict):
            continue
        qa = row.get("quality_analysis")
        if not isinstance(qa, dict):
            continue
        hd = qa.get("hidden_discovery")
        if not isinstance(hd, dict):
            continue

        row_changed = False
        module = hd.get("module")
        if isinstance(module, list) and module:
            patched_module, module_count = _patch_module_list(module)
            module_updated += module_count
            if module_count:
                hd["module"] = patched_module
                row_changed = True

        discover = hd.get("discover")
        if isinstance(discover, list) and discover:
            patched_discover, updated, skipped, unknown = _patch_discover_list(discover)
            discover_updated += updated
            discover_skipped += skipped
            for metric_type in unknown:
                if metric_type not in unknown_types:
                    unknown_types.append(metric_type)
            if updated:
                hd["discover"] = patched_discover
                row_changed = True

        if row_changed:
            changed = True
            file_updated += 1

    if changed and not dry_run:
        _atomic_write_json(path, rows)

    return {
        "file": path,
        "days_updated": file_updated,
        "module_items_updated": module_updated,
        "discover_items_updated": discover_updated,
        "discover_items_skipped": discover_skipped,
        "unknown_types": unknown_types,
        "changed": changed,
        "dry_run": dry_run,
    }


def discover_sleep_report_files(output_dir: str) -> list[str]:
    pattern = os.path.join(os.path.abspath(output_dir), "*_sleep_report*.json")
    return sorted(glob.glob(pattern))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default=os.path.join(_PROJECT_ROOT, "output"),
        help="sleep_report JSON 所在目录，默认 output/",
    )
    parser.add_argument(
        "--file",
        default="",
        help="仅处理单个文件（绝对或相对路径）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计变更，不写回文件",
    )
    args = parser.parse_args()

    if args.file.strip():
        paths = [os.path.abspath(args.file.strip())]
    else:
        paths = discover_sleep_report_files(args.output_dir)

    if not paths:
        print(json.dumps({"error": "no_sleep_report_files"}, ensure_ascii=False))
        return

    results: list[dict[str, Any]] = []
    total_days = 0
    total_module = 0
    total_discover = 0
    total_skipped = 0
    all_unknown: list[str] = []

    for path in paths:
        result = patch_sleep_report_file(path, dry_run=args.dry_run)
        results.append(result)
        total_days += int(result.get("days_updated") or 0)
        total_module += int(result.get("module_items_updated") or 0)
        total_discover += int(result.get("discover_items_updated") or 0)
        total_skipped += int(result.get("discover_items_skipped") or 0)
        for metric_type in result.get("unknown_types") or []:
            if metric_type not in all_unknown:
                all_unknown.append(metric_type)

    print(
        json.dumps(
            {
                "files_processed": len(paths),
                "days_updated": total_days,
                "module_items_updated": total_module,
                "discover_items_updated": total_discover,
                "discover_items_skipped": total_skipped,
                "unknown_types": all_unknown,
                "dry_run": args.dry_run,
                "details": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
