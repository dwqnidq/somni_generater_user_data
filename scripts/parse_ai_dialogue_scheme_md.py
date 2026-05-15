"""解析 AI对话方案.md 中的「光/声/味/空调干预方案」表。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SECTION = "## 光 / 声 / 味 / 空调干预方案"


def parse_intervention_table(md_text: str) -> dict[str, dict[str, dict[str, str]]]:
    """
    返回: 人格编码 \"M-H-R-U\" -> 阶段 \"U\"|\"S\" -> {\"light\",\"sound\",\"scent\",\"temp\"}
    """
    if SECTION not in md_text:
        raise ValueError(f"Markdown 中未找到章节: {SECTION!r}")
    body = md_text.split(SECTION, 1)[1]
    # 截到下一个 ## 或文件尾
    m = re.search(r"\n## ", body)
    if m:
        body = body[: m.start()]
    rows: dict[str, dict[str, dict[str, str]]] = {}
    for raw in body.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.split("|")]
        while parts and parts[0] == "":
            parts.pop(0)
        while parts and parts[-1] == "":
            parts.pop(-1)
        if len(parts) != 7:
            continue
        code, _name, phase, light, sound, scent, temp = parts
        if phase not in ("U", "S") or not re.match(r"^[ME]-[HL]-[RC]-[MSUW]$", code):
            continue
        rows.setdefault(code, {})[phase] = {
            "light": light,
            "sound": sound,
            "scent": scent,
            "temp": temp,
        }
    expected = {
        "M-H-R-U",
        "M-H-C-M",
        "M-L-R-S",
        "M-L-C-M",
        "E-H-R-U",
        "E-H-C-S",
        "E-L-R-W",
        "E-L-C-W",
    }
    missing = expected - set(rows)
    if missing:
        raise ValueError(f"干预表缺人格或缺 U/S 行: {sorted(missing)}")
    for c in expected:
        if set(rows[c].keys()) != {"U", "S"}:
            raise ValueError(f"{c} 缺少 U 或 S 阶段: {list(rows[c])}")
    return rows


def parse_intervention_table_from_file(path: Path) -> dict[str, dict[str, dict[str, str]]]:
    return parse_intervention_table(path.read_text(encoding="utf-8"))


def apply_row_to_phase(phase_obj: dict[str, Any], row: dict[str, str]) -> None:
    """按 light/sound/scent/temp 写入 phase 的 scenes（原地修改）。"""
    scenes = phase_obj.get("scenes") or []
    by_type = {s.get("type"): s for s in scenes if isinstance(s, dict)}
    mapping = (
        ("light", "light"),
        ("sound", "sound"),
        ("scent", "scent"),
        ("temp", "temp"),
    )
    for scene_type, key in mapping:
        s = by_type.get(scene_type)
        if not isinstance(s, dict):
            continue
        val = row[key]
        if scene_type == "temp":
            s["description"] = val
            s.pop("name", None)
        else:
            s["name"] = val
            s["description"] = val
