"""
为每个用户人格生成天气数据和路况数据，存放至 output/ 目录。
- {uid}_weather_data.json  : 单条天气快照（深圳市，固定日期 2026-03-15）
- {uid}_traffic_data.json  : 单条通勤路况快照（北京市路线；根据晨型/夜型人格差异化）
"""

from __future__ import annotations

import json
import os
import random
from datetime import date

# ── 路径配置 ─────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.join(ROOT, "output")

# ── 用户人格映射 ──────────────────────────────────────────────────────────────
PERSONAS = [
    {"uid": "69aea593af5e6cbf08027964", "code": "M-H-R", "name": "完美主义百灵鸟",
     "chronotype": "M", "sensitivity": "H", "brain_activity": "R"},
    {"uid": "69aea63eaf5e6cbf08027965", "code": "M-H-C", "name": "敏感晨间鹿",
     "chronotype": "M", "sensitivity": "H", "brain_activity": "C"},
    {"uid": "69aea6d8af5e6cbf08027966", "code": "M-L-R", "name": "效率考拉",
     "chronotype": "M", "sensitivity": "L", "brain_activity": "R"},
    {"uid": "69aea6e3af5e6cbf08027967", "code": "M-L-C", "name": "阳光漫步者",
     "chronotype": "M", "sensitivity": "L", "brain_activity": "C"},
    {"uid": "69aea6e8af5e6cbf08027968", "code": "E-H-R", "name": "创意夜猫子",
     "chronotype": "E", "sensitivity": "H", "brain_activity": "R"},
    {"uid": "69aea6eeaf5e6cbf08027969", "code": "E-H-C", "name": "敏感都市夜行者",
     "chronotype": "E", "sensitivity": "H", "brain_activity": "C"},
    {"uid": "69aea6f3af5e6cbf0802796a", "code": "E-L-R", "name": "夜间猎手",
     "chronotype": "E", "sensitivity": "L", "brain_activity": "R"},
    {"uid": "69aea6f8af5e6cbf0802796b", "code": "E-L-C", "name": "随性漫游者",
     "chronotype": "E", "sensitivity": "L", "brain_activity": "C"},
]

# ── 快照日期 ──────────────────────────────────────────────────────────────────
START_DATE = date(2026, 3, 1)
SNAPSHOT_DATE = date(2026, 3, 15)

# ── 深圳3月天气基础参数（模拟数据）────────────────────────────────────────────
# 深圳3月：春季多云多雨，气温15-26°C，湿度大
SHENZHEN_WEATHER_BASE = [
    # (weather_condition, weather_code, precipitation_mm, uv_index)
    ("多云",     "cloudy",         0,    4),
    ("晴",       "clear",          0,    7),
    ("阴",       "overcast",       0,    2),
    ("小雨",     "light_rain",     5,    1),
    ("中雨",     "moderate_rain",  18,   1),
    ("阵雨",     "shower",         8,    2),
    ("多云转晴", "cloudy_to_clear", 0,   5),
    ("晴转多云", "clear_to_cloudy", 0,   6),
    ("小到中雨", "light_to_moderate_rain", 12, 1),
]

# 按日期确定性地选择天气（使用日期作为随机种子）
WEATHER_PATTERN = [
    0, 1, 2, 3, 0, 0, 5, 1, 0, 4,  # 1-10
    2, 0, 1, 3, 0, 5, 0, 1, 2, 8,  # 11-20
    0, 1, 6, 0, 3, 1, 0, 5, 7, 4,  # 21-30
    0,                               # 31
]

WIND_DIRECTIONS = ["东南风", "南风", "偏南风", "东风", "偏东风", "北风", "西南风"]
AQI_LEVELS = [
    (0,  50,  "优",   "空气质量优，非常适合户外活动"),
    (51, 100, "良",   "空气质量良好，适合户外活动"),
    (101,150, "轻度污染", "敏感人群应减少户外活动"),
    (151,200, "中度污染", "建议减少户外活动"),
]

# 深圳3月日出日落（近似）
SUNRISE_SUNSET = {
    3: ("06:38", "18:29"),
    4: ("06:20", "18:38"),
}


def get_weather_for_date(d: date) -> dict:
    """返回某日深圳天气（确定性模拟）"""
    day_idx = (d - START_DATE).days
    rng = random.Random(20260300 + day_idx)

    pattern_idx = WEATHER_PATTERN[day_idx % len(WEATHER_PATTERN)]
    wc, wcode, base_precip, base_uv = SHENZHEN_WEATHER_BASE[pattern_idx]

    temp_high = rng.randint(18, 27)
    temp_low  = temp_high - rng.randint(5, 9)
    temp_feel = temp_high - rng.randint(1, 3)
    humidity  = rng.randint(62, 88)
    wind_spd  = rng.randint(8, 22)
    wind_dir  = rng.choice(WIND_DIRECTIONS)
    wind_lvl  = max(1, min(5, wind_spd // 6))
    precip    = base_precip + rng.randint(0, 4) if base_precip > 0 else 0
    uv        = max(1, base_uv + rng.randint(-1, 1))
    visibility = rng.randint(6, 20) if base_precip > 0 else rng.randint(12, 25)

    aqi = rng.randint(20, 130)
    for low, high, level, desc in AQI_LEVELS:
        if low <= aqi <= high:
            aqi_level = level
            aqi_desc  = desc
            break
    else:
        aqi_level, aqi_desc = "良", "空气质量良好，适合户外活动"

    pm25 = int(aqi * 0.55 + rng.randint(-5, 5))

    month = d.month
    sunrise, sunset = SUNRISE_SUNSET.get(month, ("06:30", "18:35"))

    weather_descs = {
        "clear":        "全天晴好，阳光充足，气温舒适",
        "cloudy":       "多云天气，气温温和，偶有云层遮阳",
        "overcast":     "阴天，气温偏凉，适合室内活动",
        "light_rain":   "小雨，出行请携带雨具，路面湿滑注意安全",
        "moderate_rain":"中雨，建议减少外出，注意行车安全",
        "shower":       "阵雨，雨势时大时小，外出需备好雨具",
        "cloudy_to_clear": "上午多云，下午逐渐放晴",
        "clear_to_cloudy": "早晨晴好，午后多云转阴",
        "light_to_moderate_rain": "小到中雨，雨势较大，注意防雨",
    }

    return {
        "date": d.isoformat(),
        "city": "深圳市",
        "district": "南山区",
        "weather_condition": wc,
        "weather_code": wcode,
        "temperature_high_c": temp_high,
        "temperature_low_c": temp_low,
        "temperature_feel_c": temp_feel,
        "humidity_percent": humidity,
        "wind_speed_kmh": wind_spd,
        "wind_direction": wind_dir,
        "wind_level": wind_lvl,
        "air_quality_index": aqi,
        "aqi_level": aqi_level,
        "aqi_description": aqi_desc,
        "pm25_ugm3": pm25,
        "uv_index": uv,
        "uv_level": ["低", "低", "中等", "中等", "中等", "高", "高", "很高", "极高", "极高"][min(uv - 1, 9)],
        "sunrise_time": sunrise,
        "sunset_time": sunset,
        "precipitation_mm": precip,
        "visibility_km": visibility,
        "weather_description": weather_descs.get(wcode, ""),
    }


def get_beijing_weather_for_date(d: date) -> dict:
    """北京 3 月天气（确定性模拟），仅用于路况生成中与降水相关的拥堵权重。"""
    day_idx = (d - START_DATE).days
    rng = random.Random(20260310 + day_idx)

    pattern_idx = WEATHER_PATTERN[day_idx % len(WEATHER_PATTERN)]
    wc, wcode, base_precip, base_uv = SHENZHEN_WEATHER_BASE[pattern_idx]

    temp_high = rng.randint(8, 18)
    temp_low = temp_high - rng.randint(4, 8)
    temp_feel = temp_high - rng.randint(0, 3)
    humidity = rng.randint(35, 65)
    wind_spd = rng.randint(10, 28)
    wind_dirs_bj = ["北风", "西北风", "偏北风", "东北风", "南风", "西南风"]
    wind_dir = rng.choice(wind_dirs_bj)
    wind_lvl = max(1, min(5, wind_spd // 6))
    precip = base_precip + rng.randint(0, 3) if base_precip > 0 else 0
    uv = max(1, min(6, base_uv + rng.randint(0, 1)))
    visibility = rng.randint(5, 15) if base_precip > 0 else rng.randint(10, 25)

    aqi = rng.randint(45, 160)
    for low, high, level, desc in AQI_LEVELS:
        if low <= aqi <= high:
            aqi_level = level
            aqi_desc = desc
            break
    else:
        aqi_level, aqi_desc = "良", "空气质量良好，适合户外活动"

    pm25 = int(aqi * 0.65 + rng.randint(-8, 8))

    month = d.month
    sunrise, sunset = ("06:22", "18:12") if month == 3 else ("06:05", "18:25")

    weather_descs = {
        "clear": "全天晴好，气温仍偏凉，注意防风保暖",
        "cloudy": "多云，春季北风时强时弱",
        "overcast": "阴天，体感偏冷",
        "light_rain": "小雨，路面湿滑，环路车流易放缓",
        "moderate_rain": "中雨，能见度下降，建议预留通勤时间",
        "shower": "阵雨，雨势变化快",
        "cloudy_to_clear": "上午云系较多，午后逐渐放晴",
        "clear_to_cloudy": "早晨晴，午后云量增多",
        "light_to_moderate_rain": "小到中雨，对晚高峰影响更明显",
    }

    return {
        "date": d.isoformat(),
        "city": "北京市",
        "district": "朝阳区",
        "weather_condition": wc,
        "weather_code": wcode,
        "temperature_high_c": temp_high,
        "temperature_low_c": temp_low,
        "temperature_feel_c": temp_feel,
        "humidity_percent": humidity,
        "wind_speed_kmh": wind_spd,
        "wind_direction": wind_dir,
        "wind_level": wind_lvl,
        "air_quality_index": aqi,
        "aqi_level": aqi_level,
        "aqi_description": aqi_desc,
        "pm25_ugm3": pm25,
        "uv_index": uv,
        "uv_level": ["低", "低", "中等", "中等", "中等", "高", "高", "很高", "极高", "极高"][min(uv - 1, 9)],
        "sunrise_time": sunrise,
        "sunset_time": sunset,
        "precipitation_mm": precip,
        "visibility_km": visibility,
        "weather_description": weather_descs.get(wcode, ""),
    }


# ── 路况参数配置 ───────────────────────────────────────────────────────────────
CONGESTION_LEVELS = [
    ("畅通",   (0,  20)),
    ("缓行",   (21, 45)),
    ("拥堵",   (46, 65)),
    ("严重拥堵", (66, 90)),
]

# 晨型通勤路线（北京市，早高峰 07:30-09:30）
MORNING_ROUTES = [
    {"name": "通州→国贸（京通快速→东三环）", "distance_km": 22.0, "planned_min": 48, "lights": 14},
    {"name": "回龙观→中关村（G6→北四环）", "distance_km": 18.5, "planned_min": 42, "lights": 11},
    {"name": "望京→金融街（机场高速→二环）", "distance_km": 19.2, "planned_min": 52, "lights": 18},
]

# 夜型通勤路线（北京市，晚高峰或弹性时段）
EVENING_TYPE_ROUTES = [
    {"name": "国贸→通州（东三环→京通快速）", "distance_km": 22.0, "planned_min": 48, "lights": 14},
    {"name": "中关村→回龙观（北四环→G6）", "distance_km": 18.5, "planned_min": 42, "lights": 11},
    {"name": "亦庄→西二旗（京沪→五环→G6辅路）", "distance_km": 35.0, "planned_min": 65, "lights": 22},
]

INCIDENT_TYPES = [
    "无", "无", "无",
    "轻微追尾",
    "车辆故障占道",
    "施工占道",
    "信号灯故障",
    "主路入口排队",
]


def get_congestion(rng: random.Random, peak: bool, rain: bool) -> tuple[str, int, int]:
    """返回 (拥堵等级, 拥堵率, 延误分钟)。

    默认偏「恶劣路况」采样：高峰期显著抬高拥堵/严重拥堵权重，延误区间整体拉长；
    非高峰仍可出现缓行及以上，雨天再向高档位偏移。
    """
    # weights 顺序：畅通 / 缓行 / 拥堵 / 严重拥堵（整体偏恶劣；非高峰仍少见「一路畅通」）
    weights = [1, 2, 4, 4] if not peak else [1, 2, 4, 5]
    if rain:
        weights = [max(1, w - 1) if i < 2 else w + 2 for i, w in enumerate(weights)]

    level_name, (low, high) = random.choices(CONGESTION_LEVELS, weights=weights, k=1)[0]
    # 拥堵档越高，拥堵率越倾向区间上沿
    bias_high = 0.25 if level_name in ("畅通", "缓行") else 0.55
    mid = (low + high) // 2
    if rng.random() < bias_high:
        ratio = rng.randint(mid, high)
    else:
        ratio = rng.randint(low, high)

    delay = 0
    if level_name == "缓行":
        delay = rng.randint(10, 24)
    elif level_name == "拥堵":
        delay = rng.randint(22, 48)
    elif level_name == "严重拥堵":
        delay = rng.randint(45, 85)
    return level_name, ratio, delay


def make_commute_record(
    rng: random.Random,
    commute_type: str,
    departure_hour: int,
    departure_min: int,
    route: dict,
    is_peak: bool,
    has_rain: bool,
    weather_impact: str,
) -> dict:
    congestion, ratio, delay = get_congestion(rng, is_peak, has_rain)
    # 恶劣路况下实际耗时在延误之上再叠一层波动，且略偏长
    actual_min = route["planned_min"] + delay + rng.randint(0, 12)

    arr_total_min = departure_hour * 60 + departure_min + actual_min
    arr_hour, arr_min = divmod(arr_total_min, 60)
    arr_hour %= 24

    incident_chance = rng.random()
    incident = rng.choice(INCIDENT_TYPES) if incident_chance < 0.38 else "无"

    return {
        "commute_type": commute_type,
        "departure_time": f"{departure_hour:02d}:{departure_min:02d}",
        "arrival_time": f"{arr_hour:02d}:{arr_min:02d}",
        "route_name": route["name"],
        "distance_km": route["distance_km"],
        "duration_planned_min": route["planned_min"],
        "duration_actual_min": actual_min,
        "congestion_level": congestion,
        "congestion_ratio_percent": ratio,
        "traffic_lights_count": route["lights"],
        "road_incident": incident,
        "weather_impact": weather_impact,
        "delay_minutes": delay,
    }


def generate_traffic_for_persona(persona: dict) -> dict:
    uid         = persona["uid"]
    chronotype  = persona["chronotype"]
    sensitivity = persona["sensitivity"]

    d = SNAPSHOT_DATE
    day_idx = (d - START_DATE).days
    rng = random.Random(int(uid[-4:], 16) * 10000 + day_idx)

    # 按人格固定路线
    rng_route = random.Random(int(uid[-4:], 16))
    if chronotype == "M":
        route = rng_route.choice(MORNING_ROUTES)
    else:
        route = rng_route.choice(EVENING_TYPE_ROUTES)

    weather_bj = get_beijing_weather_for_date(d)
    has_rain = weather_bj["precipitation_mm"] > 0

    if has_rain and sensitivity == "H":
        w_impact = rng.choice(["轻度影响", "中度影响"])
    elif has_rain:
        w_impact = rng.choice(["正常", "轻度影响"])
    else:
        w_impact = "正常"

    is_workday = d.weekday() < 5

    commute_records = []
    if chronotype == "M":
        dep_h = rng.randint(6, 8) if is_workday else rng.randint(8, 10)
        dep_m = rng.randint(0, 59)
        morning_peak = is_workday and dep_h in (7, 8)
        commute_records.append(make_commute_record(
            rng, "上班", dep_h, dep_m, route,
            is_peak=morning_peak, has_rain=has_rain, weather_impact=w_impact
        ))
        ret_h = rng.randint(17, 18) if is_workday else rng.randint(16, 19)
        ret_m = rng.randint(0, 59)
        evening_peak = is_workday and ret_h == 18
        commute_records.append(make_commute_record(
            rng, "下班", ret_h, ret_m, route,
            is_peak=evening_peak, has_rain=has_rain, weather_impact=w_impact
        ))
    else:
        dep_h = rng.randint(8, 10) if is_workday else rng.randint(9, 11)
        dep_m = rng.randint(0, 59)
        commute_records.append(make_commute_record(
            rng, "上班", dep_h, dep_m, route,
            is_peak=is_workday and dep_h <= 9, has_rain=has_rain, weather_impact=w_impact
        ))
        ret_h = rng.randint(18, 21) if is_workday else rng.randint(17, 20)
        ret_m = rng.randint(0, 59)
        commute_records.append(make_commute_record(
            rng, "下班", ret_h, ret_m, route,
            is_peak=is_workday and ret_h >= 18, has_rain=has_rain, weather_impact=w_impact
        ))

    return {
        "uid": uid,
        "city": "北京市",
        "date": d.isoformat(),
        "is_workday": is_workday,
        "commute_records": commute_records,
    }


# ── 主流程 ─────────────────────────────────────────────────────────────────────
def main() -> None:
    print("生成天气数据和路况数据…")
    weather_base = get_weather_for_date(SNAPSHOT_DATE)

    for persona in PERSONAS:
        uid = persona["uid"]

        # ── 天气文件（单条快照）──
        weather_record = {"uid": uid, **weather_base}
        weather_path = os.path.join(OUTPUT_DIR, f"{uid}_weather_data.json")
        with open(weather_path, "w", encoding="utf-8") as f:
            json.dump(weather_record, f, ensure_ascii=False, indent=2)
        print(f"  [天气] {uid} ({persona['name']}) → {weather_path}")

        # ── 路况文件（单条快照）──
        traffic_record = generate_traffic_for_persona(persona)
        traffic_path = os.path.join(OUTPUT_DIR, f"{uid}_traffic_data.json")
        with open(traffic_path, "w", encoding="utf-8") as f:
            json.dump(traffic_record, f, ensure_ascii=False, indent=2)
        print(f"  [路况] {uid} ({persona['name']}) → {traffic_path}")

    print(f"\n完成！共生成 {len(PERSONAS)} 个用户 × 2 种数据 = {len(PERSONAS) * 2} 个文件")


if __name__ == "__main__":
    main()
