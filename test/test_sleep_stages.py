"""文件作用：用于 test sleep stages 相关的数据处理或流程支持。"""

import json
import os
from datetime import datetime, timedelta

# 测试函数：验证idf_data中对应阶段的总分钟数是否等于公式计算出的分钟数
def test_sleep_stages():
    # 读取输出目录中的所有健康数据文件
    output_dir = "output"
    for filename in os.listdir(output_dir):
        if filename.endswith("_health_data.json"):
            file_path = os.path.join(output_dir, filename)
            print(f"\n测试文件: {filename}")
            
            # 读取文件内容
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 检查数据是否为数组（多个日期的数据）
            if isinstance(data, list):
                # 测试每个日期的数据
                for idx, record in enumerate(data):
                    print(f"\n  测试记录 {idx+1}: {record.get('record_date', '未知日期')}")
                    test_single_record(record)
            else:
                # 单个记录
                test_single_record(data)

def test_single_record(data):
    # 获取原始数据中的各个阶段比例和总睡眠分钟数
    raw_data = data.get('raw_data', {})
    total_sleep_minutes = raw_data.get('total_sleep_minutes', 0)
    awake_ratio = raw_data.get('awake_ratio', 0)
    deep_sleep_ratio = raw_data.get('deep_sleep_ratio', 0)
    light_sleep_ratio = raw_data.get('light_sleep_ratio', 0)
    rem_ratio = raw_data.get('rem_ratio', 0)

    ratio_sum = awake_ratio + deep_sleep_ratio + light_sleep_ratio + rem_ratio
    if abs(ratio_sum - 100) > 1:
        print(f"    警告: 四阶段占比之和为 {ratio_sum}，期望约 100")

    # 理论分钟数：占比以 SPT（入睡→起床）为分母；TST = total_sleep_minutes
    ar = awake_ratio / 100.0 if awake_ratio is not None else 0.0
    if ar >= 0.999:
        spt = int(total_sleep_minutes)
    else:
        spt = int(round(float(total_sleep_minutes) / max(1e-6, (1.0 - ar))))
    awake_minutes = int(round(spt * awake_ratio / 100.0))
    deep_sleep_minutes = int(round(spt * deep_sleep_ratio / 100.0))
    light_sleep_minutes = int(round(spt * light_sleep_ratio / 100.0))
    rem_minutes = int(round(spt * rem_ratio / 100.0))
    
    # 调整以确保总和正确
    total_calculated = awake_minutes + deep_sleep_minutes + light_sleep_minutes + rem_minutes
    if total_calculated != spt:
        diff = spt - total_calculated
        light_sleep_minutes += diff
    
    # 计算idf_data中各阶段的实际分钟数
    idf_data = data.get('idf_data', [])
    actual_awake_minutes = 0
    actual_deep_sleep_minutes = 0
    actual_light_sleep_minutes = 0
    actual_rem_minutes = 0
    
    for stage in idf_data:
        stage_type = stage.get('stage')
        start_time = stage.get('start')
        end_time = stage.get('end')
        
        # 解析时间
        start_hour, start_minute = map(int, start_time.split(':'))
        end_hour, end_minute = map(int, end_time.split(':'))
        
        # 计算持续时间（分钟）
        duration = (end_hour * 60 + end_minute) - (start_hour * 60 + start_minute)
        if duration < 0:
            # 跨天情况
            duration += 24 * 60
        
        # 累加各阶段的分钟数
        if stage_type == 'awake':
            actual_awake_minutes += duration
        elif stage_type == 'deep':
            actual_deep_sleep_minutes += duration
        elif stage_type == 'light':
            actual_light_sleep_minutes += duration
        elif stage_type == 'rem':
            actual_rem_minutes += duration
    
    # 输出详细的阶段信息，帮助调试
    print(f"    阶段详情:")
    for i, stage in enumerate(idf_data):
        stage_type = stage.get('stage')
        start_time = stage.get('start')
        end_time = stage.get('end')
        
        # 解析时间
        start_hour, start_minute = map(int, start_time.split(':'))
        end_hour, end_minute = map(int, end_time.split(':'))
        
        # 计算持续时间（分钟）
        duration = (end_hour * 60 + end_minute) - (start_hour * 60 + start_minute)
        if duration < 0:
            # 跨天情况
            duration += 24 * 60
        
        print(f"      阶段 {i+1}: {stage_type} ({start_time} - {end_time}) - {duration}分钟")
    
    # 输出结果
    print(f"    理论值:")
    print(f"      总时间(SPT): {spt} 分钟")
    print(f"      清醒: {awake_minutes} 分钟")
    print(f"      深睡: {deep_sleep_minutes} 分钟")
    print(f"      浅睡: {light_sleep_minutes} 分钟")
    print(f"      眼动: {rem_minutes} 分钟")
    print(f"    实际值:")
    print(f"      总时间: {actual_awake_minutes + actual_deep_sleep_minutes + actual_light_sleep_minutes + actual_rem_minutes} 分钟")
    print(f"      清醒: {actual_awake_minutes} 分钟")
    print(f"      深睡: {actual_deep_sleep_minutes} 分钟")
    print(f"      浅睡: {actual_light_sleep_minutes} 分钟")
    print(f"      眼动: {actual_rem_minutes} 分钟")
    
    # 验证结果
    print(f"    验证结果:")
    print(f"      清醒阶段: {'✓' if abs(actual_awake_minutes - awake_minutes) <= 1 else '✗'} (差: {abs(actual_awake_minutes - awake_minutes)}分钟)")
    print(f"      深睡阶段: {'✓' if abs(actual_deep_sleep_minutes - deep_sleep_minutes) <= 1 else '✗'} (差: {abs(actual_deep_sleep_minutes - deep_sleep_minutes)}分钟)")
    print(f"      浅睡阶段: {'✓' if abs(actual_light_sleep_minutes - light_sleep_minutes) <= 1 else '✗'} (差: {abs(actual_light_sleep_minutes - light_sleep_minutes)}分钟)")
    print(f"      眼动阶段: {'✓' if abs(actual_rem_minutes - rem_minutes) <= 1 else '✗'} (差: {abs(actual_rem_minutes - rem_minutes)}分钟)")

if __name__ == "__main__":
    test_sleep_stages()
