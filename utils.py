"""文件作用：用于 utils 相关的数据处理或流程支持。"""

import copy
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
import random
import math
import statistics
import requests

def parse_time(time_str, format='%H:%M'):
    """解析时间字符串为 datetime 对象"""
    return datetime.strptime(time_str, format)

def format_time(dt, format='%H:%M'):
    """格式化 datetime 对象为时间字符串"""
    return dt.strftime(format)

def calculate_duration(start_time, end_time):
    """计算两个时间之间的分钟数"""
    if isinstance(start_time, str):
        start_time = parse_time(start_time)
    if isinstance(end_time, str):
        end_time = parse_time(end_time)
    
    # 处理跨天情况
    if end_time < start_time:
        end_time += timedelta(days=1)
    
    delta = end_time - start_time
    return int(delta.total_seconds() / 60)

def validate_sleep_data(sleep_data):
    """验证睡眠数据的合理性"""
    raw_data = sleep_data.get('raw_data', {})
    
    # 验证总睡眠分钟数
    total_sleep = raw_data.get('total_sleep_minutes', 0)
    if total_sleep < 120 or total_sleep > 720:  # 2-12小时
        return False, "总睡眠分钟数不合理"
    
    # 清醒/深睡/浅睡/REM 为占 SPT（入睡→起床）的百分比，四者之和应为 100（与人格配置一致）
    awake_ratio = raw_data.get('awake_ratio', 0)
    deep_ratio = raw_data.get('deep_sleep_ratio', 0)
    light_ratio = raw_data.get('light_sleep_ratio', 0)
    rem_ratio = raw_data.get('rem_ratio', 0)

    total_four = awake_ratio + deep_ratio + light_ratio + rem_ratio
    if abs(total_four - 100) > 1:
        return False, "清醒/深睡/浅睡/REM 四阶段占比之和应约为100%（相对入睡至起床窗口 SPT）"
    
    # 验证时间顺序
    bed_time = datetime.fromisoformat(raw_data.get('bed_time', '').replace('Z', ''))
    sleep_time = datetime.fromisoformat(raw_data.get('sleep_time', '').replace('Z', ''))
    wake_time = datetime.fromisoformat(raw_data.get('wake_time', '').replace('Z', ''))
    wake_up_time = datetime.fromisoformat(raw_data.get('wake_up_time', '').replace('Z', ''))
    
    if not (bed_time <= sleep_time <= wake_time <= wake_up_time):
        return False, "时间顺序不合理"
    
    # 验证睡眠阶段数据
    idf_data = sleep_data.get('idf_data', [])
    if not idf_data:
        return False, "睡眠阶段数据为空"
    
    # 验证第一个阶段是清醒
    if idf_data[0].get('stage') != 'awake':
        return False, "第一个睡眠阶段不是清醒"
    
    # 验证阶段时间顺序
    for i in range(1, len(idf_data)):
        prev_end = idf_data[i-1].get('end')
        curr_start = idf_data[i].get('start')
        if prev_end != curr_start:
            return False, "睡眠阶段时间不连续"
        
        # 验证阶段不连续重复
        prev_stage = idf_data[i-1].get('stage')
        curr_stage = idf_data[i].get('stage')
        if prev_stage == curr_stage:
            return False, "睡眠阶段连续重复"
    
    return True, "数据验证通过"

def generate_random_value(min_val, max_val, mean=None, std=None):
    """生成符合特定范围和分布的随机值"""
    if mean and std:
        # 使用正态分布
        while True:
            value = random.normalvariate(mean, std)
            if min_val <= value <= max_val:
                return int(round(value))
    else:
        # 使用均匀分布
        return random.randint(min_val, max_val)

def adjust_value_by_profile(value, profile, factor_dict):
    """根据用户画像调整数值"""
    for condition, factor in factor_dict.items():
        if condition in profile:
            return int(value * factor)
    return value

def generate_time_based_on_profile(profile):
    """根据用户画像生成合理的睡眠时间"""
    # 基础卧床时间（晚上9点到12点）
    base_bed_hour = random.randint(21, 23)
    base_bed_minute = random.randint(0, 59)
    
    # 根据用户画像调整
    if '失眠' in profile:
        # 失眠用户可能更晚睡觉
        base_bed_hour = random.randint(22, 23)
    elif '健康' in profile:
        # 健康用户可能更早睡觉
        base_bed_hour = random.randint(21, 22)
    
    return base_bed_hour, base_bed_minute


def atomic_write_json(path, data, *, ensure_ascii=False, indent=2):
    """
    原子写入 JSON：先写到同目录临时文件，再 os.replace 覆盖目标。
    避免进程在 json.dump 中途崩溃时把原文件截断成半截 JSON。
    """
    path = os.path.abspath(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_open_meteo_weather(latitude, longitude, query_date=None, timezone="auto", timeout=10):
    """调用 Open-Meteo API，只返回紫外线指数、湿度、气压、降水强度。"""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {"latitude": latitude, "longitude": longitude, "timezone": timezone}

    if query_date:
        # 指定日期查询：按小时拉取后聚合成单日结果
        if isinstance(query_date, datetime):
            date_str = query_date.strftime("%Y-%m-%d")
        else:
            try:
                date_str = datetime.strptime(str(query_date), "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError as exc:
                raise ValueError("query_date 格式必须是 YYYY-MM-DD") from exc

        params.update(
            {
                "start_date": date_str,
                "end_date": date_str,
                "hourly": "uv_index,relative_humidity_2m,surface_pressure,precipitation",
            }
        )
    else:
        params["current"] = "uv_index,relative_humidity_2m,surface_pressure,precipitation"

    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if query_date:
        hourly = data.get("hourly")
        if not isinstance(hourly, dict):
            raise RuntimeError("Open-Meteo 返回中缺少 hourly 字段")

        uv_index_list = [x for x in hourly.get("uv_index", []) if x is not None]
        humidity_list = [x for x in hourly.get("relative_humidity_2m", []) if x is not None]
        pressure_list = [x for x in hourly.get("surface_pressure", []) if x is not None]
        precipitation_list = [x for x in hourly.get("precipitation", []) if x is not None]

        return {
            "uv_index": max(uv_index_list) if uv_index_list else None,
            "humidity": round(sum(humidity_list) / len(humidity_list), 2) if humidity_list else None,
            "pressure": round(sum(pressure_list) / len(pressure_list), 2) if pressure_list else None,
            "precipitation_intensity": round(sum(precipitation_list) / len(precipitation_list), 3) if precipitation_list else None,
        }

    current = data.get("current")
    if not isinstance(current, dict):
        raise RuntimeError("Open-Meteo 返回中缺少 current 字段")

    return {
        "uv_index": current.get("uv_index"),
        "humidity": current.get("relative_humidity_2m"),
        "pressure": current.get("surface_pressure"),
        "precipitation_intensity": current.get("precipitation"),
    }


_CN_OFFSET = timedelta(hours=8)
_CN_TZ = timezone(_CN_OFFSET, name="CST")
_QWEATHER_HOST = "https://nv63yxq3rp.re.qweatherapi.com"


def cn_aqi_six_level_from_display(aqi_display):
    """
    将 aqiDisplay 数值按常见 AQI 分段映射为 1~6 档及中文等级。
    区间：0~50(1)优，51~100(2)良，101~150(3)轻度污染，151~200(4)中度污染，
    201~300(5)重度污染，301~500(6)严重污染；>500 视为 (6)；无法解析为数字返回 null。
    """
    _null = {"aqi_six_level": None, "aqi_six_level_label": None, "aqi_six_level_display": None}
    if aqi_display is None:
        return _null
    try:
        v = float(str(aqi_display).strip())
    except (TypeError, ValueError):
        return _null
    if v < 0:
        return _null
    if v <= 50:
        n, label = 1, "优"
    elif v <= 100:
        n, label = 2, "良"
    elif v <= 150:
        n, label = 3, "轻度污染"
    elif v <= 200:
        n, label = 4, "中度污染"
    elif v <= 300:
        n, label = 5, "重度污染"
    else:
        n, label = 6, "严重污染"
    return {
        "aqi_six_level": n,
        "aqi_six_level_label": label,
        "aqi_six_level_display": f"({n}) {label}",
    }


def uv_index_to_light_level_label(uv_index):
    """
    将紫外线指数映射为光强档位文案（与和风日预报 uvIndex 数值口径一致）。
    0~2 暗光，3~4 弱光，5~6 亮光，7 及以上为强光（含 11+）。
    无法解析为数字时返回 None。
    """
    if uv_index is None:
        return None
    try:
        v = float(str(uv_index).strip())
    except (TypeError, ValueError):
        return None
    if v < 0:
        return None
    if v <= 2:
        return "暗光"
    if v <= 4:
        return "弱光"
    if v <= 6:
        return "亮光"
    return "强光"


def fetch_qweather_today_snapshot(
    *,
    location="101010100",
    latitude=39.9042,
    longitude=116.4074,
    api_key=None,
    output_dir="output",
    output_filename="qweather_today_snapshot.json",
    timeout=15,
):
    """
    调用和风天气（自定义域名）3 日预报与空气质量日预报，仅取「今日」（按中国标准时间日历日）
    的日出/日落/紫外线/湿度，以及空气质量 indexes 中优先 CN AQI 的 aqiDisplay，并原子写入 output 下 JSON。

    api_key: 和风 API Key；为空时读取环境变量 QWEATHER_API_KEY。当前自定义 Host 使用查询参数
    ``key`` 鉴权（与 devapi 一致）；未配置时接口可能返回 401。

    返回并写入的 dict 含键：sunrise、sunset、uvIndex、humidity、aqiDisplay。
    uvIndex 为紫外线档位文案（0~2 暗光，3~4 弱光，5~6 亮光，7+ 强光），非数值。
    aqiDisplay 为中国 AQI 六档中文等级（优/良/轻度污染/…），非原始指数。
    """
    try:
        from dotenv import load_dotenv

        _repo_root = os.path.dirname(os.path.abspath(__file__))
        load_dotenv(os.path.join(_repo_root, ".env"))
    except ImportError:
        pass

    today = datetime.now(_CN_TZ).date()
    today_str = today.isoformat()

    key = api_key or os.getenv("QWEATHER_API_KEY")
    weather_params = {"location": location}
    if key:
        weather_params["key"] = key

    weather_url = f"{_QWEATHER_HOST}/v7/weather/3d"
    w_resp = requests.get(
        weather_url,
        params=weather_params,
        timeout=timeout,
    )
    w_resp.raise_for_status()
    w_data = w_resp.json()
    if str(w_data.get("code")) != "200":
        raise RuntimeError(f"和风天气 3d 接口异常: code={w_data.get('code')!r}")

    daily_list = w_data.get("daily") or []
    today_weather = next(
        (d for d in daily_list if str(d.get("fxDate", "")) == today_str),
        daily_list[0] if daily_list else None,
    )
    if not today_weather:
        raise RuntimeError("和风天气 3d 返回中无 daily 数据")

    air_url = f"{_QWEATHER_HOST}/airquality/v1/daily/{latitude}/{longitude}"
    air_params = {"key": key} if key else None
    a_resp = requests.get(air_url, params=air_params, timeout=timeout)
    a_resp.raise_for_status()
    a_data = a_resp.json()
    if not isinstance(a_data, dict):
        raise RuntimeError("空气质量接口返回非 JSON 对象")
    days = a_data.get("days") or []

    def _parse_air_utc(value):
        if not value:
            return None
        s = str(value).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _day_covers_now_utc(day):
        start = _parse_air_utc(day.get("forecastStartTime"))
        end = _parse_air_utc(day.get("forecastEndTime"))
        if start is None or end is None:
            return False
        now_utc = datetime.now(timezone.utc)
        return start <= now_utc < end

    today_air = next((d for d in days if _day_covers_now_utc(d)), None)
    if today_air is None and days:
        today_air = days[0]

    aqi_display = None
    if today_air:
        indexes = today_air.get("indexes") or []
        for idx in indexes:
            if idx.get("code") == "cn-mee":
                aqi_display = idx.get("aqiDisplay")
                break
        if aqi_display is None and indexes:
            aqi_display = indexes[0].get("aqiDisplay")

    raw_uv = today_weather.get("uvIndex")
    aqi_lv = cn_aqi_six_level_from_display(aqi_display)
    out = {
        "sunrise": today_weather.get("sunrise"),
        "sunset": today_weather.get("sunset"),
        "uvIndex": uv_index_to_light_level_label(raw_uv),
        "humidity": today_weather.get("humidity"),
        "aqiDisplay": aqi_lv["aqi_six_level_label"],
    }

    out_path = os.path.join(os.path.abspath(output_dir), output_filename)
    atomic_write_json(out_path, out, ensure_ascii=False, indent=2)
    return out


def _amap_geocode(address, api_key, city=None, timeout=10):
    """使用高德地理编码把地址转换为经纬度字符串（lng,lat）。"""
    url = "https://restapi.amap.com/v3/geocode/geo"
    params = {"key": api_key, "address": address}
    if city:
        params["city"] = city

    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "1":
        raise RuntimeError(f"高德地理编码失败: {data.get('info', '未知错误')}")
    geocodes = data.get("geocodes") or []
    if not geocodes:
        raise RuntimeError(f"地址无法解析为经纬度: {address}")
    location = geocodes[0].get("location")
    if not location:
        raise RuntimeError(f"高德地理编码返回缺少 location: {address}")
    return location


def get_amap_route_info(
    origin,
    destination,
    api_key=None,
    city=None,
    strategy=0,
    timeout=10,
):
    """
    查询高德驾车路线，返回里程、耗时、红绿灯和拥堵情况。

    参数:
    - origin: 起点，可传 "经度,纬度" 或地址字符串
    - destination: 终点，可传 "经度,纬度" 或地址字符串
    - api_key: 高德 API Key；为空时读取环境变量 AMAP_API_KEY
    - city: 地址解析辅助城市名（origin/destination 为地址时可选）
    - strategy: 驾车策略（0-速度优先，避免收费等请参考高德文档）
    """
    key = api_key or os.getenv("AMAP_API_KEY")
    if not key:
        raise ValueError("缺少高德 API Key，请传入 api_key 或设置环境变量 AMAP_API_KEY")

    def _normalize_point(point):
        text = str(point).strip()
        if "," in text:
            parts = [p.strip() for p in text.split(",")]
            if len(parts) == 2:
                try:
                    float(parts[0])
                    float(parts[1])
                    return f"{parts[0]},{parts[1]}"
                except ValueError:
                    pass
        return _amap_geocode(text, key, city=city, timeout=timeout)

    origin_location = _normalize_point(origin)
    destination_location = _normalize_point(destination)

    url = "https://restapi.amap.com/v3/direction/driving"
    params = {
        "key": key,
        "origin": origin_location,
        "destination": destination_location,
        "strategy": strategy,
        "extensions": "all",
    }
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "1":
        raise RuntimeError(f"高德路径规划失败: {data.get('info', '未知错误')}")

    route = data.get("route") or {}
    paths = route.get("paths") or []
    if not paths:
        raise RuntimeError("高德路径规划返回为空，无法获取路线信息")
    best_path = paths[0]

    traffic_status_counter = {"未知": 0, "畅通": 0, "缓行": 0, "拥堵": 0, "严重拥堵": 0}
    status_map = {
        "unknown": "未知",
        "smooth": "畅通",
        "slow": "缓行",
        "jam": "拥堵",
        "serious jam": "严重拥堵",
    }

    for step in best_path.get("steps", []):
        for tmc in step.get("tmcs", []):
            status = status_map.get(str(tmc.get("status", "")).strip().lower(), "未知")
            distance = int(tmc.get("distance") or 0)
            traffic_status_counter[status] += distance

    distance_m = int(best_path.get("distance") or 0)
    duration_s = int(best_path.get("duration") or 0)
    traffic_distance_m = sum(traffic_status_counter.values())
    congestion_distance_m = (
        traffic_status_counter["缓行"]
        + traffic_status_counter["拥堵"]
        + traffic_status_counter["严重拥堵"]
    )

    congestion_ratio = (
        round(congestion_distance_m / traffic_distance_m, 4) if traffic_distance_m > 0 else None
    )

    return {
        "origin": origin_location,
        "destination": destination_location,
        "distance_m": distance_m,
        "distance_km": round(distance_m / 1000, 2),
        "duration_s": duration_s,
        "duration_min": round(duration_s / 60, 1),
        "traffic_lights": int(best_path.get("traffic_lights") or 0),
        "toll_cost": float(best_path.get("tolls") or 0),
        "congestion_ratio": congestion_ratio,
        "traffic_status_distance_m": traffic_status_counter,
        "raw_route": best_path,
    }


def _clamp_score(value):
    """将分值限制在 0~100。"""
    return max(0.0, min(100.0, float(value)))


def _safe_float(value, default=0.0):
    """尽量把输入转为 float。"""
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _parse_iso_datetime(value):
    """解析 ISO 时间字符串，兼容末尾 Z。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except ValueError:
        return None


def _minutes_since_midnight(dt_obj):
    """把 datetime 转为当天分钟数。"""
    if dt_obj is None:
        return None
    return dt_obj.hour * 60 + dt_obj.minute + dt_obj.second / 60.0


def score_sleep_duration(total_sleep_minutes):
    """
    睡眠时长得分：
    - 7~9h: 100
    - <7h: 每少30分钟扣10分，<=4h 记0分
    - >9h: 每多30分钟扣5分，>=11h 记0分
    """
    minutes = _safe_float(total_sleep_minutes)
    if minutes <= 240:
        return 0.0
    if 420 <= minutes <= 540:
        return 100.0
    if minutes < 420:
        deduction_steps = math.ceil((420 - minutes) / 30.0)
        return _clamp_score(100 - deduction_steps * 10)
    if minutes >= 660:
        return 0.0
    deduction_steps = math.ceil((minutes - 540) / 30.0)
    return _clamp_score(100 - deduction_steps * 5)


def score_sleep_latency(latency_minutes):
    """入睡快慢得分。"""
    minutes = _safe_float(latency_minutes)
    if minutes <= 15:
        return 100.0
    if minutes <= 30:
        return 80.0
    if minutes <= 45:
        return 60.0
    if minutes <= 60:
        return 40.0
    return 0.0


def score_night_stability(stability_ratio):
    """夜间安稳度得分（稳定占比 -> 分档）。"""
    ratio = max(0.0, min(1.0, _safe_float(stability_ratio)))
    if ratio >= 0.8:
        return 100.0
    if ratio >= 0.7:
        return 80.0
    if ratio >= 0.6:
        return 60.0
    if ratio >= 0.5:
        return 40.0
    return 0.0


def score_sleep_regularity(fluctuation_minutes):
    """作息规律度得分（综合波动分钟数 -> 分档）。"""
    minutes = _safe_float(fluctuation_minutes)
    if minutes <= 30:
        return 100.0
    if minutes <= 60:
        return 80.0
    if minutes <= 90:
        return 60.0
    if minutes <= 120:
        return 40.0
    return 0.0


def score_no_abnormal_events(total_sleep_minutes, abnormal_total_duration_sec, abnormal_count):
    """
    异常事件得分（时长占比 + 次数修正）：
    1) 基础占比 = (总睡眠时长 - 异常总时长) / 总睡眠时长
    2) 次数修正：<=2 不变，3~5 *0.8，>=6 *0.6
    3) 最终占比<30% 则 0 分，否则 最终占比*100
    """
    total_sleep_sec = _safe_float(total_sleep_minutes) * 60.0
    abnormal_sec = max(0.0, _safe_float(abnormal_total_duration_sec))
    count = _safe_float(abnormal_count)

    if total_sleep_sec <= 0:
        return 0.0, 0.0, 0.0

    no_abnormal_sec = max(0.0, total_sleep_sec - abnormal_sec)
    base_ratio = max(0.0, min(1.0, no_abnormal_sec / total_sleep_sec))

    if count <= 2:
        ratio_after_count = base_ratio
    elif count <= 5:
        ratio_after_count = base_ratio * 0.8
    else:
        ratio_after_count = base_ratio * 0.6

    ratio_after_count = max(0.0, min(1.0, ratio_after_count))
    if ratio_after_count < 0.3:
        return 0.0, base_ratio, ratio_after_count
    return _clamp_score(ratio_after_count * 100.0), base_ratio, ratio_after_count


def _iter_user_ids_from_output_dir(output_dir):
    """扫描 output 目录，找出含 health_data 的 uid。"""
    user_ids = set()
    for file_name in os.listdir(output_dir):
        if file_name.endswith("_health_data.json"):
            user_ids.add(file_name.replace("_health_data.json", ""))
    return sorted(user_ids)


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_abnormal_event_index(events):
    """
    以 record_date 聚合异常事件:
    { date: {"abnormal_count": x, "abnormal_duration_sec": y} }
    """
    date_index = {}
    for event in events:
        if event.get("type") != "abnormal":
            continue
        record_date = event.get("record_date")
        if not record_date:
            continue
        date_index.setdefault(record_date, {"abnormal_count": 0, "abnormal_duration_sec": 0.0})
        date_index[record_date]["abnormal_count"] += 1
        date_index[record_date]["abnormal_duration_sec"] += max(
            0.0, _safe_float(event.get("duration_sec"))
        )
    return date_index


def calculate_sleep_map_score_window(start_date, uid=None, days=14, output_dir="output"):
    """
    计算睡眠地图分数（按日期窗口，支持单用户/全用户）。

    参数:
    - start_date: 起始日期，YYYY-MM-DD
    - uid: 可选。传入用户 id 时只算该用户；为空时统计 output 下所有用户。
    - days: 窗口天数，默认 14 天（含起始日）
    - output_dir: 数据目录，默认 output

    返回 dict 中 nightly_records 每条在派生指标外，另含 source_health（当日 health 行）
    与 source_sleep_events（当日睡眠事件列表，深拷贝）。
    """
    if days <= 0:
        raise ValueError("days 必须大于 0")

    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = start + timedelta(days=days - 1)
    output_dir = os.path.abspath(output_dir)

    if not os.path.isdir(output_dir):
        raise FileNotFoundError(f"output 目录不存在: {output_dir}")

    if uid:
        user_ids = [uid]
    else:
        user_ids = _iter_user_ids_from_output_dir(output_dir)

    all_total_sleep_minutes = []
    all_sleep_latency_minutes = []
    all_night_stability_ratios = []
    all_abnormal_counts = []
    all_abnormal_durations_sec = []
    sleep_clock_minutes = []
    wake_clock_minutes = []
    nightly_records = []
    users_included = set()

    for current_uid in user_ids:
        health_path = os.path.join(output_dir, f"{current_uid}_health_data.json")
        events_path = os.path.join(output_dir, f"{current_uid}_sleep_events.json")
        if not os.path.exists(health_path) or not os.path.exists(events_path):
            continue

        health_list = _load_json(health_path)
        events_list = _load_json(events_path)
        abnormal_by_date = _build_abnormal_event_index(events_list)
        events_by_date = {}
        for e in events_list:
            rd = e.get("record_date")
            if rd:
                events_by_date.setdefault(rd, []).append(e)

        for item in health_list:
            record_date = item.get("record_date")
            if not record_date:
                continue
            try:
                record_day = datetime.strptime(record_date, "%Y-%m-%d").date()
            except ValueError:
                continue

            if not (start <= record_day <= end):
                continue

            users_included.add(current_uid)
            raw_data = item.get("raw_data", {})
            total_sleep_minutes = max(0.0, _safe_float(raw_data.get("total_sleep_minutes")))
            sleep_latency = max(0.0, _safe_float(raw_data.get("sleep_latency")))

            event_stats = abnormal_by_date.get(
                record_date, {"abnormal_count": 0, "abnormal_duration_sec": 0.0}
            )
            abnormal_count = event_stats["abnormal_count"]
            abnormal_duration_sec = event_stats["abnormal_duration_sec"]
            total_sleep_sec = total_sleep_minutes * 60.0
            if total_sleep_sec > 0:
                night_stability_ratio = max(
                    0.0, min(1.0, (total_sleep_sec - abnormal_duration_sec) / total_sleep_sec)
                )
            else:
                night_stability_ratio = 0.0

            sleep_dt = _parse_iso_datetime(raw_data.get("sleep_time"))
            wake_dt = _parse_iso_datetime(raw_data.get("wake_up_time"))
            sleep_minutes_clock = _minutes_since_midnight(sleep_dt)
            wake_minutes_clock = _minutes_since_midnight(wake_dt)

            all_total_sleep_minutes.append(total_sleep_minutes)
            all_sleep_latency_minutes.append(sleep_latency)
            all_night_stability_ratios.append(night_stability_ratio)
            all_abnormal_counts.append(float(abnormal_count))
            all_abnormal_durations_sec.append(abnormal_duration_sec)
            if sleep_minutes_clock is not None:
                sleep_clock_minutes.append(sleep_minutes_clock)
            if wake_minutes_clock is not None:
                wake_clock_minutes.append(wake_minutes_clock)

            nightly_records.append(
                {
                    "uid": current_uid,
                    "record_date": record_date,
                    "total_sleep_minutes": total_sleep_minutes,
                    "sleep_latency_minutes": sleep_latency,
                    "night_stability_ratio": night_stability_ratio,
                    "abnormal_count": abnormal_count,
                    "abnormal_duration_sec": abnormal_duration_sec,
                    "sleep_time_minutes_of_day": sleep_minutes_clock,
                    "wake_up_time_minutes_of_day": wake_minutes_clock,
                    "source_health": copy.deepcopy(item),
                    "source_sleep_events": copy.deepcopy(
                        events_by_date.get(record_date, [])
                    ),
                }
            )

    if not nightly_records:
        return {
            "window": {"start_date": str(start), "end_date": str(end), "days": days},
            "uid": uid,
            "users_included": [],
            "night_count": 0,
            "message": "指定条件下无可用睡眠数据",
        }

    avg_total_sleep_minutes = statistics.mean(all_total_sleep_minutes)
    avg_sleep_latency_minutes = statistics.mean(all_sleep_latency_minutes)
    avg_night_stability_ratio = statistics.mean(all_night_stability_ratios)
    avg_abnormal_count = statistics.mean(all_abnormal_counts)
    avg_abnormal_duration_sec = statistics.mean(all_abnormal_durations_sec)
    sleep_std = statistics.pstdev(sleep_clock_minutes) if len(sleep_clock_minutes) > 1 else 0.0
    wake_std = statistics.pstdev(wake_clock_minutes) if len(wake_clock_minutes) > 1 else 0.0
    regularity_fluctuation_minutes = (sleep_std + wake_std) / 2.0

    sleep_duration_score = score_sleep_duration(avg_total_sleep_minutes)
    sleep_latency_score = score_sleep_latency(avg_sleep_latency_minutes)
    night_stability_score = score_night_stability(avg_night_stability_ratio)
    regularity_score = score_sleep_regularity(regularity_fluctuation_minutes)
    no_abnormal_score, abnormal_base_ratio, abnormal_final_ratio = score_no_abnormal_events(
        total_sleep_minutes=avg_total_sleep_minutes,
        abnormal_total_duration_sec=avg_abnormal_duration_sec,
        abnormal_count=avg_abnormal_count,
    )

    total_score = (
        sleep_duration_score * 0.25
        + sleep_latency_score * 0.15
        + night_stability_score * 0.25
        + regularity_score * 0.15
        + no_abnormal_score * 0.20
    )

    return {
        "window": {"start_date": str(start), "end_date": str(end), "days": days},
        "uid": uid,
        "users_included": sorted(users_included),
        "night_count": len(nightly_records),
        "averages": {
            "total_sleep_minutes": round(avg_total_sleep_minutes, 2),
            "sleep_latency_minutes": round(avg_sleep_latency_minutes, 2),
            "night_stability_ratio": round(avg_night_stability_ratio, 4),
            "abnormal_count": round(avg_abnormal_count, 2),
            "abnormal_duration_sec": round(avg_abnormal_duration_sec, 2),
            "sleep_time_std_minutes": round(sleep_std, 2),
            "wake_up_time_std_minutes": round(wake_std, 2),
            "regularity_fluctuation_minutes": round(regularity_fluctuation_minutes, 2),
        },
        "scores": {
            "sleep_duration_score": round(sleep_duration_score, 2),
            "sleep_latency_score": round(sleep_latency_score, 2),
            "night_stability_score": round(night_stability_score, 2),
            "regularity_score": round(regularity_score, 2),
            "no_abnormal_events_score": round(no_abnormal_score, 2),
            "composite_score": round(_clamp_score(total_score), 2),
        },
        "abnormal_ratio_debug": {
            "base_ratio": round(abnormal_base_ratio, 4),
            "final_ratio_after_count_adjustment": round(abnormal_final_ratio, 4),
        },
        "formula": "最终综合得分=睡眠时长得分x0.25+入睡快慢得分x0.15+夜间安稳得分x0.25+作息规律度得分x0.15+无异常事件得分x0.20",
        "nightly_records": nightly_records,
    }
