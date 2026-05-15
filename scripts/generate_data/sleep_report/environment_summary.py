"""环境摘要生成。"""

from __future__ import annotations

import json
import os
import random


def generate_environment_summary(user_id, record_date, output_dir="output"):
    """从环境数据文件中获取环境摘要"""
    # 读取环境数据文件
    environment_file = os.path.join(output_dir, f"{user_id}_environment_data.json")
    if not os.path.exists(environment_file):
        # 如果文件不存在，生成默认数据
        return generate_default_environment_summary()

    try:
        with open(environment_file, 'r', encoding='utf-8') as f:
            environment_data = json.load(f)
    except Exception as e:
        print(f"读取环境数据文件时出错: {str(e)}")
        return generate_default_environment_summary()

    # 筛选指定日期的环境数据
    date_data = [item for item in environment_data if item.get('record_date') == record_date]
    if not date_data:
        # 如果没有该日期的数据，生成默认数据
        return generate_default_environment_summary()

    # 计算温度数据
    temperatures = [item.get('temperature') for item in date_data if 'temperature' in item]
    if temperatures:
        temperature_value = round(sum(temperatures) / len(temperatures))
        temperature_max = max(temperatures)
        temperature_min = min(temperatures)
        if 18 <= temperature_value <= 24:
            temperature_status = "最佳"
        elif (16 <= temperature_value < 18) or (24 < temperature_value <= 26):
            temperature_status = "良好"
        elif temperature_value < 16:
            temperature_status = "偏冷"
        else:
            temperature_status = "偏热"
    else:
        temperature_value = 22
        temperature_max = 24
        temperature_min = 21
        temperature_status = "最佳"

    # 计算湿度数据
    humidities = [item.get('humidity') for item in date_data if 'humidity' in item]
    if humidities:
        humidity_value = round(sum(humidities) / len(humidities))
        humidity_max = max(humidities)
        humidity_min = min(humidities)
        if 40 <= humidity_value <= 60:
            humidity_status = "最佳"
        elif (30 <= humidity_value < 40) or (60 < humidity_value <= 70):
            humidity_status = "良好"
        elif humidity_value < 30:
            humidity_status = "干燥"
        else:
            humidity_status = "潮湿"
    else:
        humidity_value = 45
        humidity_max = 50
        humidity_min = 30
        humidity_status = "最佳"

    # 计算光照度数据
    illuminances = [item.get('illuminance') for item in date_data if 'illuminance' in item]
    if illuminances:
        illuminance_value = round(sum(illuminances) / len(illuminances))
        illuminance_max = max(illuminances)
        illuminance_min = min(illuminances)
        if illuminance_value < 5:
            illuminance_status = "最佳"
        elif 5 <= illuminance_value <= 20:
            illuminance_status = "偏亮"
        else:
            illuminance_status = "过亮"
    else:
        illuminance_value = 0
        illuminance_max = 5
        illuminance_min = 1
        illuminance_status = "最佳"

    # 计算噪音数据
    noises = [item.get('noise') for item in date_data if 'noise' in item]
    if noises:
        noise_value = round(sum(noises) / len(noises))
        noise_max = max(noises)
        noise_min = min(noises)
        if noise_value < 35:
            noise_status = "最佳"
        elif 35 <= noise_value <= 50:
            noise_status = "良好"
        elif 50 < noise_value <= 65:
            noise_status = "偏嘈杂"
        else:
            noise_status = "过载"
    else:
        noise_value = 35
        noise_max = 60
        noise_min = 20
        noise_status = "最佳"

    return {
        "temperature": {
            "value": temperature_value,
            "max": temperature_max,
            "min": temperature_min,
            "status": temperature_status
        },
        "humidity": {
            "value": humidity_value,
            "max": humidity_max,
            "min": humidity_min,
            "status": humidity_status
        },
        "illuminance": {
            "value": illuminance_value,
            "max": illuminance_max,
            "min": illuminance_min,
            "status": illuminance_status
        },
        "noise": {
            "value": noise_value,
            "max": noise_max,
            "min": noise_min,
            "status": noise_status
        }
    }


def generate_default_environment_summary():
    """生成默认环境摘要"""
    # 生成温度数据
    temperature_value = random.randint(18, 24)
    temperature_max = temperature_value + random.randint(0, 2)
    temperature_min = temperature_value - random.randint(0, 2)
    if 18 <= temperature_value <= 24:
        temperature_status = "最佳"
    elif (16 <= temperature_value < 18) or (24 < temperature_value <= 26):
        temperature_status = "良好"
    elif temperature_value < 16:
        temperature_status = "偏冷"
    else:
        temperature_status = "偏热"

    # 生成湿度数据
    humidity_value = random.randint(40, 60)
    humidity_max = humidity_value + random.randint(0, 5)
    humidity_min = humidity_value - random.randint(0, 5)
    if 40 <= humidity_value <= 60:
        humidity_status = "最佳"
    elif (30 <= humidity_value < 40) or (60 < humidity_value <= 70):
        humidity_status = "良好"
    elif humidity_value < 30:
        humidity_status = "干燥"
    else:
        humidity_status = "潮湿"

    # 生成光照度数据
    illuminance_value = random.randint(0, 20)
    illuminance_max = illuminance_value + random.randint(0, 5)
    illuminance_min = max(0, illuminance_value - random.randint(0, 1))
    if illuminance_value < 5:
        illuminance_status = "最佳"
    elif 5 <= illuminance_value <= 20:
        illuminance_status = "良好"
    else:
        illuminance_status = "过亮"

    # 生成噪音数据
    noise_value = random.randint(30, 50)
    noise_max = noise_value + random.randint(10, 30)
    noise_min = max(20, noise_value - random.randint(5, 15))
    if noise_value < 35:
        noise_status = "最佳"
    elif 35 <= noise_value <= 50:
        noise_status = "良好"
    elif 50 < noise_value <= 65:
        noise_status = "偏嘈杂"
    else:
        noise_status = "过载"

    return {
        "temperature": {
            "value": temperature_value,
            "max": temperature_max,
            "min": temperature_min,
            "status": temperature_status
        },
        "humidity": {
            "value": humidity_value,
            "max": humidity_max,
            "min": humidity_min,
            "status": humidity_status
        },
        "illuminance": {
            "value": illuminance_value,
            "max": illuminance_max,
            "min": illuminance_min,
            "status": illuminance_status
        },
        "noise": {
            "value": noise_value,
            "max": noise_max,
            "min": noise_min,
            "status": noise_status
        }
    }
