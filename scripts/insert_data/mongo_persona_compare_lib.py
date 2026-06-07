"""MongoDB 八人格数据拉取与 output JSON 对比的共享配置与工具。"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from bson import ObjectId
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "health_data_personas_config.json")
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
DEFAULT_PULL_DIR = os.path.join(DEFAULT_OUTPUT_DIR, "_mongo_pull")

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

DEFAULT_MONGO_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or (
    "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive"
)


def extract_db_name(uri: str, fallback: str = "Fullive") -> str:
    try:
        path = (urlparse(uri).path or "").lstrip("/")
        if path:
            return path.split("?")[0]
    except Exception:
        pass
    return os.getenv("MONGODB_DB") or fallback


# output 文件名后缀 -> Mongo 集合（与 insert_somni_records.aaa 对齐）
DATASET_SPECS: list[dict[str, Any]] = [
    {
        "suffix": "health_data",
        "collection": "somni_records",
        "uid_in_file": False,
        "match_keys": ("record_date",),
        "extra_query": {},
    },
    {
        "suffix": "environment_data",
        "collection": "somni_environment_data",
        "uid_in_file": True,
        "match_keys": ("collected_at",),
        "extra_query": {},
    },
    {
        "suffix": "vitals_data",
        "collection": "somni_physiological_data",
        "uid_in_file": True,
        "match_keys": ("collected_at",),
        "extra_query": {},
    },
    {
        "suffix": "sleep_events",
        "collection": "somni_events",
        "uid_in_file": True,
        "match_keys": ("record_date", "_id"),
        "extra_query": {},
    },
    {
        "suffix": "sleep_report",
        "collection": "somni_reports",
        "uid_in_file": True,
        "match_keys": ("record_date",),
        "extra_query": {"language": "zh"},
    },
    {
        "suffix": "somni_sleep_analysis",
        "collection": "somni_sleep_analysis",
        "uid_in_file": True,
        "match_keys": ("stats_date",),
        "extra_query": {},
    },
    {
        "suffix": "sleep_art_data",
        "collection": "somni_dream_universe_assets",
        "uid_in_file": True,
        "match_keys": ("record_date",),
        "extra_query": {},
    },
    {
        "suffix": "daily_emotion_steps",
        "collection": "somni_fusion",
        "uid_in_file": True,
        "match_keys": ("record_date",),
        "extra_query": {},
    },
    {
        "suffix": "calendar_events",
        "collection": "somni_schedules",
        "uid_in_file": True,
        "match_keys": ("start_time", "title"),
        "extra_query": {},
    },
]

_ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")


def load_persona_uids(config_path: str = CONFIG_PATH) -> list[dict[str, str]]:
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    rows: list[dict[str, str]] = []
    for p in cfg.get("personas") or []:
        uid = str(p.get("user_id") or "").strip()
        if uid:
            rows.append(
                {
                    "uid": uid,
                    "name": str(p.get("name") or p.get("identity_type") or uid),
                }
            )
    return rows


def normalize_value(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if isinstance(value, dict):
        return {k: normalize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_value(v) for v in value]
    return value


def normalize_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return normalize_value(deepcopy(doc))


def load_output_json(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return [data]
    if not isinstance(data, list):
        raise ValueError(f"期望 JSON 数组或对象: {path}")
    return data


def record_sort_key(doc: dict[str, Any], keys: tuple[str, ...]) -> tuple:
    parts: list[Any] = []
    for key in keys:
        val = doc.get(key)
        parts.append("" if val is None else str(val))
    if "_id" not in keys and doc.get("_id") is not None:
        parts.append(str(doc.get("_id")))
    return tuple(parts)


def sort_records(records: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda r: record_sort_key(r, keys))


def build_query(uid: str, spec: dict[str, Any]) -> dict[str, Any]:
    q: dict[str, Any] = {"uid": uid}
    q.update(spec.get("extra_query") or {})
    return q


def output_path(output_dir: str, uid: str, suffix: str) -> str:
    return os.path.join(output_dir, f"{uid}_{suffix}.json")


def pull_path(pull_dir: str, uid: str, suffix: str) -> str:
    return os.path.join(pull_dir, f"{uid}_{suffix}.json")


def compare_record_sets(
    output_rows: list[dict[str, Any]],
    mongo_rows: list[dict[str, Any]],
    match_keys: tuple[str, ...],
) -> dict[str, Any]:
    out_norm = [normalize_doc(r) for r in sort_records(output_rows, match_keys)]
    mongo_norm = [normalize_doc(r) for r in sort_records(mongo_rows, match_keys)]

    result: dict[str, Any] = {
        "output_count": len(out_norm),
        "mongo_count": len(mongo_norm),
        "count_match": len(out_norm) == len(mongo_norm),
        "content_match": out_norm == mongo_norm,
        "only_in_output": 0,
        "only_in_mongo": 0,
        "field_mismatch_samples": [],
    }
    if result["content_match"]:
        return result

    out_keys = {record_sort_key(r, match_keys) for r in out_norm}
    mongo_keys = {record_sort_key(r, match_keys) for r in mongo_norm}
    result["only_in_output"] = len(out_keys - mongo_keys)
    result["only_in_mongo"] = len(mongo_keys - out_keys)

    out_by_key = {record_sort_key(r, match_keys): r for r in out_norm}
    mongo_by_key = {record_sort_key(r, match_keys): r for r in mongo_norm}
    for key in sorted(out_keys & mongo_keys)[:5]:
        if out_by_key[key] != mongo_by_key[key]:
            result["field_mismatch_samples"].append(
                {"key": key, "output": out_by_key[key], "mongo": mongo_by_key[key]}
            )
    return result
