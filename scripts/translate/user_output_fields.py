"""用户 output JSON 的可翻译字段定义：只提取/写回明确含文案的字段。"""

from __future__ import annotations

from typing import Any, Callable

from scripts.translate._common import has_chinese

ExtractFn = Callable[[dict[str, Any]], list[str]]
ApplyFn = Callable[[dict[str, Any], dict[str, str]], dict[str, Any]]


def _append_if_chinese(out: list[str], value: Any) -> None:
    if isinstance(value, str) and has_chinese(value):
        out.append(value)


def _map_str(value: str, mapping: dict[str, str]) -> str:
    return mapping.get(value, value)


# --- ai_analysis_14d ---
def extract_ai_analysis_14d(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("title", "sleep_insight", "schedule_insight"):
        _append_if_chinese(out, doc.get(key))
    return out


def apply_ai_analysis_14d(doc: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    result = dict(doc)
    for key in ("title", "sleep_insight", "schedule_insight"):
        if isinstance(result.get(key), str):
            result[key] = _map_str(result[key], mapping)
    result["language"] = "en"
    return result


# --- morning_alarm_insight ---
def extract_morning_alarm_insight(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    _append_if_chinese(out, doc.get("alarm_insight"))
    return out


def apply_morning_alarm_insight(doc: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    result = dict(doc)
    if isinstance(result.get("alarm_insight"), str):
        result["alarm_insight"] = _map_str(result["alarm_insight"], mapping)
    return result


# --- calendar_events ---
def extract_calendar_events(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    _append_if_chinese(out, doc.get("event_name"))
    return out


def apply_calendar_events(doc: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    result = dict(doc)
    if isinstance(result.get("event_name"), str):
        result["event_name"] = _map_str(result["event_name"], mapping)
    result["language"] = "en"
    return result


# --- sleep_art_data ---
def extract_sleep_art_data(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("title", "description"):
        _append_if_chinese(out, doc.get(key))
    return out


def apply_sleep_art_data(doc: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    result = dict(doc)
    for key in ("title", "description"):
        if isinstance(result.get(key), str):
            result[key] = _map_str(result[key], mapping)
    return result


# --- sleep_pattern_commonality ---
def extract_sleep_pattern_commonality(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    items = doc.get("sleep_pattern_commonality")
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("highlight", "analysis"):
            _append_if_chinese(out, item.get(key))
    return out


def apply_sleep_pattern_commonality(doc: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    result = dict(doc)
    items = result.get("sleep_pattern_commonality")
    if not isinstance(items, list):
        return result
    new_items: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            new_items.append(item)
            continue
        patched = dict(item)
        for key in ("highlight", "analysis"):
            if isinstance(patched.get(key), str):
                patched[key] = _map_str(patched[key], mapping)
        new_items.append(patched)
    result["sleep_pattern_commonality"] = new_items
    return result


# --- sleep_pattern_commonality_insight ---
def extract_sleep_pattern_commonality_insight(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    insight = doc.get("sleep_pattern_commonality_insight")
    if not isinstance(insight, dict):
        return out
    for key in ("target", "description", "tips"):
        _append_if_chinese(out, insight.get(key))
    return out


def apply_sleep_pattern_commonality_insight(
    doc: dict[str, Any], mapping: dict[str, str]
) -> dict[str, Any]:
    result = dict(doc)
    insight = result.get("sleep_pattern_commonality_insight")
    if not isinstance(insight, dict):
        return result
    patched = dict(insight)
    for key in ("target", "description", "tips"):
        if isinstance(patched.get(key), str):
            patched[key] = _map_str(patched[key], mapping)
    result["sleep_pattern_commonality_insight"] = patched
    return result


# --- somni_sleep_analysis ---
def extract_somni_sleep_analysis(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    _append_if_chinese(out, doc.get("user_name"))
    _append_if_chinese(out, doc.get("evaluation"))
    return out


def apply_somni_sleep_analysis(doc: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    result = dict(doc)
    for key in ("user_name", "evaluation"):
        if isinstance(result.get(key), str) and result[key]:
            result[key] = _map_str(result[key], mapping)
    return result


# --- sleep_report (somni_reports) ---
def _append_env_summary_status(out: list[str], env: Any) -> None:
    if not isinstance(env, dict):
        return
    for block in env.values():
        if isinstance(block, dict):
            _append_if_chinese(out, block.get("status"))


def extract_sleep_report(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    main = doc.get("main")
    if isinstance(main, dict):
        for key in ("title", "summary"):
            _append_if_chinese(out, main.get(key))
    summary = doc.get("sleep_summary")
    if isinstance(summary, dict):
        _append_if_chinese(out, summary.get("body_battery_status"))
    pain = doc.get("pain_point_analysis")
    if isinstance(pain, dict):
        modules = pain.get("module")
        if isinstance(modules, list):
            for item in modules:
                if not isinstance(item, dict):
                    continue
                for key in ("title", "description"):
                    _append_if_chinese(out, item.get(key))
        _append_env_summary_status(out, pain.get("environment_summary"))
    quality = doc.get("quality_analysis")
    if isinstance(quality, dict):
        modules = quality.get("module")
        if isinstance(modules, list):
            for item in modules:
                if not isinstance(item, dict):
                    continue
                for key in ("target", "description"):
                    _append_if_chinese(out, item.get(key))
    return out


def apply_sleep_report(doc: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    result = dict(doc)
    main = result.get("main")
    if isinstance(main, dict):
        patched = dict(main)
        for key in ("title", "summary"):
            if isinstance(patched.get(key), str):
                patched[key] = _map_str(patched[key], mapping)
        result["main"] = patched
    summary = result.get("sleep_summary")
    if isinstance(summary, dict):
        patched = dict(summary)
        if isinstance(patched.get("body_battery_status"), str):
            patched["body_battery_status"] = _map_str(
                patched["body_battery_status"], mapping
            )
        result["sleep_summary"] = patched
    pain = result.get("pain_point_analysis")
    if isinstance(pain, dict):
        patched_pain = dict(pain)
        modules = patched_pain.get("module")
        if isinstance(modules, list):
            new_modules: list[Any] = []
            for item in modules:
                if not isinstance(item, dict):
                    new_modules.append(item)
                    continue
                row = dict(item)
                for key in ("title", "description"):
                    if isinstance(row.get(key), str):
                        row[key] = _map_str(row[key], mapping)
                new_modules.append(row)
            patched_pain["module"] = new_modules
        env = patched_pain.get("environment_summary")
        if isinstance(env, dict):
            new_env: dict[str, Any] = {}
            for name, block in env.items():
                if not isinstance(block, dict):
                    new_env[name] = block
                    continue
                row = dict(block)
                if isinstance(row.get("status"), str):
                    row["status"] = _map_str(row["status"], mapping)
                new_env[name] = row
            patched_pain["environment_summary"] = new_env
        result["pain_point_analysis"] = patched_pain
    quality = result.get("quality_analysis")
    if isinstance(quality, dict):
        patched_q = dict(quality)
        modules = patched_q.get("module")
        if isinstance(modules, list):
            new_modules = []
            for item in modules:
                if not isinstance(item, dict):
                    new_modules.append(item)
                    continue
                row = dict(item)
                for key in ("target", "description"):
                    if isinstance(row.get(key), str):
                        row[key] = _map_str(row[key], mapping)
                new_modules.append(row)
            patched_q["module"] = new_modules
        result["quality_analysis"] = patched_q
    result["language"] = "en"
    return result


# --- sleep_events ---
def extract_sleep_events(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    _append_if_chinese(out, doc.get("event_type"))
    detail = doc.get("detail")
    if isinstance(detail, dict):
        for key in ("trigger_cause", "action_taken", "result_summary"):
            _append_if_chinese(out, detail.get(key))
    return out


def apply_sleep_events(doc: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    result = dict(doc)
    if isinstance(result.get("event_type"), str):
        result["event_type"] = _map_str(result["event_type"], mapping)
    detail = result.get("detail")
    if isinstance(detail, dict):
        patched = dict(detail)
        for key in ("trigger_cause", "action_taken", "result_summary"):
            if isinstance(patched.get(key), str):
                patched[key] = _map_str(patched[key], mapping)
        result["detail"] = patched
    return result


FIELD_HANDLERS: dict[str, tuple[ExtractFn, ApplyFn]] = {
    "ai_analysis_14d": (extract_ai_analysis_14d, apply_ai_analysis_14d),
    "morning_alarm_insight": (extract_morning_alarm_insight, apply_morning_alarm_insight),
    "calendar_events": (extract_calendar_events, apply_calendar_events),
    "sleep_art_data": (extract_sleep_art_data, apply_sleep_art_data),
    "sleep_pattern_commonality": (
        extract_sleep_pattern_commonality,
        apply_sleep_pattern_commonality,
    ),
    "sleep_pattern_commonality_insight": (
        extract_sleep_pattern_commonality_insight,
        apply_sleep_pattern_commonality_insight,
    ),
    "somni_sleep_analysis": (extract_somni_sleep_analysis, apply_somni_sleep_analysis),
    "sleep_events": (extract_sleep_events, apply_sleep_events),
    "sleep_report": (extract_sleep_report, apply_sleep_report),
}

# 各类型可翻译字段说明（供日志 / dry-run 展示）
FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "ai_analysis_14d": ("title", "sleep_insight", "schedule_insight"),
    "morning_alarm_insight": ("alarm_insight",),
    "calendar_events": ("event_name",),
    "sleep_art_data": ("title", "description"),
    "sleep_pattern_commonality": (
        "sleep_pattern_commonality[].highlight",
        "sleep_pattern_commonality[].analysis",
    ),
    "sleep_pattern_commonality_insight": (
        "sleep_pattern_commonality_insight.target",
        "sleep_pattern_commonality_insight.description",
        "sleep_pattern_commonality_insight.tips",
    ),
    "somni_sleep_analysis": ("user_name", "evaluation"),
    "sleep_events": (
        "event_type",
        "detail.trigger_cause",
        "detail.action_taken",
        "detail.result_summary",
    ),
    "sleep_report": (
        "main.title",
        "main.summary",
        "sleep_summary.body_battery_status",
        "pain_point_analysis.module[].title",
        "pain_point_analysis.module[].description",
        "pain_point_analysis.environment_summary.*.status",
        "quality_analysis.module[].target",
        "quality_analysis.module[].description",
    ),
}


def infer_basename_from_stem(stem: str, known_basenames: tuple[str, ...]) -> str | None:
    """从文件名 stem 推断类型，如 69aea6f3_..._ai_analysis_14d 或 ai_analysis_14d_en。"""
    name = stem
    if name.endswith("_en"):
        name = name[:-3]
    for bn in sorted(known_basenames, key=len, reverse=True):
        if name == bn or name.endswith(f"_{bn}"):
            return bn
    return None


def collect_translatable_strings(
    docs: list[dict[str, Any]], basename: str
) -> list[str]:
    extract_fn, _ = FIELD_HANDLERS[basename]
    out: list[str] = []
    for doc in docs:
        out.extend(extract_fn(doc))
    return out


def apply_translations(
    docs: list[dict[str, Any]], basename: str, mapping: dict[str, str]
) -> list[dict[str, Any]]:
    _, apply_fn = FIELD_HANDLERS[basename]
    return [apply_fn(doc, mapping) for doc in docs]
