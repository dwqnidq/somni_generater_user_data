"""文件作用：用于 test fitness data 相关的数据处理或流程支持。"""

import json
import os
import sys
from datetime import datetime, timedelta

# 添加项目根目录到导入路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_health_data import HealthDataGenerator

if __name__ == "__main__":
    # 创建健康数据生成器实例，设置generate_config=False以避免重新生成配置文件
    generator = HealthDataGenerator(generate_config=False)
    
    # 测试体征数据生成
    print("测试体征数据生成...")
    
    # 遍历所有用户，为每个用户生成体征数据
    for user in generator.users:
        user_id = user.get('user_id')
        try:
            # 获取用户配置的日期范围
            date_config = user.get('date', {})
            start_date_str = date_config.get('start', None)
            end_date_str = date_config.get('end', None)
            
            if start_date_str and end_date_str:
                # 解析日期范围
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
                
                print(f"用户 {user_id} 的日期范围：{start_date.strftime('%Y-%m-%d')} 到 {end_date.strftime('%Y-%m-%d')}")
                
                # 测试生成日期范围内的体征数据
                fitness_data = generator.generate_fitness_data(user_id, user, start_date, end_date)
                print(f"已生成用户 {user_id} 的体征数据，共{len(fitness_data)}条")
                
                # 保存体征数据到文件
                output_file = os.path.join(generator.output_dir, f"{user_id}_fitness_data.json")
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(fitness_data, f, ensure_ascii=False, indent=2)
                print(f"体征数据已保存到 {output_file}")
                
                # 打印生成的数据
                print(f"\n用户 {user_id} 的体征数据：")
                print(json.dumps(fitness_data, ensure_ascii=False, indent=2))
            else:
                print(f"用户 {user_id} 没有配置日期范围，跳过生成体征数据")
        except Exception as e:
            print(f"测试体征数据生成时出错：{e}")
            import traceback
            traceback.print_exc()