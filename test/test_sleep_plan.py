"""文件作用：用于 test sleep plan 相关的数据处理或流程支持。"""

import json
import os
import sys

# 添加项目根目录到导入路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_health_data import HealthDataGenerator

if __name__ == "__main__":
    # 创建健康数据生成器实例，设置generate_config=False以避免重新生成配置文件
    generator = HealthDataGenerator(generate_config=False)
    
    # 测试睡眠方案数据生成
    print("测试睡眠方案数据生成...")
    session_id = "6789abcdef01234567890123"
    uid = "695dddc5852fc8ae2ef0d05a"
    count = 1
    
    try:
        sleep_plan_data = generator.generate_sleep_plan_data(session_id, uid, count)
        print(f"已生成{len(sleep_plan_data)}条睡眠方案数据")
        
        # 保存睡眠方案数据到文件
        output_file = os.path.join(generator.output_dir, f"{uid}_sleep_plan_data.json")
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(sleep_plan_data, f, ensure_ascii=False, indent=2)
        print(f"睡眠方案数据已保存到 {output_file}")
        
        # 打印生成的数据
        print("\n生成的睡眠方案数据：")
        print(json.dumps(sleep_plan_data, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"测试睡眠方案数据生成时出错：{e}")
        import traceback
        traceback.print_exc()