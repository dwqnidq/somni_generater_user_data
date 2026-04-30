"""
睡眠人格画像参数配置模块

基于三维人格编码体系（作息类型/敏感度/大脑活跃度），
为每种人格编码定义精确的睡眠、生理、环境参数范围，
驱动数据生成更符合人格背景。
"""
# 文件作用：用于 personality profile 相关的数据处理或流程支持。


import random


def parse_personality_code(personality_type):
    """
    解析人格编码为三个维度
    
    Args:
        personality_type: 人格编码字符串，如 "M-H-R"
        
    Returns:
        dict: {
            'chronotype': 'M' or 'E',      # 作息类型
            'sensitivity': 'H' or 'L',      # 敏感度
            'brain_activity': 'R' or 'C'    # 大脑活跃度
        }
    """
    parts = personality_type.split('-')
    if len(parts) != 3:
        return {'chronotype': 'M', 'sensitivity': 'L', 'brain_activity': 'C'}
    return {
        'chronotype': parts[0],
        'sensitivity': parts[1],
        'brain_activity': parts[2]
    }


# ============================================================
# 八种人格的完整参数配置
# ============================================================

PERSONALITY_PROFILES = {
    "M-H-R": {
        "label": "完美主义百灵鸟",
        "description": "作息规律但入睡困难，高敏感+高活跃导致睡前思绪纷飞",
        "total_sleep_hours": {"min": 5.5, "max": 7.0},
        "sleep_latency": {"min": 20, "max": 35},   # new.md: 20-35
        "sleep_efficiency": {"min": 78, "max": 86}, # new.md: 78-86
        "night_awakenings": {"min": 1, "max": 2},   # new.md: 1-2
        "night_wake_total_minutes": {"min": 22, "max": 34},  # WASO，new.md；>30 为异常
        "turnover_count": {"min": 35, "max": 55},
        "apnea_count": {"min": 0, "max": 3},
        "avg_heartbeat": {"min": 60, "max": 78},
        "avg_respiration": {"min": 14, "max": 18},
        "leave_bed_count": {"min": 0, "max": 2},
        "leave_bed_duration": {"min": 5, "max": 12},
        "wake_after_sleep": {"min": 10, "max": 25},
        "noise_tolerance": {"min": 20, "max": 35},
        "hrv_adjustment": -10,
        "fatigue_index": {"min": 6, "max": 9},
        "stage_pattern": "sensitive_active",
    },

    "M-H-C": {
        "label": "敏感的晨间鹿",
        "description": "早睡型，大脑容易安静，但感官高度敏感，夜间易被惊醒",
        "total_sleep_hours": {"min": 5.5, "max": 7.5},
        "sleep_latency": {"min": 8, "max": 18},     # new.md: 8-18
        "sleep_efficiency": {"min": 82, "max": 90}, # new.md: 82-90
        "night_awakenings": {"min": 1, "max": 3},
        "night_wake_total_minutes": {"min": 14, "max": 28},
        "turnover_count": {"min": 30, "max": 50},
        "apnea_count": {"min": 0, "max": 3},
        "avg_heartbeat": {"min": 58, "max": 75},
        "avg_respiration": {"min": 14, "max": 18},
        "leave_bed_count": {"min": 0, "max": 2},
        "leave_bed_duration": {"min": 5, "max": 10},
        "wake_after_sleep": {"min": 10, "max": 20},
        "noise_tolerance": {"min": 20, "max": 32},
        "hrv_adjustment": -5,
        "fatigue_index": {"min": 7, "max": 10},
        "stage_pattern": "sensitive_calm",
    },

    "M-L-R": {
        "label": "效率至上考拉",
        "description": "作息规律，环境适应力强，但睡前大脑容易过热",
        "total_sleep_hours": {"min": 6.5, "max": 8.0},
        "sleep_latency": {"min": 15, "max": 28},    # new.md: 15-28
        "sleep_efficiency": {"min": 87, "max": 93}, # new.md: 87-93
        "night_awakenings": {"min": 0, "max": 1},
        "night_wake_total_minutes": {"min": 3, "max": 14},
        "turnover_count": {"min": 20, "max": 40},
        "apnea_count": {"min": 0, "max": 2},
        "avg_heartbeat": {"min": 58, "max": 72},
        "avg_respiration": {"min": 13, "max": 17},
        "leave_bed_count": {"min": 0, "max": 1},
        "leave_bed_duration": {"min": 5, "max": 10},
        "wake_after_sleep": {"min": 8, "max": 18},
        "noise_tolerance": {"min": 25, "max": 45},
        "hrv_adjustment": 5,
        "fatigue_index": {"min": 3, "max": 6},
        "stage_pattern": "healthy_active",
    },

    "M-L-C": {
        "label": "阳光漫步者",
        "description": "睡眠质量最优，倒头就睡，一觉到天亮",
        "total_sleep_hours": {"min": 7.0, "max": 8.0},
        "sleep_latency": {"min": 5, "max": 15},
        "sleep_efficiency": {"min": 91, "max": 96}, # new.md: 91-96
        "night_awakenings": {"min": 0, "max": 1},
        "night_wake_total_minutes": {"min": 0, "max": 10},
        "turnover_count": {"min": 15, "max": 30},
        "apnea_count": {"min": 0, "max": 1},
        "avg_heartbeat": {"min": 55, "max": 68},
        "avg_respiration": {"min": 12, "max": 16},
        "leave_bed_count": {"min": 0, "max": 1},
        "leave_bed_duration": {"min": 5, "max": 8},
        "wake_after_sleep": {"min": 5, "max": 15},
        "noise_tolerance": {"min": 28, "max": 50},
        "hrv_adjustment": 15,
        "fatigue_index": {"min": 3, "max": 5},
        "stage_pattern": "optimal",
    },

    "E-H-R": {
        "label": "深夜灵感守望者",
        "description": "睡眠质量最差，夜间大脑极度活跃且感官高度敏感",
        "total_sleep_hours": {"min": 5.0, "max": 6.5},
        "sleep_latency": {"min": 25, "max": 40},    # new.md: 25-40
        "sleep_efficiency": {"min": 74, "max": 83}, # new.md: 74-83
        "night_awakenings": {"min": 2, "max": 3},   # new.md: 2-3
        "night_wake_total_minutes": {"min": 48, "max": 78},
        "turnover_count": {"min": 40, "max": 65},
        "apnea_count": {"min": 1, "max": 5},
        "avg_heartbeat": {"min": 62, "max": 82},
        "avg_respiration": {"min": 15, "max": 20},
        "leave_bed_count": {"min": 1, "max": 3},
        "leave_bed_duration": {"min": 8, "max": 15},
        "wake_after_sleep": {"min": 15, "max": 30},
        "noise_tolerance": {"min": 20, "max": 33},
        "hrv_adjustment": -15,
        "fatigue_index": {"min": 8, "max": 10},
        "stage_pattern": "poor_quality",
    },

    "E-H-C": {
        "label": "深海独奏家",
        "description": "作息偏晚，大脑容易安静但感官极度敏感，夜间易被惊醒",
        "total_sleep_hours": {"min": 5.0, "max": 7.0},
        "sleep_latency": {"min": 10, "max": 22},    # new.md: 10-22
        "sleep_efficiency": {"min": 80, "max": 89}, # new.md: 80-89
        "night_awakenings": {"min": 1, "max": 3},   # new.md: 1-3
        "night_wake_total_minutes": {"min": 14, "max": 32},
        "turnover_count": {"min": 30, "max": 50},
        "apnea_count": {"min": 0, "max": 3},
        "avg_heartbeat": {"min": 60, "max": 78},
        "avg_respiration": {"min": 14, "max": 19},
        "leave_bed_count": {"min": 0, "max": 2},
        "leave_bed_duration": {"min": 5, "max": 12},
        "wake_after_sleep": {"min": 12, "max": 22},
        "noise_tolerance": {"min": 20, "max": 33},
        "hrv_adjustment": -8,
        "fatigue_index": {"min": 7, "max": 10},
        "stage_pattern": "sensitive_calm",
    },

    "E-L-R": {
        "label": "创意夜猫子",
        "description": "典型夜猫子，越夜越精神，但入睡后睡眠质量尚可",
        "total_sleep_hours": {"min": 6.0, "max": 7.5},
        "sleep_latency": {"min": 18, "max": 30},    # new.md: 18-30
        "sleep_efficiency": {"min": 84, "max": 91}, # new.md: 84-91
        "night_awakenings": {"min": 0, "max": 1},   # new.md: 0-1
        "night_wake_total_minutes": {"min": 4, "max": 16},
        "turnover_count": {"min": 25, "max": 45},
        "apnea_count": {"min": 0, "max": 3},
        "avg_heartbeat": {"min": 58, "max": 75},
        "avg_respiration": {"min": 13, "max": 18},
        "leave_bed_count": {"min": 0, "max": 1},
        "leave_bed_duration": {"min": 5, "max": 10},
        "wake_after_sleep": {"min": 10, "max": 20},
        "noise_tolerance": {"min": 25, "max": 48},
        "hrv_adjustment": 0,
        "fatigue_index": {"min": 4, "max": 7},
        "stage_pattern": "healthy_active",
    },

    "E-L-C": {
        "label": "月光冲浪者",
        "description": "主动选择晚睡，入睡迅速，睡眠质量高",
        "total_sleep_hours": {"min": 6.5, "max": 8.0},
        "sleep_latency": {"min": 5, "max": 15},     # new.md: 5-15
        "sleep_efficiency": {"min": 89, "max": 95}, # new.md: 89-95
        "night_awakenings": {"min": 0, "max": 1},
        "night_wake_total_minutes": {"min": 2, "max": 12},
        "turnover_count": {"min": 18, "max": 35},
        "apnea_count": {"min": 0, "max": 2},
        "avg_heartbeat": {"min": 55, "max": 70},
        "avg_respiration": {"min": 12, "max": 17},
        "leave_bed_count": {"min": 0, "max": 1},
        "leave_bed_duration": {"min": 5, "max": 8},
        "wake_after_sleep": {"min": 5, "max": 15},
        "noise_tolerance": {"min": 28, "max": 50},
        "hrv_adjustment": 10,
        "fatigue_index": {"min": 4, "max": 7},
        "stage_pattern": "optimal",
    },
}


def get_personality_profile(personality_type):
    """
    根据人格编码获取完整的参数配置
    
    Args:
        personality_type: 人格编码字符串，如 "M-H-R"
        
    Returns:
        dict: 该人格的完整参数配置，如果未找到则返回默认配置(M-L-C)
    """
    return PERSONALITY_PROFILES.get(personality_type, PERSONALITY_PROFILES["M-L-C"])


def get_sleep_latency(personality_type):
    """根据人格编码获取入睡潜伏期范围"""
    profile = get_personality_profile(personality_type)
    latency_range = profile["sleep_latency"]
    return random.randint(latency_range["min"], latency_range["max"])


def get_total_sleep_minutes(personality_type):
    """根据人格编码获取总净睡眠时长（分钟），不含清醒时间"""
    profile = get_personality_profile(personality_type)
    r = profile["total_sleep_hours"]
    hours = random.uniform(r["min"], r["max"])
    return int(hours * 60)


def get_turnover_count(personality_type):
    """根据人格编码获取翻身次数"""
    profile = get_personality_profile(personality_type)
    r = profile["turnover_count"]
    return random.randint(r["min"], r["max"])


def get_apnea_count(personality_type):
    """根据人格编码获取呼吸暂停次数"""
    profile = get_personality_profile(personality_type)
    r = profile["apnea_count"]
    return random.randint(r["min"], r["max"])


def get_avg_heartbeat(personality_type):
    """根据人格编码获取平均心率"""
    profile = get_personality_profile(personality_type)
    r = profile["avg_heartbeat"]
    return random.randint(r["min"], r["max"])


def get_avg_respiration(personality_type):
    """根据人格编码获取平均呼吸频率"""
    profile = get_personality_profile(personality_type)
    r = profile["avg_respiration"]
    return random.randint(r["min"], r["max"])


def get_leave_bed_info(personality_type):
    """根据人格编码获取离床信息"""
    profile = get_personality_profile(personality_type)
    count = random.randint(
        profile["leave_bed_count"]["min"],
        profile["leave_bed_count"]["max"]
    )
    duration = count * random.randint(
        profile["leave_bed_duration"]["min"],
        profile["leave_bed_duration"]["max"]
    ) if count > 0 else 0
    return count, duration


def get_wake_after_sleep(personality_type):
    """根据人格编码获取觉后清醒时间"""
    profile = get_personality_profile(personality_type)
    r = profile["wake_after_sleep"]
    return random.randint(r["min"], r["max"])


def get_noise_range(personality_type):
    """根据人格编码获取噪音容忍范围（用于环境数据生成）"""
    profile = get_personality_profile(personality_type)
    return profile["noise_tolerance"]


def get_hrv_adjustment(personality_type):
    """根据人格编码获取HRV调整因子"""
    profile = get_personality_profile(personality_type)
    return profile["hrv_adjustment"]


def get_fatigue_index(personality_type):
    """根据人格编码获取疲劳指数"""
    profile = get_personality_profile(personality_type)
    r = profile["fatigue_index"]
    return random.randint(r["min"], r["max"])


def get_stage_pattern(personality_type):
    """根据人格编码获取睡眠阶段模式"""
    profile = get_personality_profile(personality_type)
    return profile["stage_pattern"]


def calculate_sleep_score_by_personality(
    personality_type,
    total_sleep_minutes,
    deep_sleep_ratio,
    sleep_latency,
    awake_ratio,
    sleep_efficiency=None,
    apnea_count=None,
    rem_ratio=None,
):
    """
    根据人格编码和睡眠指标计算睡眠分数（0-100）。

    设计目标：
    - 分数梯度更细，避免大量数据扎堆在同一分段
    - 兼容人格差异（H/R 人格不过度“吃亏”）
    - 既看结构（深睡/REM），也看连续性（入睡/清醒/效率）与风险（呼吸暂停）
    """
    dims = parse_personality_code(personality_type)

    def clamp(v, lo=0.0, hi=100.0):
        return max(lo, min(hi, v))

    # ---- 1) 时长分（权重 30）----
    # 以 450 分钟为理想值，偏离越大扣分；极短/极长快速衰减
    duration_score = 100.0 - abs(float(total_sleep_minutes) - 450.0) * 0.22
    if total_sleep_minutes < 300:
        duration_score -= (300 - float(total_sleep_minutes)) * 0.12
    if total_sleep_minutes > 600:
        duration_score -= (float(total_sleep_minutes) - 600) * 0.10
    duration_score = clamp(duration_score)

    # ---- 2) 深睡结构分（权重 25）----
    # 18%-24%最优；过低扣分，过高视为补偿性深睡/异常轻惩罚
    ds = float(deep_sleep_ratio)
    if 18.0 <= ds <= 24.0:
        deep_score = 100.0
    elif ds < 18.0:
        deep_score = 100.0 - (18.0 - ds) * 4.2
    else:
        deep_score = 100.0 - (ds - 24.0) * 2.8
        if ds > 35.0:
            deep_score -= (ds - 35.0) * 1.2
    deep_score = clamp(deep_score)

    # ---- 3) 入睡与连续性分（权重 25）----
    # latency 越短越好，awake_ratio 越低越好
    lat = float(sleep_latency)
    awk = float(awake_ratio)
    latency_score = clamp(100.0 - max(0.0, lat - 8.0) * 2.6)
    awake_score = clamp(100.0 - max(0.0, awk - 6.0) * 3.4)
    continuity_score = clamp(latency_score * 0.45 + awake_score * 0.55)

    # ---- 4) 质量修正分（权重 20）----
    # 优先用效率；若有 REM 和 apnea 则增强判别力
    if sleep_efficiency is None:
        eff_score = 80.0
    else:
        eff = float(sleep_efficiency)
        eff_score = clamp(100.0 - max(0.0, 90.0 - eff) * 3.2)

    rem_bonus = 0.0
    if rem_ratio is not None:
        rr = float(rem_ratio)
        if 18.0 <= rr <= 27.0:
            rem_bonus = 4.0
        elif rr < 14.0 or rr > 32.0:
            rem_bonus = -5.0

    apnea_penalty = 0.0
    if apnea_count is not None:
        ap = float(apnea_count)
        apnea_penalty = min(18.0, ap * 2.2)

    quality_score = clamp(eff_score + rem_bonus - apnea_penalty)

    # ---- 人格校准（防止天然劣势人格长期偏低）----
    personality_bias = 0.0
    if dims['sensitivity'] == 'H':
        personality_bias += 2.0
    if dims['brain_activity'] == 'R':
        personality_bias += 1.5
    if dims['chronotype'] == 'E':
        personality_bias += 0.8
    if dims['sensitivity'] == 'L' and dims['brain_activity'] == 'C':
        personality_bias -= 1.0

    score = (
        duration_score * 0.30 +
        deep_score * 0.25 +
        continuity_score * 0.25 +
        quality_score * 0.20 +
        personality_bias
    )

    return int(round(clamp(score)))
