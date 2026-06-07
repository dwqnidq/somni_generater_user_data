"""八人格 output 文件与 MongoDB 集合映射（与 insert_somni_records.py 的 aaa 一致）。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from bson import ObjectId
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
PERSONAS_CONFIG = os.path.join(PROJECT_ROOT, "config", "health_data_personas_config.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
MONGO_PULL_DIR = os.path.join(OUTPUT_DIR, "_mongo_pull")


@dataclass(frozen=True)
class OutputTypeSpec:
    """一类 output 文件与 Mongo 集合的对应关系。"""

    output_suffix: str
    collection: str
    uid_field: str = "uid"
    extra_filter: dict[str, Any] | None = None
    compare_key: str = "_id"


# 与 insert_somni_records.aaa 对齐；output 文件名 = {uid}_{output_suffix}.json
OUTPUT_TYPE_SPECS: tuple[OutputTypeSpec, ...] = (
    OutputTypeSpec("health_data", "somni_records", compare_key="record_date"),
    OutputTypeSpec("environment_data", "somni_environment_data"),
    OutputTypeSpec("vitals_data", "somni_physiological_data"),
    OutputTypeSpec("sleep_events", "somni_events"),
    OutputTypeSpec(
        "sleep_report",
        "somni_reports",
        extra_filter={"language": "zh"},
        compare_key="record_date",
    ),
    OutputTypeSpec(
        "somni_sleep_analysis",
        "somni_sleep_analysis",
        compare_key="stats_date",
    ),
    OutputTypeSpec("sleep_art_data", "somni_dream_universe_assets"),
    OutputTypeSpec("daily_emotion_steps", "somni_fusion", compare_key="record_date"),
    OutputTypeSpec("calendar_events", "somni_schedules"),
    OutputTypeSpec("ai_analysis_14d", "somni_ai_insights", compare_key="record_date"),
)


def load_persona_uids(config_path: str = PERSONAS_CONFIG) -> list[str]:
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    uids: list[str] = []
    for p in cfg.get("personas") or []:
        uid = str(p.get("user_id") or "").strip()
        if uid:
            uids.append(uid)
    return uids


def resolve_mongo_uri() -> tuple[str, str]:
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or ""
    if not uri:
        raise RuntimeError("未配置 MONGODB_URI 或 MONGO_URI（.env）")
    db_name = os.getenv("MONGODB_DB") or ""
    if not db_name:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/").split("?")[0] or "Fullive"
    return uri, db_name


def normalize_value(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return value.astimezone().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if isinstance(value, dict):
        return {k: normalize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_value(v) for v in value]
    return value


def normalize_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return normalize_value(doc)


def output_file_path(uid: str, spec: OutputTypeSpec) -> str:
    return os.path.join(OUTPUT_DIR, f"{uid}_{spec.output_suffix}.json")


def pull_file_path(uid: str, spec: OutputTypeSpec) -> str:
    return os.path.join(MONGO_PULL_DIR, f"{uid}_{spec.output_suffix}.json")


def build_query(uid: str, spec: OutputTypeSpec) -> dict[str, Any]:
    q: dict[str, Any] = {spec.uid_field: uid}
    if spec.extra_filter:
        q.update(spec.extra_filter)
    return q


def parse_type_names(raw: str) -> list[OutputTypeSpec]:
    if not raw.strip():
        return list(OUTPUT_TYPE_SPECS)
    names = {s.strip() for s in raw.split(",") if s.strip()}
    specs = [s for s in OUTPUT_TYPE_SPECS if s.output_suffix in names]
    unknown = names - {s.output_suffix for s in specs}
    if unknown:
        raise ValueError(f"未知类型: {', '.join(sorted(unknown))}")
    return specs
