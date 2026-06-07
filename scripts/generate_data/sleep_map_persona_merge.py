"""睡眠地图虚拟池：加载并合并八人格用户的 somni_sleep_analysis 记录。"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
    "health_data_personas_config.json",
)


APP_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
    "config.json",
)


def resolve_current_persona_uid(
    *,
    app_config_path: str = APP_CONFIG_PATH,
    personas_config_path: str = DEFAULT_CONFIG_PATH,
    name: str = "",
    uid: str = "",
) -> tuple[str, str]:
    """解析「当前人格」uid 与展示名。优先 uid，其次 name，否则 config.json 首条 user_profiles。"""
    if uid.strip():
        target = uid.strip()
        with open(personas_config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        for p in cfg.get("personas") or []:
            if str(p.get("user_id") or "").strip() == target:
                label = str(p.get("name") or p.get("identity_type") or target)
                return target, label
        return target, target

    if name.strip():
        needle = name.strip()
        with open(personas_config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        for p in cfg.get("personas") or []:
            if needle in (
                str(p.get("name") or ""),
                str(p.get("identity_type") or ""),
            ):
                u = str(p.get("user_id") or "").strip()
                if u:
                    return u, str(p.get("name") or needle)
        raise ValueError(f"配置中未找到人格: {needle}")

    if not os.path.isfile(app_config_path):
        raise ValueError(f"缺少应用配置: {app_config_path}")
    with open(app_config_path, encoding="utf-8") as f:
        app_cfg = json.load(f)
    profiles = app_cfg.get("user_profiles") or []
    if not profiles:
        raise ValueError("config.json 中 user_profiles 为空")
    first = profiles[0]
    target = str(first.get("user_id") or "").strip()
    if not target:
        raise ValueError("config.json 首条 user_profiles 无 user_id")
    pinfo = first.get("personalInformation") or {}
    label = str(pinfo.get("label") or first.get("identity_type") or target)
    return target, label


def load_persona_uids(config_path: str = DEFAULT_CONFIG_PATH) -> list[str]:
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    uids: list[str] = []
    for persona in cfg.get("personas") or []:
        uid = str(persona.get("user_id") or "").strip()
        if uid:
            uids.append(uid)
    return uids


def persona_uid_set(config_path: str = DEFAULT_CONFIG_PATH) -> frozenset[str]:
    return frozenset(load_persona_uids(config_path))


def filter_virtual_pool_records(
    records: list[dict[str, Any]],
    persona_uids: frozenset[str] | set[str],
) -> list[dict[str, Any]]:
    """从排行池记录中移除八人格 uid（大池仅保留虚拟用户）。"""
    return [
        r
        for r in records
        if str(r.get("uid") or "").strip() not in persona_uids
    ]


def build_ranking_scores_by_date(
    output_dir: str,
    virtual_pool_path: str,
    *,
    config_path: str = DEFAULT_CONFIG_PATH,
    extra_persona_uids: set[str] | None = None,
) -> dict[str, list[tuple[str, int]]]:
    """合并「仅虚拟」排行池 + 各人格 output/{uid}_somni_sleep_analysis.json 用于按日排名。"""
    persona_uids = set(load_persona_uids(config_path))
    if extra_persona_uids:
        persona_uids |= extra_persona_uids

    by_date: dict[str, list[tuple[str, int]]] = defaultdict(list)

    with open(virtual_pool_path, encoding="utf-8") as f:
        pool_rows = json.load(f)
    if isinstance(pool_rows, dict):
        pool_rows = [pool_rows]
    for row in pool_rows:
        uid = str(row.get("uid") or "").strip()
        date_str = str(row.get("stats_date") or "").strip()
        score = row.get("score")
        if not uid or not date_str or score is None or uid in persona_uids:
            continue
        by_date[date_str].append((uid, int(score)))

    for uid in sorted(persona_uids):
        path = os.path.join(output_dir, f"{uid}_somni_sleep_analysis.json")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            date_str = str(row.get("stats_date") or "").strip()
            score = row.get("score")
            if not date_str or score is None:
                continue
            by_date[date_str].append((uid, int(score)))

    return dict(by_date)


def _in_date_range(stats_date: str, start_date: str | None, end_date: str | None) -> bool:
    if start_date and stats_date < start_date:
        return False
    if end_date and stats_date > end_date:
        return False
    return True


def load_persona_analysis_records(
    output_dir: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> tuple[list[dict[str, Any]], list[str]]:
    """从 output/{uid}_somni_sleep_analysis.json 加载人格记录（按 stats_date 过滤）。"""
    persona_uids = load_persona_uids(config_path)
    records: list[dict[str, Any]] = []
    missing: list[str] = []

    for uid in persona_uids:
        path = os.path.join(output_dir, f"{uid}_somni_sleep_analysis.json")
        if not os.path.isfile(path):
            missing.append(uid)
            continue
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            date_str = str(row.get("stats_date") or "").strip()
            if not date_str or not _in_date_range(date_str, start_date, end_date):
                continue
            records.append(row)

    return records, missing


def merge_persona_into_analysis(
    virtual_records: list[dict[str, Any]],
    persona_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """虚拟池合并人格：先去掉池中同 uid 旧记录，再追加人格记录。"""
    persona_uids = {str(r.get("uid") or "").strip() for r in persona_records}
    persona_uids.discard("")
    merged = [r for r in virtual_records if str(r.get("uid") or "") not in persona_uids]
    merged.extend(persona_records)
    merged.sort(
        key=lambda r: (
            str(r.get("stats_date") or ""),
            (r.get("region") or {}).get("district_code", ""),
            -int(r.get("score") or 0),
        )
    )
    return merged
