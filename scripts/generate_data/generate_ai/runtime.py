"""generate_ai 脚本共用：路径、环境、health 行加载。"""

from __future__ import annotations

import json
import os
import sys
from typing import List

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
GEN_DATA_DIR = os.path.join(PROJECT_ROOT, "scripts", "generate_data")
GEN_AI_DIR = os.path.join(GEN_DATA_DIR, "generate_ai")

for _p in (PROJECT_ROOT, GEN_DATA_DIR, GEN_AI_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def apply_doubao_env(*, strict: bool = False) -> bool:
    from llm_vendor_config import apply_doubao_vendor_env

    return apply_doubao_vendor_env(strict=strict)


def bootstrap_llm(*, strict: bool = True) -> bool:
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    except ImportError:
        pass
    if not apply_doubao_env(strict=strict):
        return False
    from generate_ai import llm_client
    llm_client.set_model_switch(True)
    return True


def load_health_rows(uid: str, output_dir: str) -> List[dict]:
    path = os.path.join(output_dir, f"{uid}_health_data.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("record_date")]


def iter_uids(output_dir: str) -> List[str]:
    return sorted(
        name.replace("_health_data.json", "")
        for name in os.listdir(output_dir)
        if name.endswith("_health_data.json")
    )
