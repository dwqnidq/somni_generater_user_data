"""
为每种睡眠人格生成健康数据
8种人格 × 4条 = 32条数据，输出到 output/all_personality_health_data.json
"""
# 文件作用：用于 generate personality data 相关的数据处理或流程支持。

import json
import os
import sys
import random
from datetime import datetime, timedelta
from bson import ObjectId
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from personality_profile import (
    get_sleep_latency,
    get_total_sleep_minutes,
    get_turnover_count,
    get_apnea_count,
    get_avg_heartbeat,
    get_avg_respiration,
    get_leave_bed_info,
    get_wake_after_sleep,
    calculate_sleep_score_by_personality,
)

load_dotenv()

CONFIG_FILE = 'config/config.json'
OUTPUT_FILE = 'output/all_personality_health_data.json'
RECORDS_PER_PERSONALITY = 4


def generate_object_id():
    return str(ObjectId())


def generate_sleep_data_for_user(user_config, base_date):
    """根据用户配置和基准日期生成一条睡眠健康数据"""
    user_id = user_config['user_id']
    personality_type = user_config.get('personalInformation', {}).get('type', 'M-L-C')

    # --- 入睡时间 ---
    sleep_time_range = user_config.get('sleepTime', {})
    min_sleep = sleep_time_range.get('min', ['22:00'])[0]
    max_sleep = sleep_time_range.get('max', ['23:00'])[0]
    min_h, min_m = map(int, min_sleep.split(':'))
    max_h, max_m = map(int, max_sleep.split(':'))

    if min_h <= max_h:
        sleep_hour = random.randint(min_h, max_h)
        if sleep_hour == min_h:
            sleep_minute = random.randint(min_m, 59)
        elif sleep_hour == max_h:
            sleep_minute = random.randint(0, max_m)
        else:
            sleep_minute = random.randint(0, 59)
    else:
        # 跨午夜
        if random.random() < 0.5:
            sleep_hour = min_h
            sleep_minute = random.randint(min_m, 59)
        else:
            sleep_hour = max_h
            sleep_minute = random.randint(0, max_m)

    sleep_time = base_date.replace(hour=sleep_hour, minute=sleep_minute, second=0, microsecond=0)
    record_date = sleep_time.strftime('%Y-%m-%d')

    # --- 入睡潜伏期 & 上床时间 ---
    sleep_latency = get_sleep_latency(personality_type)
    bed_time = sleep_time - timedelta(minutes=sleep_latency)

    # --- 净睡眠时长 & 清醒比例 ---
    total_sleep_minutes = get_total_sleep_minutes(personality_type)
    awake_range = user_config.get('awakeSleep', {'min': [5], 'max': [15]})
    awake_ratio = random.randint(awake_range['min'][0], awake_range['max'][0])
    total_time_minutes = int(total_sleep_minutes / (1 - awake_ratio / 100))

    # --- 各睡眠阶段比例 ---
    deep_range = user_config.get('deepSleep', {'min': [15], 'max': [25]})
    light_range = user_config.get('lightSleep', {'min': [45], 'max': [55]})
    rem_range = user_config.get('remSleep', {'min': [20], 'max': [25]})

    deep_ratio = random.randint(deep_range['min'][0], deep_range['max'][0])
    light_ratio = random.randint(light_range['min'][0], light_range['max'][0])
    rem_ratio = random.randint(rem_range['min'][0], rem_range['max'][0])

    # 调整总和为100
    total_ratio = deep_ratio + awake_ratio + light_ratio + rem_ratio
    diff = 100 - total_ratio
    light_ratio = max(light_range['min'][0], min(light_range['max'][0], light_ratio + diff))
    total_ratio = deep_ratio + awake_ratio + light_ratio + rem_ratio
    diff = 100 - total_ratio
    if diff != 0:
        deep_ratio = max(deep_range['min'][0], min(deep_range['max'][0], deep_ratio + diff))

    # --- 时间节点 ---
    wake_time = sleep_time + timedelta(minutes=total_time_minutes)
    wake_up_delta = get_wake_after_sleep(personality_type)
    wake_up_time = wake_time + timedelta(minutes=wake_up_delta)

    # 转为UTC（东八区 → UTC）
    bed_time_utc = bed_time - timedelta(hours=8)
    sleep_time_utc = sleep_time - timedelta(hours=8)
    wake_time_utc = wake_time - timedelta(hours=8)
    wake_up_time_utc = wake_up_time - timedelta(hours=8)

    # --- 其他指标 ---
    apnea_count = get_apnea_count(personality_type)
    avg_heartbeat = get_avg_heartbeat(personality_type)
    avg_respiration = get_avg_respiration(personality_type)
    leave_bed_count, leave_bed_minutes = get_leave_bed_info(personality_type)
    turnover_count = get_turnover_count(personality_type)
    sleep_efficiency = min(100, max(50, int(total_sleep_minutes / total_time_minutes * 100)))
    sleep_score = calculate_sleep_score_by_personality(
        personality_type, total_sleep_minutes, deep_ratio, sleep_latency, awake_ratio
    )

    now_utc = (datetime.now() - timedelta(hours=8)).isoformat() + "Z"

    return {
        "_id": generate_object_id(),
        "user_id": user_id,
        "record_date": record_date,
        "timestamp": bed_time_utc.isoformat() + "Z",
        "dimension_type": "sleep_quality_analysis",
        "data_source": "health_monitor",
        "raw_data": {
            "apnea_count": apnea_count,
            "average_heartbeat": avg_heartbeat,
            "average_respiration": avg_respiration,
            "awake_ratio": awake_ratio,
            "deep_sleep_ratio": deep_ratio,
            "leave_bed_count": leave_bed_count,
            "leave_bed_minutes": leave_bed_minutes,
            "light_sleep_ratio": light_ratio,
            "rem_ratio": rem_ratio,
            "sleep_score": sleep_score,
            "total_sleep_minutes": total_sleep_minutes,
            "turnover_count": turnover_count,
            "bed_time": bed_time_utc.isoformat() + "Z",
            "sleep_time": sleep_time_utc.isoformat() + "Z",
            "wake_time": wake_time_utc.isoformat() + "Z",
            "wake_up_time": wake_up_time_utc.isoformat() + "Z",
            "sleep_latency": sleep_latency,
            "sleep_efficiency": sleep_efficiency
        },
        "create_time": now_utc,
        "update_time": now_utc,
        "__v": 0
    }


def main():
    # 加载配置
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        config = json.load(f)

    user_profiles = config.get('user_profiles', [])
    print(f"共 {len(user_profiles)} 种人格，每种生成 {RECORDS_PER_PERSONALITY} 条，目标 {len(user_profiles) * RECORDS_PER_PERSONALITY} 条")

    all_records = []

    for user in user_profiles:
        personality_type = user.get('personalInformation', {}).get('type', '未知')
        label = user.get('personalInformation', {}).get('label', '')
        user_id = user.get('user_id', '')
        print(f"  生成人格 [{personality_type}] {label} (user_id: {user_id})")

        # 以今天为基准，每条数据间隔1天
        base = datetime.now()
        for i in range(RECORDS_PER_PERSONALITY):
            date = base - timedelta(days=i + 1)
            record = generate_sleep_data_for_user(user, date)
            all_records.append(record)

    # 输出
    os.makedirs('output', exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)

    print(f"\n完成，共生成 {len(all_records)} 条数据，已保存到 {OUTPUT_FILE}")


if __name__ == '__main__':
    main()
