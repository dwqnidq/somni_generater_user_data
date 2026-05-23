#!/usr/bin/env python3
"""
入睡阶段（phase=fall_asleep）灯光字段批量修正（zh 四位人格，排除测试）：

1. color_stops：r=120, g=0, b=0, ww=255, cw=0
2. 仅光相关文案（不改 period.description / 声 / 香）：
   - schemes[].light.description → 2700k 暗红光 + 保留原 lux/渐暗 后缀
   - scenes[type=light].description → 2700k 暗红光
   - scenes[type=light].name → 暗红光
   - scenes[type=light].config.color_temp → 2700

写库前默认备份。用法（项目根目录）:
  python scripts/fix_quiz_personalities_fall_asleep_light.py --dry-run
  python scripts/fix_quiz_personalities_fall_asleep_light.py
  python scripts/fix_quiz_personalities_fall_asleep_light.py --skip-rgb
  python scripts/fix_quiz_personalities_fall_asleep_light.py --skip-text
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
TARGET_KELVIN = 2700
LIGHT_LABEL = "暗红光"
DEFAULT_BACKUP = Path(
    "output/quiz_personalities_mhr4_zh_before_fall_asleep_light_fix.json"
)

_SCHEME_K_RE = re.compile(r"^(\d{4})\s*([kK])\s*(.+)$")
_LUX_TAIL_RE = re.compile(r"(\d+\s*→\s*[\d.]+lux.*)$|(\d+→\d+lux.*)$")


@dataclass(frozen=True)
class FieldUpdate:
    path: str
    value: str | int

    def as_set_item(self) -> tuple[str, str | int]:
        return self.path, self.value


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


def normalize_scheme_light_description(description: Any) -> str | None:
    if not isinstance(description, str):
        return None
    text = description.strip()
    if not text or text == "灯光关闭":
        return None
    m = _SCHEME_K_RE.match(text)
    if not m:
        return None
    rest = m.group(3)
    tail_m = _LUX_TAIL_RE.search(rest)
    suffix = f" {tail_m.group(0).strip()}" if tail_m else ""
    return f"{TARGET_KELVIN}k {LIGHT_LABEL}{suffix}"


def normalize_scene_light_description(description: Any) -> str | None:
    if not isinstance(description, str):
        return None
    text = description.strip()
    if not text or text == "无光":
        return None
    target = f"{TARGET_KELVIN}k {LIGHT_LABEL}"
    if text == target:
        return None
    return target


def scan_rgb_updates(periods: Any) -> list[FieldUpdate]:
    if not isinstance(periods, list):
        return []

    out: list[FieldUpdate] = []
    for pi, period in enumerate(periods):
        if not isinstance(period, dict) or period.get("phase") != PHASE:
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
                r, g, b = (
                    int(stop.get("r") or 0),
                    int(stop.get("g") or 0),
                    int(stop.get("b") or 0),
                )
                ww, cw = int(stop.get("ww") or 0), int(stop.get("cw") or 0)
                if (r, g, b) == TARGET_RGB and ww == TARGET_WW and cw == TARGET_CW:
                    continue
                base = (
                    f"periods.{pi}.schemes.{si}.light.color_stops.{sti}"
                )
                out.extend(
                    [
                        FieldUpdate(f"{base}.r", TARGET_RGB[0]),
                        FieldUpdate(f"{base}.g", TARGET_RGB[1]),
                        FieldUpdate(f"{base}.b", TARGET_RGB[2]),
                        FieldUpdate(f"{base}.ww", TARGET_WW),
                        FieldUpdate(f"{base}.cw", TARGET_CW),
                    ]
                )
    return out


def scan_light_text_updates(periods: Any) -> list[FieldUpdate]:
    if not isinstance(periods, list):
        return []

    out: list[FieldUpdate] = []
    for pi, period in enumerate(periods):
        if not isinstance(period, dict) or period.get("phase") != PHASE:
            continue

        scenes = period.get("scenes")
        if isinstance(scenes, list):
            for sci, scene in enumerate(scenes):
                if not isinstance(scene, dict) or scene.get("type") != "light":
                    continue
                base = f"periods.{pi}.scenes.{sci}"
                new_desc = normalize_scene_light_description(
                    scene.get("description")
                )
                if new_desc is not None:
                    out.append(FieldUpdate(f"{base}.description", new_desc))
                if scene.get("name") != LIGHT_LABEL:
                    out.append(FieldUpdate(f"{base}.name", LIGHT_LABEL))
                cfg = scene.get("config")
                if isinstance(cfg, dict):
                    ct = cfg.get("color_temp")
                    if ct is not None and int(ct) != TARGET_KELVIN:
                        out.append(
                            FieldUpdate(
                                f"{base}.config.color_temp", TARGET_KELVIN
                            )
                        )

        schemes = period.get("schemes")
        if not isinstance(schemes, list):
            continue
        for si, scheme in enumerate(schemes):
            if not isinstance(scheme, dict):
                continue
            light = scheme.get("light")
            if not isinstance(light, dict):
                continue
            if light.get("mode") == "off" or light.get("enabled") is False:
                continue
            new_desc = normalize_scheme_light_description(
                light.get("description")
            )
            if new_desc is None:
                continue
            path = f"periods.{pi}.schemes.{si}.light.description"
            if light.get("description") != new_desc:
                out.append(FieldUpdate(path, new_desc))

    return out


def build_mongo_set_payload(updates: list[FieldUpdate]) -> dict[str, str | int]:
    payload: dict[str, str | int] = {}
    for u in updates:
        path, val = u.as_set_item()
        if path in payload:
            raise ValueError(f"重复的 Mongo 路径: {path}")
        payload[path] = val
    return payload


def main() -> None:
    os.chdir(PROJECT_ROOT)
    load_dotenv(PROJECT_ROOT / ".env")

    ap = argparse.ArgumentParser(
        description="入睡阶段：color_stops + 光相关 description/name/K"
    )
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写库")
    ap.add_argument(
        "--backup",
        type=Path,
        default=DEFAULT_BACKUP,
        help=f"写库前备份路径（默认 {DEFAULT_BACKUP}）",
    )
    ap.add_argument("--no-backup", action="store_true", help="不写备份文件")
    ap.add_argument("--skip-rgb", action="store_true", help="不更新 color_stops")
    ap.add_argument(
        "--skip-text", action="store_true", help="不更新光相关文案/K"
    )
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
        f"排除 mhr_name∈{sorted(EXCLUDED_MHR_NAMES)}, phase={PHASE})"
    )
    if not docs:
        print("无数据，退出")
        return

    docs_to_write: list[tuple[Any, dict[str, str | int]]] = []
    total_fields = 0

    for doc in docs:
        codes = doc.get("mhr_codes") or []
        key = _codes_key(codes) if isinstance(codes, list) else "?"
        name = doc.get("mhr_name", "")
        updates: list[FieldUpdate] = []
        if not args.skip_rgb:
            updates.extend(scan_rgb_updates(doc.get("periods")))
        if not args.skip_text:
            updates.extend(scan_light_text_updates(doc.get("periods")))

        if not updates:
            print(f"  [{key}] {name}: 无需更新")
            continue

        payload = build_mongo_set_payload(updates)
        total_fields += len(payload)
        for path, val in sorted(payload.items()):
            print(f"  [{key}] {name} | {path} → {val!r}")
        docs_to_write.append((doc["_id"], payload))

    print(
        f"\n汇总: {len(docs)} 文档, 将更新 {len(docs_to_write)} 文档 / "
        f"{total_fields} 个字段"
    )

    if args.dry_run:
        print("[dry-run] 未写库")
        return

    if not docs_to_write:
        print("无变更，退出")
        return

    if not args.no_backup:
        backup_path = (
            args.backup if args.backup.is_absolute() else PROJECT_ROOT / args.backup
        )
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
