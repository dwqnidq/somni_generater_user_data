"""文件作用：用于 test schedule data 相关的数据处理或流程支持。"""

from generate_health_data import HealthDataGenerator
import datetime

# 创建生成器实例
generator = HealthDataGenerator(generate_config=False)

# 从配置文件中获取一个用户
if generator.users:
    user = generator.users[0]
    user_id = user.get('user_id')
    date_config = user.get('date', {})
    start_date_str = date_config.get('start')
    end_date_str = date_config.get('end')
    
    if start_date_str and end_date_str:
        # 解析日期
        start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d')
        
        # 生成日程数据，每天最多3个事件
        schedule_data = generator.generate_schedule_data(user_id, start_date, end_date, 3)
        
        # 按日期分组
        events_by_date = {}
        for event in schedule_data:
            date = event['event_date']
            if date not in events_by_date:
                events_by_date[date] = []
            events_by_date[date].append(event)
        
        # 输出结果
        print(f"用户 {user_id} 的日期范围: {start_date_str} 到 {end_date_str}")
        print(f"生成了 {len(schedule_data)} 个日程事件")
        print("\n每天的事件分布:")
        
        # 遍历日期范围内的每一天
        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime('%Y-%m-%d')
            events = events_by_date.get(date_str, [])
            print(f"  {date_str}: {len(events)} 个事件")
            for event in events:
                print(f"    - {event['start_time']}-{event['end_time']}: {event['event_type']} - {event['event_name']}")
            current_date += datetime.timedelta(days=1)
    else:
        print("用户缺少日期配置")
else:
    print("配置文件中没有用户数据")
