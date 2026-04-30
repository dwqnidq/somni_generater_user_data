"""文件作用：用于 test sleep report 相关的数据处理或流程支持。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_health_data import generate_sleep_report

# 测试数据
sleep_data = {
    "raw_data": {
        "total_sleep_minutes": 480,
        "deep_sleep_ratio": 20,
        "light_sleep_ratio": 50,
        "rem_ratio": 20,
        "awake_ratio": 10,
        "bed_time": "2026-03-15T22:00:00Z",
        "wake_up_time": "2026-03-16T06:00:00Z",
        "sleep_latency": 20,
        "sleep_efficiency": 90,
        "wake_time": "2026-03-16T05:45:00Z",
        "average_heartbeat": 70,
        "average_respiration": 15,
        "apnea_count": 2,
    },
    "record_date": "2026-03-15",
    "idf_data": [],
}

# 睡眠标准
sleep_standard = {"deep": [15, 25], "light": [45, 60], "rem": [20, 25], "awake": 10}

# 生成睡眠报告
try:
    report = generate_sleep_report(sleep_data, "test_user", "test_session", sleep_standard)
    print("睡眠报告生成成功！")
    print(f"用户ID: {report['uid']}")
    print(f"记录日期: {report['record_date']}")
    print(f"呼吸暂停次数(顶层): {report.get('apnea_count')}")
    print("睡眠摘要:")
    print(f"  身体电量: {report['sleep_summary']['body_battery']}")
    print(f"  总睡眠时间: {report['sleep_summary']['total_minutes']}分钟")
    print(f"  深睡时间: {report['sleep_summary']['deep_sleep_minutes']}分钟")
    print("痛点分析 module (数组):")
    for i, m in enumerate(report["pain_point_analysis"]["module"]):
        print(f"  [{i}] {m.get('target')}: {m.get('summary')}")
    print("  环境摘要: 包含温度、湿度、光照度和噪音数据")
    print("质量分析 module:")
    for i, m in enumerate(report["quality_analysis"]["module"]):
        print(f"  [{i}] {m.get('target')}: {m.get('description', '')[:60]}...")
    print("  睡眠结构:")
    for stage, data in report["quality_analysis"]["sleep_structure"].items():
        print(f"    {stage}: {data['minutes']}分钟 ({data['percent']}%) - {data['status']}")
    print("  睡眠质量:")
    print(f"    卧床时间: {report['quality_analysis']['sleep_quality']['time_in_bed_minutes']}分钟")
    print(
        f"    入睡潜伏期: {report['quality_analysis']['sleep_quality']['sleep_onset_latency_minutes']}分钟"
    )
    print(f"    睡眠效率: {report['quality_analysis']['sleep_quality']['sleep_efficiency']}%")
    print(f"    就寝时间: {report['quality_analysis']['sleep_quality']['bedtime']}")
    print(f"    起床时间: {report['quality_analysis']['sleep_quality']['wake_up_time']}")
    print(
        f"    醒后清醒时间: {report['quality_analysis']['sleep_quality']['awake_after_onset_minutes']}分钟"
    )
    print("  听觉报告:")
    aud = report["quality_analysis"]["auditory"]
    print(f"    target: {aud.get('target')}")
    print(f"    风险提示: {aud.get('risk_alert')}")
    print(f"    module: {aud.get('module')}")
    print(f"    鼾声 data_points: {len(aud.get('snoring_analysis', {}).get('data_points', []))}")
    print(f"    notice: {report.get('notice')}")
except Exception as e:
    print(f"生成睡眠报告时出错: {str(e)}")
    import traceback

    traceback.print_exc()
