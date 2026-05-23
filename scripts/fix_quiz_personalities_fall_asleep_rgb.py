#!/usr/bin/env python3
"""
将 quiz_personalities 中符合条件的 zh 四位人格文档，
入睡阶段（phase=fall_asleep）各 scheme 的 light.color_stops 统一为：
  r=120, g=0, b=0, ww=255, cw=0

筛选：language=zh, len(mhr_codes)=4, mhr_name 不为「测试」/「Test」。

用法（项目根目录）:
  python scripts/fix_quiz_personalities_fall_asleep_rgb.py --dry-run
  python scripts/fix_quiz_personalities_fall_asleep_rgb.py
  python scripts/fix_quiz_personalities_fall_asleep_rgb.py --no-backup

光文案/K 修正请用: scripts/fix_quiz_personalities_fall_asleep_light.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
COLLECTION = "quiz_personalities"
LANGUAGE = "zh"
PHASE = "fall_asleep"
EXCLUDED_MHR_NAMES = frozenset({"测试", "Test"})
TARGET_RGB = (120, 0, 0)
TARGET_WW = 255
TARGET_CW = 0
DEFAULT_BACKUP = Path("output/quiz_personalities_mhr4_zh_before_fall_asleep_rgb_fix.json")


@dataclass(frozen=True)
class ColorStopUpdate:
    period_index: int
    scheme_index: int
    stop_index: int
    r: int
    g: int
    b: int
    ww: int
    cw: int

    def mongo_paths(self) -> dict[str, int]:
        base = (
            f"periods.{self.period_index}.schemes.{self.scheme_index}"
            f".light.color_stops.{self.stop_index}"
        )
        return {
            f"{base}.r": self.r,
            f"{base}.g": self.g,
            f"{base}.b": self.b,
            f"{base}.ww": self.ww,
            f"{base}.cw": self.cw,
        }


def _extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


def base_query() -> dict[str, Any]:
    return {
        "language": LANGUAGE,
        "$expr": {"$eq": [{"$size": "$mhr_codes"}, 4]},
        "mhr_name": {"$nin": list(EXCLUDED_MHR_NAMES)},
    }


def _codes_key(codes: list[Any]) -> str:
    return "-".join(str(c) for c in codes)


def scan_fall_asleep_updates(periods: Any) -> list[ColorStopUpdate]:
    if not isinstance(periods, list):
        return []

    updates: list[ColorStopUpdate] = []
    for pi, period in enumerate(periods):
        if not isinstance(period, dict):
            continue
        if period.get("phase") != PHASE:
            continue
        schemes = period.get("schemes")
        if not isinstance(schemes, list):
            continue
        for si, scheme in enumerate(schemes):
            if not isinstance(scheme, dict):
                continue
            light = scheme.get("light")
            if not isinstance(light, dict):
                continue
            stops = light.get("color_stops")
            if not isinstance(stops, list):
                continue
            for sti, stop in enumerate(stops):
                if not isinstance(stop, dict):
                    continue
                r, g, b = int(stop.get("r") or 0), int(stop.get("g") or 0), int(stop.get("b") or 0)
                ww, cw = int(stop.get("ww") or 0), int(stop.get("cw") or 0)
                if (r, g, b) == TARGET_RGB and ww == TARGET_WW and cw == TARGET_CW:
                    continue
                updates.append(
                    ColorStopUpdate(
                        period_index=pi,
                        scheme_index=si,
                        stop_index=sti,
                        r=TARGET_RGB[0],
                        g=TARGET_RGB[1],
                        b=TARGET_RGB[2],
                        ww=TARGET_WW,
                        cw=TARGET_CW,
                    )
                )
    return updates


def build_mongo_set_payload(updates: list[ColorStopUpdate]) -> dict[str, int]:
    payload: dict[str, int] = {}
    for u in updates:
        for path, val in u.mongo_paths().items():
            if path in payload:
                raise ValueError(f"重复的 Mongo 路径: {path}")
            payload[path] = val
    return payload


def main() -> None:
    os.chdir(PROJECT_ROOT)
    load_dotenv(PROJECT_ROOT / ".env")

    ap = argparse.ArgumentParser(description="入睡阶段 color_stops → r120 g0 b0 ww255 cw0")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写库")
    ap.add_argument(
        "--backup",
        type=Path,
        default=DEFAULT_BACKUP,
        help=f"写库前备份路径（默认 {DEFAULT_BACKUP}）",
    )
    ap.add_argument("--no-backup", action="store_true", help="不写备份文件")
    args = ap.parse_args()

    uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
    if not uri:
        print("请在 .env 中配置 MONGODB_URI（或 MONGO_URI）", file=sys.stderr)
        sys.exit(1)

    db_name = os.getenv("MONGODB_DB") or _extract_db_name_from_uri(uri)

    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    try:
        coll = client[db_name][COLLECTION]
        docs = list(coll.find(base_query()))
    finally:
        client.close()

    print(
        f"查询到 {len(docs)} 条 (language={LANGUAGE}, len(mhr_codes)=4, "
        f"排除 mhr_name∈{sorted(EXCLUDED_MHR_NAMES)})"
    )
    if not docs:
        print("无数据，退出")
        return

    docs_to_write: list[tuple[Any, dict[str, int]]] = []
    total_stops = 0

    for doc in docs:
        codes = doc.get("mhr_codes") or []
        key = _codes_key(codes) if isinstance(codes, list) else "?"
        name = doc.get("mhr_name", "")
        updates = scan_fall_asleep_updates(doc.get("periods"))
        if not updates:
            print(f"  [{key}] {name}: 入睡阶段已是目标值或无可更新 color_stops")
            continue
        payload = build_mongo_set_payload(updates)
        total_stops += len(updates)
        for u in updates:
            print(
                f"  [{key}] {name} | periods.{u.period_index}.schemes.{u.scheme_index}"
                f".color_stops[{u.stop_index}] → r={u.r} g={u.g} b={u.b} ww={u.ww} cw={u.cw}"
            )
        docs_to_write.append((doc["_id"], payload))

    print(
        f"\n汇总: {len(docs)} 文档, 将更新 {len(docs_to_write)} 文档 / "
        f"{total_stops} 个 color_stop"
    )

    if args.dry_run:
        print("[dry-run] 未写库")
        return

    if not docs_to_write:
        print("无变更，退出")
        return

    if not args.no_backup:
        backup_path = args.backup if args.backup.is_absolute() else PROJECT_ROOT / args.backup
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(
            json.dumps(docs, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"已备份原始数据 → {backup_path}")

    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    try:
        coll = client[db_name][COLLECTION]
        for oid, set_payload in docs_to_write:
            coll.update_one({"_id": oid}, {"$set": set_payload})
        print(
            f"已写回 {len(docs_to_write)} 条文档 → {db_name}.{COLLECTION}"
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
