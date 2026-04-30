"""文件作用：用于 test modification 相关的数据处理或流程支持。"""

import json
import sys
import os

# 添加当前目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from generate_sleep_report import generate_sleep_report

# 测试数据
sleep_data = {
    'raw_data': {
        'total_sleep_minutes': 480,
        'deep_sleep_ratio': 20,
        'light_sleep_ratio': 50,
        'rem_ratio': 20,
        'awake_ratio': 10,
        'bed_time': '2026-03-15T22:00:00Z',
        'wake_up_time': '2026-03-16T06:00:00Z',
        'sleep_latency': 20,
        'sleep_efficiency': 90,
        'wake_time': '2026-03-16T05:45:00Z',
        'average_heartbeat': 70,
        'average_respiration': 15,
        'apnea_count': 2
    },
    'record_date': '2026-03-15'
}

# 睡眠标准
sleep_standard = {
    "deep": [15, 25],
    "light": [45, 60],
    "rem": [20, 25],
    "awake": 10
}

# 生成睡眠报告
try:
    report = generate_sleep_report(sleep_data, 'test_user', 'test_session', sleep_standard)
    print("睡眠报告生成成功！")
    print(f"报告ID: {report['_id']}")
    print(f"用户ID: {report['uid']}")
    print(f"记录日期: {report['record_date']}")
    print("睡眠摘要:")
    print(f"  身体电量: {report['sleep_summary']['body_battery']}")
    print(f"  总睡眠时间: {report['sleep_summary']['total_minutes']}分钟")
    print(f"  深睡时间: {report['sleep_summary']['deep_sleep_minutes']}分钟")
    
    # 检查是否存在light_sleep_minutes和rem_sleep_minutes字段
    if 'light_sleep_minutes' in report['sleep_summary']:
        print("  ❌ 错误：light_sleep_minutes字段仍然存在")
    else:
        print("  ✅ 正确：light_sleep_minutes字段已移除")
    
    if 'rem_sleep_minutes' in report['sleep_summary']:
        print("  ❌ 错误：rem_sleep_minutes字段仍然存在")
    else:
        print("  ✅ 正确：rem_sleep_minutes字段已移除")
    
    print("\n完整的sleep_summary结构:")
    print(json.dumps(report['sleep_summary'], ensure_ascii=False, indent=2))
    
except Exception as e:
    print(f"生成睡眠报告时出错: {str(e)}")
    import traceback
    traceback.print_exc()
