"""write_back 子目录内脚本共用：工程路径、JSON 读取、按日索引。"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_GEN_DATA_DIR = os.path.dirname(_SCRIPT_DIR)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_GEN_DATA_DIR))


def ensure_sys_path() -> str:
    """将项目根与 generate_data 加入 sys.path，便于 ``from utils import ...``。"""
    for p in (_PROJECT_ROOT, _GEN_DATA_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)
    return _PROJECT_ROOT


def default_output_dir() -> str:
    return os.path.join(_PROJECT_ROOT, "output")


def load_json_list(path: str) -> list:
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def index_by_record_date(rows: list) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        rd = str(r.get("record_date") or "")
        if rd:
            out[rd] = r
    return out


def find_report_row(report_rows: list, record_date: str) -> Optional[dict]:
    for r in report_rows:
        if isinstance(r, dict) and str(r.get("record_date") or "") == record_date:
            return r
    return None


def record_date_in_range(record_date: str, start_date: Optional[str], end_date: Optional[str]) -> bool:
    """``record_date`` 为 ``YYYY-MM-DD`` 字符串；``start_date`` / ``end_date`` 为闭区间，任一为 None 则该侧不限制。"""
    rd = str(record_date or "")
    if not rd:
        return False
    if start_date and rd < start_date:
        return False
    if end_date and rd > end_date:
        return False
    return True
