#!/usr/bin/env python3
"""将 sound_text_normalize 应用到 output JSON 与 AI对话方案.md，并重新生成 interv 文件。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sound_text_normalize import normalize_sound_fields_in_json, normalize_sound_text


def _fix_md_table_sound_column(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    code_re = re.compile(r"^[ME]-[HL]-[RC]-[MSUW]$")
    for line in lines:
        if not line.startswith("|"):
            out.append(line)
            continue
        parts = [p.strip() for p in line.split("|")]
        while parts and parts[0] == "":
            parts.pop(0)
        while parts and parts[-1] == "":
            parts.pop(-1)
        if len(parts) == 7 and code_re.match(parts[0]):
            parts[4] = normalize_sound_text(parts[4])
            line = "| " + " | ".join(parts) + " |"
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def main() -> None:
    schemes = ROOT / "output" / "sleep_intervention_schemes.json"
    data = json.loads(schemes.read_text(encoding="utf-8"))
    normalize_sound_fields_in_json(data)
    schemes.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"normalized sound fields in {schemes}")

    md = ROOT / "AI对话方案.md"
    md.write_text(_fix_md_table_sound_column(md.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"updated sound column in {md}")

    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_sleep_intervention_schemes_interv.py")],
        check=True,
    )


if __name__ == "__main__":
    main()
