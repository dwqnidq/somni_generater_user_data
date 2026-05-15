#!/usr/bin/env python3
"""
从 output/sleep_intervention_schemes.json 生成干预专用副本：
- 写入新 JSON（默认 output/sleep_intervention_schemes_interv.json），不覆盖源文件
- phases 去掉 phase 为 guard、wake 的项
- 每条 type 固定为 interv
- 同一三维前缀（前三个 mhr_codes）下，以「标准四码人格」为头，其余三个锚点的 phases 骨架与头一致
- 光/声/味/空调文案与 AI对话方案.md 干预表对齐：relax=U，fall_asleep=S
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.parse_ai_dialogue_scheme_md import apply_row_to_phase, parse_intervention_table_from_file
from scripts.sound_text_normalize import normalize_sound_text

# 三维元组 -> 作为方案模板与 MD 行对应的第四维字母
HEAD_ANCHOR_BY_PREFIX: dict[tuple[str, str, str], str] = {
    ("M", "H", "R"): "U",
    ("M", "H", "C"): "M",
    ("M", "L", "R"): "S",
    ("M", "L", "C"): "M",
    ("E", "H", "R"): "U",
    ("E", "H", "C"): "S",
    ("E", "L", "R"): "W",
    ("E", "L", "C"): "W",
}


def _md_persona_key(pre: tuple[str, str, str]) -> str:
    a = HEAD_ANCHOR_BY_PREFIX[pre]
    return f"{pre[0]}-{pre[1]}-{pre[2]}-{a}"


def _filter_phases(phases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [p for p in phases if p.get("phase") not in ("guard", "wake")]


def _resolve_heads(data: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    heads: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in data:
        codes = item.get("mhr_codes") or []
        if len(codes) < 4:
            continue
        pre = (codes[0], codes[1], codes[2])
        want = HEAD_ANCHOR_BY_PREFIX.get(pre)
        if want is None:
            raise ValueError(f"未知三维前缀: {pre}")
        if codes[3] == want:
            if pre in heads:
                raise ValueError(f"重复的头人格: {''.join(codes)}")
            heads[pre] = item
    missing = set(HEAD_ANCHOR_BY_PREFIX) - set(heads)
    if missing:
        raise ValueError(f"缺少头人格，缺: {missing}")
    return heads


def _normalize_sound_scenes_in_item(item: dict[str, Any]) -> None:
    for p in item.get("phases") or []:
        for sc in p.get("scenes") or []:
            if sc.get("type") != "sound":
                continue
            for k in ("name", "description"):
                v = sc.get(k)
                if isinstance(v, str):
                    sc[k] = normalize_sound_text(v)


def _overlay_md_on_phases(
    phases: list[dict[str, Any]],
    table: dict[str, dict[str, dict[str, str]]],
    pre: tuple[str, str, str],
) -> None:
    key = _md_persona_key(pre)
    row_u = table[key]["U"]
    row_s = table[key]["S"]
    pmap = {p.get("phase"): p for p in phases}
    if "relax" in pmap:
        apply_row_to_phase(pmap["relax"], row_u)
    if "fall_asleep" in pmap:
        apply_row_to_phase(pmap["fall_asleep"], row_s)


def build_interv_schemes(
    data: list[dict[str, Any]],
    md_table: dict[str, dict[str, dict[str, str]]],
) -> list[dict[str, Any]]:
    heads = _resolve_heads(data)
    out: list[dict[str, Any]] = []
    for item in data:
        codes = item.get("mhr_codes") or []
        if len(codes) < 4:
            raise ValueError(f"mhr_codes 长度不足: {codes}")
        pre = (codes[0], codes[1], codes[2])
        head = heads[pre]
        new_item = copy.deepcopy(item)
        new_item["phases"] = copy.deepcopy(_filter_phases(head["phases"]))
        new_item["type"] = "interv"
        for k in ("title", "subtitle"):
            if k in head:
                new_item[k] = head[k]
        _overlay_md_on_phases(new_item["phases"], md_table, pre)
        _normalize_sound_scenes_in_item(new_item)
        out.append(new_item)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--input",
        type=Path,
        default=ROOT / "output" / "sleep_intervention_schemes.json",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=ROOT / "output" / "sleep_intervention_schemes_interv.json",
    )
    ap.add_argument(
        "--md",
        type=Path,
        default=ROOT / "AI对话方案.md",
        help="含「光/声/味/空调干预方案」表的 Markdown",
    )
    args = ap.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("输入应为 JSON 数组")
    md_table = parse_intervention_table_from_file(args.md)
    built = build_interv_schemes(data, md_table)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(built, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(built)} records -> {args.output}")


if __name__ == "__main__":
    main()
