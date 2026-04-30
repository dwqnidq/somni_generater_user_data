"""文件作用：用于 test sleep events 相关的数据处理或流程支持。"""

from generate_health_data import generate_sleep_events
import datetime

# 测试数据
sleep_data = {
    'raw_data': {
        'sleep_time': '2026-03-15T23:00:00Z',
        'wake_time': '2026-03-16T07:00:00Z'
    }
}

# 生成睡眠事件
events = generate_sleep_events(sleep_data, 'test_user', '2026-03-15')

# 输出结果
print(f'生成了 {len(events)} 个睡眠事件')
for event in events:
    print(f'事件类型: {event["event_type"]}, 时间: {event["event_timestamp"]}, 详情: {event["detail"]}')
