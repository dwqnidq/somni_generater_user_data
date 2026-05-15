"""身体电量生成。"""

from __future__ import annotations

from .sleep_score import sleep_report_score_from_sleep_data


def generate_body_battery(sleep_data, personality_type="M-L-C", output_dir=None):
    """睡眠报告展示「身体电量」（0–100）：仅由净睡时长与深/浅/REM 占净睡比例四项规则子分平均。"""
    _ = personality_type
    _ = output_dir
    return sleep_report_score_from_sleep_data(sleep_data)


def get_body_battery_status(body_battery):
    """根据身体电量值返回状态描述"""
    if body_battery >= 95:
        return "身体电量已充满"
    elif body_battery >= 85:
        return "能量高度充沛"
    elif body_battery >= 75:
        return "电量储备充足"
    elif body_battery >= 65:
        return "正在进入蓄能态"
    else:
        return "能量正在温和回升"
