"""14 天趋势 AI 分析：prompt payload 构建与 schedule_insight 数值校验。"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

_SCHEDULE_EMOTION_SCORE_RE = re.compile(
    r"情绪压力(?:指数)?(?:达|达到|推至)?(\d+)|情绪压力推至(\d+)"
)
_DEFAULT_SCHEDULE_VALIDATION_RETRIES = 2


def extract_emotion_score_from_schedule_insight(text: str) -> Optional[int]:
    if not text:
        return None
    match = _SCHEDULE_EMOTION_SCORE_RE.search(str(text))
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    return int(raw) if raw is not None else None


def schedule_insight_uses_today_health(schedule_insight: str, today_health: dict) -> bool:
    expected_score = today_health.get("emotion_score")
    expected_steps = today_health.get("steps")
    if expected_score is None or expected_steps is None:
        return True
    got_score = extract_emotion_score_from_schedule_insight(schedule_insight)
    return got_score == expected_score and str(expected_steps) in str(schedule_insight or "")


def schedule_validation_retries() -> int:
    raw = os.getenv("AI_ANALYSIS_14D_SCHEDULE_VALIDATION_RETRIES", "")
    if raw.strip().isdigit():
        return max(0, int(raw.strip()))
    return _DEFAULT_SCHEDULE_VALIDATION_RETRIES


def build_trend_14d_payload(
    record_date_str: str,
    period_start: str,
    sleep_records_14d: list,
    schedule_records_14d: list,
    today_health: Optional[dict] = None,
    today_weather: Optional[dict] = None,
) -> dict:
    payload = {
        "anchor_record_date": record_date_str,
        "analysis_period": {"start": period_start, "end": record_date_str},
        "sleep_records_14d": sleep_records_14d,
        "schedule_records_14d": schedule_records_14d,
    }
    if today_health:
        payload["today_health"] = today_health
    if today_weather:
        payload["today_weather"] = today_weather
    return payload


def build_trend_14d_user_prompt(payload: dict, *, strict_health: bool = False) -> str:
    prefix = (
        "以下为真实输入数据（JSON）。请仅依据这些数据输出 JSON 对象，"
        "字段必须为 `title`、`sleep_insight`、`schedule_insight`，不要附加解释文本。"
        "不要照抄文档中的示例数值与文案。"
    )
    if strict_health and payload.get("today_health"):
        health = payload["today_health"]
        prefix += (
            f" schedule_insight 必须严格使用 today_health.emotion_score="
            f"{health.get('emotion_score')} 与 today_health.steps={health.get('steps')} 的精确整数，"
            "不得改用其他数字。"
        )
    return prefix + "\n\n" + json.dumps(payload, ensure_ascii=False)
