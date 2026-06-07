"""展会备份流水线共用常量与 IO。"""

from __future__ import annotations

import glob
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from typing import Any, Iterable

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT = os.path.abspath(os.path.join(BACKUP_DIR, "..", ".."))
INSERT_DATA_DIR = os.path.join(PROJECT_ROOT, "scripts", "insert_data")
GEN_DATA_DIR = os.path.join(PROJECT_ROOT, "scripts", "generate_data")

for _p in (PROJECT_ROOT, INSERT_DATA_DIR, GEN_DATA_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mongo_persona_output_specs import load_persona_uids  # noqa: E402
from utils import atomic_write_json  # noqa: E402

DEFAULT_SOURCE_REL = os.path.join("backup", "somni_all_data_展会版本")
DEFAULT_OUTPUT_REL = os.path.join("backup", "somni_all_data_展会版本_processed")
LATEST_AUX_REL = os.path.join("backup", "最新")

PROMPT_SLEEP_TREND_14D = os.path.join(PROJECT_ROOT, "prompt", "sleep_trend_14d_analysis.md")
PROMPT_MORNING_ALARM = os.path.join(
    PROJECT_ROOT, "prompt", "morning_timeline_alarm_context_advisory.md"
)

JUNE1_ANCHOR = "2026-06-01"
FALLBACK_WINDOW_START = "2026-05-19"
DATE_RANGE_START = "2026-06-01"
DATE_RANGE_END = "2026-06-30"

TITLE_MAX_LEN = 14
CONTENT_MAX_LEN = 28
EN_SUFFIX = "_en"


def resolve_path(raw: str | None, default_rel: str) -> str:
    if raw:
        return raw if os.path.isabs(raw) else os.path.join(PROJECT_ROOT, raw)
    return os.path.join(PROJECT_ROOT, default_rel)


def assert_writable_work_dir(data_dir: str) -> None:
    """禁止写入只读源目录 backup/somni_all_data_展会版本/。"""
    work = os.path.abspath(data_dir)
    source = os.path.abspath(resolve_path(None, DEFAULT_SOURCE_REL))
    if work == source:
        raise ValueError(
            f"禁止写入源目录 {DEFAULT_SOURCE_REL}/；请使用 {DEFAULT_OUTPUT_REL}/"
        )


def load_json_list(path: str) -> list[dict[str, Any]]:
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def save_json_list(path: str, rows: list[Any]) -> None:
    atomic_write_json(path, rows, ensure_ascii=False, indent=2)


def index_by_record_date(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        rd = str(row.get("record_date") or "")
        if rd:
            out[rd] = row
    return out


def dates_in_range(start: str, end: str) -> list[str]:
    d0 = datetime.strptime(start[:10], "%Y-%m-%d")
    d1 = datetime.strptime(end[:10], "%Y-%m-%d")
    out: list[str] = []
    cur = d0
    while cur <= d1:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def copy_source_tree(source_dir: str, output_dir: str, *, force: bool = False) -> None:
    src = os.path.abspath(source_dir)
    dst = os.path.abspath(output_dir)
    if not os.path.isdir(src):
        raise FileNotFoundError(f"源目录不存在: {src}")
    if os.path.exists(dst):
        if not force:
            raise FileExistsError(f"输出目录已存在（加 --force 覆盖）: {dst}")
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def list_persona_uids(data_dir: str, uid_filter: str | None = None) -> list[str]:
    if uid_filter:
        return [uid_filter.strip()]
    configured = load_persona_uids()
    present = {
        name.replace("_health_data.json", "")
        for name in os.listdir(data_dir)
        if name.endswith("_health_data.json")
    }
    return sorted(uid for uid in configured if uid in present)


def sleep_report_paths(data_dir: str, uid: str | None = None) -> list[str]:
    pattern = f"{uid}_sleep_report.json" if uid else "*_sleep_report.json"
    return sorted(glob.glob(os.path.join(os.path.abspath(data_dir), pattern)))


def health_dates(uid: str, data_dir: str) -> set[str]:
    rows = load_json_list(os.path.join(data_dir, f"{uid}_health_data.json"))
    return {str(r.get("record_date")) for r in rows if r.get("record_date")}


def report_dates(uid: str, data_dir: str) -> set[str]:
    rows = load_json_list(os.path.join(data_dir, f"{uid}_sleep_report.json"))
    return {str(r.get("record_date")) for r in rows if r.get("record_date")}


def ai_analysis_dates(uid: str, data_dir: str) -> set[str]:
    rows = load_json_list(os.path.join(data_dir, f"{uid}_ai_analysis_14d.json"))
    return {str(r.get("record_date")) for r in rows if r.get("record_date")}


def hidden_discovery_dates(uid: str, data_dir: str) -> set[str]:
    rows = load_json_list(os.path.join(data_dir, f"{uid}_sleep_report.json"))
    found: set[str] = set()
    for row in rows:
        rd = str(row.get("record_date") or "")
        qa = row.get("quality_analysis")
        if not rd or not isinstance(qa, dict):
            continue
        hd = qa.get("hidden_discovery")
        if isinstance(hd, dict) and hd.get("discover"):
            found.add(rd)
    return found


def missing_dates_in_range(existing: set[str], start: str, end: str) -> list[str]:
    return [d for d in dates_in_range(start, end) if d not in existing]


def report_dates_in_range(uid: str, data_dir: str, start: str, end: str) -> list[str]:
    """sleep_report 在 [start, end] 内的 record_date（升序）。"""
    rdates = report_dates(uid, data_dir)
    return sorted(d for d in dates_in_range(start, end) if d in rdates)


def ensure_morning_aux_files(
    uid: str,
    data_dir: str,
    *,
    aux_dir: str | None = None,
) -> dict[str, str]:
    """若缺失则从 backup/最新 复制 weather / traffic，供晨间洞察 LLM 使用。"""
    src_root = resolve_path(aux_dir, LATEST_AUX_REL)
    copied: dict[str, str] = {}
    for suffix in ("weather", "traffic_link_realtime"):
        fname = f"{uid}_{suffix}.json"
        dst = os.path.join(data_dir, fname)
        if os.path.isfile(dst):
            continue
        src = os.path.join(src_root, fname)
        if not os.path.isfile(src):
            continue
        shutil.copy2(src, dst)
        copied[suffix] = dst
    return copied
