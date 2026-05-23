#!/usr/bin/env python3
"""
为 quiz_personalities 中符合条件的 zh 四位人格文档，
按 schemes[].light.description 开头的色温 (K) 填充 color_stops 的 cw / ww。

规则：
- 写库前默认备份整份匹配文档。
- K < 2700：仅将 description 开头的 K 改为 2700（其余文案不动），再按 2700K 写 cw/ww。
- K 在 k值.md（2700–6500）：直接写 cw/ww。
- 无法解析 K、或 K 不在表且 ≥2700：跳过。

写库仅 MongoDB 点路径 $set：
  periods.*.schemes.*.light.description（仅 K 被抬高时）
  periods.*.schemes.*.light.color_stops.*.cw / .ww

用法（项目根目录）:
  python scripts/fix_quiz_personalities_light_cw_ww.py --dry-run
  python scripts/fix_quiz_personalities_light_cw_ww.py
  python scripts/fix_quiz_personalities_light_cw_ww.py --no-backup
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
EXCLUDED_MHR_NAMES = frozenset({"测试", "Test"})
MIN_KELVIN = 2700
DEFAULT_BACKUP = Path("output/quiz_personalities_mhr4_zh_before_cw_ww_fix.json")

# 与 k值.md 一致：色温 (K) -> (cw, ww)
K_TO_CW_WW: dict[int, tuple[int, int]] = {
    2700: (0, 255),
    2800: (6, 249),
    2900: (13, 242),
    3000: (20, 235),
    3100: (27, 228),
    3200: (34, 221),
    3300: (41, 214),
    3400: (48, 207),
    3500: (55, 200),
    3600: (62, 193),
    3700: (69, 186),
    3800: (76, 179),
    3900: (83, 172),
    4000: (89, 166),
    4100: (96, 159),
    4200: (103, 152),
    4300: (110, 145),
    4400: (117, 138),
    4500: (124, 131),
    4600: (131, 124),
    4700: (138, 117),
    4800: (145, 110),
    4900: (152, 103),
    5000: (159, 96),
    5100: (166, 89),
    5200: (172, 83),
    5300: (179, 76),
    5400: (186, 69),
    5500: (193, 62),
    5600: (200, 55),
    5700: (207, 48),
    5800: (214, 41),
    5900: (221, 34),
    6000: (228, 27),
    6100: (235, 20),
    6200: (242, 13),
    6300: (249, 6),
    6400: (252, 3),
    6500: (255, 0),
}

_K_PREFIX_RE = re.compile(r"^(\d{4})\s*([kK])")


@dataclass(frozen=True)
class DescriptionFieldUpdate:
    period_index: int
    scheme_index: int
    new_description: str

    def mongo_path(self) -> str:
        return (
            f"periods.{self.period_index}.schemes.{self.scheme_index}"
            f".light.description"
        )


@dataclass(frozen=True)
class CwWwFieldUpdate:
    period_index: int
    scheme_index: int
    stop_index: int
    cw: int
    ww: int

    def mongo_paths(self) -> tuple[str, str]:
        base = (
            f"periods.{self.period_index}.schemes.{self.scheme_index}"
            f".light.color_stops.{self.stop_index}"
        )
        return f"{base}.cw", f"{base}.ww"


@dataclass(frozen=True)
class SchemeChange:
    period_index: int
    scheme_index: int
    phase: str
    scheme_name: str
    description_before: str
    description_after: str
    kelvin_before: int
    kelvin_after: int
    stop_count: int
    old_samples: list[tuple[int, int]]
    new_cw: int
    new_ww: int


@dataclass(frozen=True)
class SkippedScheme:
    period_index: int
    scheme_index: int
    phase: str
    scheme_name: str
    description: str
    reason: str


def _extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


def parse_kelvin_from_description(description: Any) -> int | None:
    if not isinstance(description, str):
        return None
    m = _K_PREFIX_RE.match(description.strip())
    if not m:
        return None
    return int(m.group(1))


def replace_leading_kelvin_only(description: str, new_kelvin: int) -> str:
    """只替换开头的四位 K 数字，保留 k/K 后缀及后续文案。"""
    text = description.strip()
    m = _K_PREFIX_RE.match(text)
    if not m:
        raise ValueError(f"无法替换 K: {description!r}")
    # 保留原 k/K 字母，只改四位数字
    return f"{new_kelvin}{m.group(2)}{text[m.end() :]}"


def resolve_kelvin_and_description(
    description: Any,
    *,
    min_kelvin: int = MIN_KELVIN,
) -> tuple[int | None, str | None, bool]:
    """
    返回 (用于查表的 effective_kelvin, 写库的 description, 是否抬高了 K)。
    无法解析 K 时返回 (None, None, False)。
    """
    if not isinstance(description, str):
        return None, None, False
    raw = description.strip()
    kelvin = parse_kelvin_from_description(raw)
    if kelvin is None:
        return None, None, False
    if kelvin < min_kelvin:
        return min_kelvin, replace_leading_kelvin_only(raw, min_kelvin), True
    return kelvin, raw, False


def lookup_cw_ww(kelvin: int) -> tuple[int, int] | None:
    return K_TO_CW_WW.get(kelvin)


def _codes_key(codes: list[Any]) -> str:
    return "-".join(str(c) for c in codes)


def base_query() -> dict[str, Any]:
    return {
        "language": LANGUAGE,
        "$expr": {"$eq": [{"$size": "$mhr_codes"}, 4]},
        "mhr_name": {"$nin": list(EXCLUDED_MHR_NAMES)},
    }


def scan_periods_for_updates(
    periods: Any,
) -> tuple[
    list[DescriptionFieldUpdate],
    list[CwWwFieldUpdate],
    list[SchemeChange],
    list[SkippedScheme],
]:
    """只读扫描；不修改入参。"""
    if not isinstance(periods, list):
        return [], [], [], []

    desc_updates: list[DescriptionFieldUpdate] = []
    field_updates: list[CwWwFieldUpdate] = []
    changes: list[SchemeChange] = []
    skipped: list[SkippedScheme] = []

    for pi, period in enumerate(periods):
        if not isinstance(period, dict):
            continue
        phase = str(period.get("phase") or period.get("phase_name") or pi)
        schemes = period.get("schemes")
        if not isinstance(schemes, list):
            continue

        for si, scheme in enumerate(schemes):
            if not isinstance(scheme, dict):
                continue
            scheme_name = str(scheme.get("name") or f"scheme_{si}")
            light = scheme.get("light")
            if not isinstance(light, dict):
                skipped.append(
                    SkippedScheme(pi, si, phase, scheme_name, "", "无 light 字段")
                )
                continue

            description_raw = light.get("description", "")
            kelvin_before = parse_kelvin_from_description(description_raw)
            effective_k, description_after, k_raised = resolve_kelvin_and_description(
                description_raw
            )

            if effective_k is None or description_after is None:
                skipped.append(
                    SkippedScheme(
                        pi,
                        si,
                        phase,
                        scheme_name,
                        str(description_raw),
                        "description 开头无法解析 K",
                    )
                )
                continue

            pair = lookup_cw_ww(effective_k)
            if pair is None:
                skipped.append(
                    SkippedScheme(
                        pi,
                        si,
                        phase,
                        scheme_name,
                        str(description_raw),
                        f"K={effective_k} 不在对照表",
                    )
                )
                continue

            cw, ww = pair
            stops = light.get("color_stops")
            if not isinstance(stops, list) or not stops:
                skipped.append(
                    SkippedScheme(
                        pi,
                        si,
                        phase,
                        scheme_name,
                        str(description_raw),
                        "无 color_stops",
                    )
                )
                continue

            if k_raised:
                desc_updates.append(
                    DescriptionFieldUpdate(pi, si, description_after)
                )

            old_samples: list[tuple[int, int]] = []
            stop_count = 0
            for sti, stop in enumerate(stops):
                if not isinstance(stop, dict):
                    continue
                stop_count += 1
                old_samples.append(
                    (int(stop.get("cw") or 0), int(stop.get("ww") or 0))
                )
                field_updates.append(
                    CwWwFieldUpdate(
                        period_index=pi,
                        scheme_index=si,
                        stop_index=sti,
                        cw=cw,
                        ww=ww,
                    )
                )

            if stop_count:
                changes.append(
                    SchemeChange(
                        period_index=pi,
                        scheme_index=si,
                        phase=phase,
                        scheme_name=scheme_name,
                        description_before=str(description_raw).strip()
                        if isinstance(description_raw, str)
                        else str(description_raw),
                        description_after=description_after,
                        kelvin_before=kelvin_before or effective_k,
                        kelvin_after=effective_k,
                        stop_count=stop_count,
                        old_samples=old_samples[:3],
                        new_cw=cw,
                        new_ww=ww,
                    )
                )

    return desc_updates, field_updates, changes, skipped


def build_mongo_set_payload(
    desc_updates: list[DescriptionFieldUpdate],
    field_updates: list[CwWwFieldUpdate],
) -> dict[str, int | str]:
    """$set：仅 light.description（K 抬高时）与 color_stops.*.cw / .ww。"""
    payload: dict[str, int | str] = {}

    for d in desc_updates:
        path = d.mongo_path()
        if path in payload:
            raise ValueError(f"重复的 Mongo 路径: {path}")
        payload[path] = d.new_description

    for u in field_updates:
        cw_path, ww_path = u.mongo_paths()
        for path, val in ((cw_path, u.cw), (ww_path, u.ww)):
            if path in payload:
                raise ValueError(f"重复的 Mongo 路径: {path}")
            payload[path] = val

    return payload


def process_document(
    doc: dict[str, Any],
) -> tuple[
    dict[str, int | str],
    list[SchemeChange],
    list[SkippedScheme],
    int,
]:
    desc_updates, field_updates, changes, skipped = scan_periods_for_updates(
        doc.get("periods")
    )
    if not field_updates:
        return {}, changes, skipped, 0
    payload = build_mongo_set_payload(desc_updates, field_updates)
    return payload, changes, skipped, len(desc_updates)


def main() -> None:
    os.chdir(PROJECT_ROOT)
    load_dotenv(PROJECT_ROOT / ".env")

    ap = argparse.ArgumentParser(
        description="备份后 $set：K<2700 仅抬高 description 的 K，并写 cw/ww"
    )
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

    total_schemes = 0
    total_skipped = 0
    total_desc_fixes = 0
    total_field_sets = 0
    docs_to_write: list[tuple[Any, dict[str, int | str]]] = []

    for doc in docs:
        codes = doc.get("mhr_codes") or []
        key = _codes_key(codes) if isinstance(codes, list) else "?"
        name = doc.get("mhr_name", "")
        set_payload, changes, skipped, desc_n = process_document(doc)
        total_skipped += len(skipped)
        total_desc_fixes += desc_n

        if skipped:
            for s in skipped:
                print(
                    f"  [{key}] {name} | {s.phase}/{s.scheme_name}: 跳过 — {s.reason}"
                    + (f" | {s.description!r}" if s.description else "")
                )

        if not changes:
            print(f"  [{key}] {name}: 无 scheme 将更新")
            continue

        for c in changes:
            sample = c.old_samples[0] if c.old_samples else ("?", "?")
            k_note = ""
            if c.kelvin_before != c.kelvin_after:
                k_note = (
                    f", description K: {c.kelvin_before}→{c.kelvin_after} "
                    f"({c.description_before!r} → {c.description_after!r})"
                )
            print(
                f"  [{key}] {name} | {c.phase}/{c.scheme_name}: "
                f"{c.kelvin_after}K → cw={c.new_cw}, ww={c.new_ww} "
                f"({c.stop_count} stops, 原 cw/ww≈{sample[0]}/{sample[1]}{k_note})"
            )
        total_schemes += len(changes)
        total_field_sets += len(set_payload)
        docs_to_write.append((doc["_id"], set_payload))

    print(
        f"\n汇总: {len(docs)} 文档, 将更新 {len(docs_to_write)} 文档 / "
        f"{total_schemes} 个 scheme / {total_desc_fixes} 条 description(K<2700) / "
        f"{total_field_sets} 个 $set 字段, 跳过 {total_skipped} 个 scheme"
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
            f"已写回 {len(docs_to_write)} 条文档, "
            f"{total_field_sets} 个 $set 字段 → {db_name}.{COLLECTION}"
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
