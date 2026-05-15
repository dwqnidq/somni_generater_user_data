#!/usr/bin/env python3
"""
从 aaa.md（睡眠人格干预方案 Markdown）抽取各人格 U/S/M/W 四阶段干预，
生成与消费者约定一致的结构（根级 type=init；phases.scene.config 占位为空对象 {}）。
每条场景的 description 会做简短归一化（如全黑、香氛关闭、静音）。

映射：U→relax 放松，S→fall_asleep 入睡，M→guard 守护，W→wake 唤醒。
光/声/味列分别对应 scenes 中 type=light/sound/scent；空调列忽略。

文档里的人格形如 M-H-R-U 时：前三维 + 文末干预表相同，
自动再生成 M-H-R-S / M-H-R-M / M-H-R-W（及原文末位那条），共四条；仅 mhr_codes 末位不同。
可用 --no-expand-fourth 仅保留表中出现的 8 条。
无参数运行：读取仓库根目录 aaa.md，写入 output/sleep_intervention_schemes.json。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_MD = REPO_ROOT / "aaa.md"
DEFAULT_OUTPUT_JSON = REPO_ROOT / "output" / "sleep_intervention_schemes.json"

# 第四位枚举顺序（与同文件表格阶段 U→S→M→W 一致）
FOURTH_ENUM_ORDER = ("U", "S", "M", "W")

USMW_PHASE = {
    "U": ("relax", "放松"),
    "S": ("fall_asleep", "入睡"),
    "M": ("guard", "守护"),
    "W": ("wake", "唤醒"),
}

SECTION_HEADER_RE = re.compile(r"^##\s+([A-Z]-[A-Z]-[A-Z]-[A-Z])\s*[｜|]\s*(.+?)\s*$")

def _scene_name(text: str, max_chars: int = 80) -> str:
    """与 description 同源，仅按需截断；不含「光干预｜」等 type 前缀。"""
    stripped = text.strip()
    if not stripped:
        return ""
    if len(stripped) <= max_chars:
        return stripped
    return stripped[: max_chars - 1] + "…"


def _normalize_scene_description(kind: str, cell: str) -> str:
    """
    生成用文案归一化：全黑类光仅保留「全黑」；香氛未开→「香氛关闭」；
    音量为 0 / 0dB 等→「静音」。
    """
    s = cell.strip()
    if not s:
        return s
    if kind == "light":
        if "全黑" in s:
            return "全黑"
        if re.match(r"无\s*绝对黑\s*0lux", s):
            return "全黑"
        return s
    if kind == "scent":
        if re.match(r"无\s*0(\s|$)", s):
            return "香氛关闭"
        return s
    if kind == "sound":
        if re.match(r"无\s*0(\s|$)", s):
            return "静音"
        if re.search(r"(?:^|[^\d])0\s*dB(?:[^\d]|$)", s, re.I):
            return "静音"
        if re.search(r"(?:音量|音频)\s*为?\s*0(?:\D|$)", s):
            return "静音"
        return s
    return s


def _phase_row_scene(kind: str, cell: str) -> dict:
    desc = _normalize_scene_description(kind, cell.strip())
    return {
        "type": kind,
        "name": _scene_name(desc),
        "description": desc,
        "config": {},
    }


def _parse_table(rows: list[str]) -> dict[str, dict[str, str]]:
    """Return { 'U': {light,sound,scent}, ... }."""
    out: dict[str, dict[str, str]] = {}
    for raw in rows:
        line = raw.strip()
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 6:
            continue
        # parts[0]='', parts[1]=phase, parts[2..5]=interventions
        phase_key = parts[1].upper().strip()
        if phase_key not in USMW_PHASE:
            continue
        light, sound, scent = parts[2], parts[3], parts[4]
        temp = parts[5] if len(parts) > 5 else ""
        out[phase_key] = {"light": light, "sound": sound, "scent": scent, "temp": temp}
    return out


def _default_schedule(order: tuple[str, ...]) -> list[tuple[str, int]]:
    """(start_time HH:MM, duration_minutes) per phase in ``order``."""
    defaults = {
        "relax": ("23:30", 15),
        "fall_asleep": ("23:45", 15),
        "guard": ("00:00", 420),
        "wake": ("07:30", 15),
    }
    return [(defaults[p][0], defaults[p][1]) for p in order]


def _build_phases(phase_cells: dict[str, dict[str, str]]) -> list[dict]:
    expected = {"U", "S", "M", "W"}
    if set(phase_cells) != expected:
        raise ValueError(
            f"表中阶段行必须为 USMW；当前解析到: {sorted(phase_cells.keys())}"
        )
    schedules = _default_schedule(
        tuple(USMW_PHASE[k][0] for k in ("U", "S", "M", "W"))
    )
    phases: list[dict] = []
    for idx, letter in enumerate(("U", "S", "M", "W")):
        phase_id, zh_name = USMW_PHASE[letter]
        row = phase_cells[letter]
        scenes = [_phase_row_scene("light", row["light"])]
        scenes.append(_phase_row_scene("sound", row["sound"]))
        scenes.append(_phase_row_scene("scent", row["scent"]))
        scenes.append({"type": "temp", "description": row["temp"].strip()})
        start, dur = schedules[idx]
        phases.append(
            {
                "phase": phase_id,
                "phase_name": zh_name,
                "scenes": scenes,
                "description": "",
                "start_time": start,
                "duration_minutes": dur,
            }
        )
    return phases


def _parse_persona_sections(text: str) -> list[tuple[str, str, dict[str, dict[str, str]]]]:
    """
    Return list of (mhr hyphen code, headline rest, phase_rows dict).
    """
    blocks = re.split(r"\n(?=## )", text.strip())
    results: list[tuple[str, str, dict[str, dict[str, str]]]] = []
    for block in blocks:
        lines = block.splitlines()
        if not lines:
            continue
        m = SECTION_HEADER_RE.match(lines[0].strip())
        if not m:
            continue
        code, title_rest = m.group(1), m.group(2).strip()
        table_lines = [ln for ln in lines if ln.strip().startswith("|")]
        parsed = _parse_table(table_lines)
        if len(parsed) != 4:
            raise ValueError(f"人格 {code} 表格不完整，期望 USMW 四行，解析到: {sorted(parsed.keys())}")
        results.append((code, title_rest, parsed))
    return results


def _mhr_code_list(code_hyphen: str) -> list[str]:
    return code_hyphen.split("-")


def expand_mhr_fourth_hyphen(canonical_hyphen_code: str) -> list[str]:
    """
    「M-H-R-U」等同族 → M-H-R-U / M-H-R-S / M-H-R-M / M-H-R-W，干预数据共用文档表。
    """
    parts = canonical_hyphen_code.strip().upper().split("-")
    if len(parts) != 4:
        raise ValueError(f"四维编码必须为 A-B-C-D 格式: {canonical_hyphen_code!r}")
    stem = "-".join(parts[:3])
    return [f"{stem}-{fourth}" for fourth in FOURTH_ENUM_ORDER]


def build_scheme_document(
    mhr_hyphen_code: str,
    persona_display: str,
    *,
    now: datetime | None = None,
    title: str | None = None,
    subtitle: str | None = None,
    phase_cells: dict[str, dict[str, str]],
    include_persona_metadata: bool = False,
    canonical_section_hyphen_code: str | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    iso = now.isoformat().replace("+00:00", "Z")
    ts = iso if iso.endswith("Z") else iso + "Z"
    doc: dict = {
        "type": "init",
        "language": "zh",
        "mhr_codes": _mhr_code_list(mhr_hyphen_code),
        "create_time": ts,
        "phases": _build_phases(phase_cells),
        "subtitle": subtitle or "基于您的测评结果,已为您定制针对性的恢复方案",
        "title": title or "今晚专属睡眠方案",
        "update_time": ts,
    }
    if include_persona_metadata:
        doc["persona_display"] = persona_display
        doc["persona_mhr_hyphen_code"] = mhr_hyphen_code
        if canonical_section_hyphen_code:
            doc["canonical_section_hyphen_code"] = canonical_section_hyphen_code
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description="由 aaa.md 生成睡眠干预方案 JSON")
    ap.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_MD,
        help=f"Markdown 源路径（默认 {DEFAULT_INPUT_MD.name}）",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_JSON,
        help=f"写入 JSON（默认 {DEFAULT_OUTPUT_JSON.relative_to(REPO_ROOT)}）",
    )
    ap.add_argument(
        "--stdout",
        action="store_true",
        help="将 JSON 写到标准输出（不写文件）",
    )
    ap.add_argument(
        "--include-persona-metadata",
        action="store_true",
        help="附带 persona_display、persona_mhr_hyphen_code 便于校对",
    )
    ap.add_argument(
        "--persona",
        metavar="M-H-R-U",
        help="仅导出指定四维的一条（同上族展开后与 --persona 精确比对）",
    )
    ap.add_argument(
        "--no-expand-fourth",
        action="store_true",
        help="不按第四位展开，仅 Markdown 中出现的 8 条人格",
    )
    ap.add_argument(
        "--always-array",
        action="store_true",
        help="即使只有一条也输出 JSON 数组",
    )
    ap.add_argument("--indent", type=int, default=2)
    ns = ap.parse_args()

    text = ns.input.read_text(encoding="utf-8")
    personas = _parse_persona_sections(text)
    docs: list[dict] = []
    persona_filter = ns.persona.strip().upper() if ns.persona else None

    for section_code, headline, parsed in personas:
        if ns.no_expand_fourth:
            codes_to_emit = [section_code.upper()]
        else:
            codes_to_emit = expand_mhr_fourth_hyphen(section_code)

        for out_code in codes_to_emit:
            if persona_filter and out_code != persona_filter:
                continue
            meta = ns.include_persona_metadata
            doc = build_scheme_document(
                out_code,
                headline,
                phase_cells=parsed,
                include_persona_metadata=meta,
                canonical_section_hyphen_code=section_code.upper() if meta else None,
            )
            docs.append(doc)

    if not docs:
        print(f"未找到人格: {ns.persona}", file=sys.stderr)
        sys.exit(1)

    if ns.always_array:
        payload = docs
    else:
        payload = docs if len(docs) > 1 else docs[0]
    dumped = json.dumps(payload, ensure_ascii=False, indent=ns.indent)

    if ns.stdout:
        sys.stdout.write(dumped + ("\n" if not dumped.endswith("\n") else ""))
    else:
        ns.output.parent.mkdir(parents=True, exist_ok=True)
        ns.output.write_text(dumped + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
