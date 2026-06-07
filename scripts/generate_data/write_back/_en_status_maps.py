"""英文睡眠报告写回：文件名后缀与睡眠结构/环境 status 中文→英文（单词）映射。"""

from __future__ import annotations

SUFFIX_MORNING_ALARM_INSIGHT_EN = "_morning_alarm_insight_en.json"
SUFFIX_AI_ANALYSIS_14D_EN = "_ai_analysis_14d_en.json"
SUFFIX_SLEEP_PATTERN_COMMONALITY_EN = "_sleep_pattern_commonality_en.json"
SUFFIX_SLEEP_PATTERN_COMMONALITY_INSIGHT_EN = "_sleep_pattern_commonality_insight_en.json"
SUFFIX_SLEEP_REPORT_EN = "_sleep_report_en.json"

SLEEP_STRUCTURE_STAGE_KEYS = ("awake", "rem_sleep", "light_sleep", "deep_sleep")
ENVIRONMENT_SUMMARY_DIM_KEYS = ("temperature", "humidity", "illuminance", "noise")

# 仅睡眠结构与环境 status；主标题、身体电量不在此表。
STATUS_ZH_TO_EN: dict[str, str] = {
    "正常": "Normal",
    "过高": "High",
    "过低": "Low",
    "最佳": "Best",
    "良好": "Good",
    "偏冷": "Cold",
    "偏热": "Hot",
    "干燥": "Dry",
    "潮湿": "Humid",
    "偏亮": "Bright",
    "过亮": "Glaring",
    "偏嘈杂": "Noisy",
    "过载": "Loud",
}


def map_status_to_en(raw: object) -> tuple[str, bool]:
    """若 raw 为已知中文 status 则返回 (英文, True)；否则 (原字符串, False)。"""
    if raw is None:
        return "", False
    text = str(raw).strip()
    if not text:
        return text, False
    mapped = STATUS_ZH_TO_EN.get(text)
    if mapped is None:
        return text, False
    return mapped, mapped != text
