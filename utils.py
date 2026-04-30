"""文件作用：用于 utils 相关的数据处理或流程支持。"""

import json
import os
import tempfile
from datetime import datetime, timedelta
import random
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
