#!/usr/bin/env python3
"""
仅补全睡眠报告 main.url：扫描 output 下 *_sleep_report.json，
按 main.title 从 qiniu/uploaded_images.json 查图，只改 url 字段，其余不动。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
_UPLOADED_IMAGES_JSON = Path(PROJECT_ROOT) / "qiniu" / "uploaded_images.json"
os.chdir(PROJECT_ROOT)


def get_image_url_by_name(name: str) -> str:
    """与 sleep_report.notice.get_image_url_by_name 一致。"""
    if not name:
        return ""
    try:
        images = json.loads(_UPLOADED_IMAGES_JSON.read_text(encoding="utf-8"))
    except Exception:
        return ""
    candidates = [name]
    if not name.endswith(".png"):
        candidates.append(f"{name}.png")
    for item in images:
        fn = (item.get("file") or "").strip()
        if not fn:
            continue
        base = fn.rsplit(".", 1)[0] if "." in fn else fn
        if fn in candidates or base == name:
            return item.get("url", "") or ""
    return ""


def get_main_title_image_url(main_title: str) -> str:
    if main_title == "秒睡王者":
        return get_image_url_by_name("秒睡王者") or get_image_url_by_name("秒睡宗师")
    return get_image_url_by_name(main_title)


def patch_sleep_report_main_urls(
    report_path: str,
    *,
    dry_run: bool = False,
) -> tuple[int, int]:
    """返回 (总条数, 实际更新 url 的条数)。"""
    with open(report_path, "r", encoding="utf-8") as f:
        reports = json.load(f)
    if not isinstance(reports, list):
        return 0, 0

    changed = 0
    for rec in reports:
        if not isinstance(rec, dict):
            continue
        main = rec.get("main")
        if not isinstance(main, dict):
            continue
        title = (main.get("title") or "").strip()
        if not title:
            continue
        new_url = get_main_title_image_url(title)
        old_url = main.get("url") or ""
        if new_url != old_url:
            main["url"] = new_url
            changed += 1

    if changed and not dry_run:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(reports, f, ensure_ascii=False, indent=2)
            f.write("\n")

    return len(reports), changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user",
        dest="user_id",
        default=None,
        help="仅处理 output/{id}_sleep_report.json",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="睡眠报告目录（相对项目根），默认 output",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计将更新的条数，不写盘",
    )
    args = parser.parse_args()

    out_dir = args.output_dir.strip() or "output"
    pattern = os.path.join(out_dir, "*_sleep_report.json")
    paths = sorted(glob.glob(pattern))
    if args.user_id:
        uid = str(args.user_id).strip()
        one = os.path.join(out_dir, f"{uid}_sleep_report.json")
        paths = [one] if os.path.isfile(one) else []

    if not paths:
        print(f"未找到匹配文件: {pattern}")
        return

    total_files = 0
    total_days = 0
    for p in paths:
        n_rec, n_changed = patch_sleep_report_main_urls(p, dry_run=bool(args.dry_run))
        if n_changed:
            total_files += 1
            total_days += n_changed
        tag = "[dry-run] " if args.dry_run else ""
        verb = "将更新" if args.dry_run else "已更新"
        print(f"  {tag}{p}: {n_changed}/{n_rec} 条 main.url {verb}")

    print(f"完成：{'将更新' if args.dry_run else '已更新'} {total_files} 个文件，合计 {total_days} 条日报告。")


if __name__ == "__main__":
    main()
