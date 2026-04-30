"""文件作用：用于 test health environment data 相关的数据处理或流程支持。"""

import json
import os
import sys

# 添加项目根目录到导入路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_health_data import HealthDataGenerator

# 测试 HealthDataGenerator 中的环境数据生成功能
def test_health_environment_data():
    """测试 HealthDataGenerator 中的环境数据生成功能"""
    print("开始测试 HealthDataGenerator 中的环境数据生成功能...")
    
    # 创建生成器实例
    generator = HealthDataGenerator()
    
    # 测试用户ID
    test_user_id = "695dddc5852fc8ae2ef0d05a"
    
    # 测试1: 生成单个环境数据
    print("\n测试1: 生成单个环境数据")
    single_data = generator.generate_environment_data(test_user_id)
    
    # 处理single_data可能是列表的情况
    if isinstance(single_data, list) and len(single_data) > 0:
        single_data = single_data[0]
    
    # 验证数据结构
    required_fields = ["uid", "record_date", "collected_at", "temperature", "humidity", "illuminance", "noise", "device_id", "create_time", "update_time"]
    for field in required_fields:
        assert field in single_data, f"缺少字段: {field}"
    
    # 验证字段值
    assert single_data["uid"] == test_user_id, "用户ID不匹配"
    assert 18 <= single_data["temperature"] <= 26, f"温度值不合理: {single_data['temperature']}"
    assert 40 <= single_data["humidity"] <= 60, f"湿度值不合理: {single_data['humidity']}"
    assert single_data["device_id"] == "", "device_id不为空"
    
    print("✓ 单个环境数据生成成功")
    print(f"  示例数据: {json.dumps(single_data, ensure_ascii=False, indent=2)}")
    
    # 测试2: 生成多个环境数据
    print("\n测试2: 生成多个环境数据")
    start_date = "2026-03-01"
    end_date = "2026-03-03"
    multiple_data = generator.generate_multiple_environment_data(test_user_id, start_date, end_date)
    
    # 验证数据数量
    assert len(multiple_data) > 0, "未生成环境数据"
    assert 6 <= len(multiple_data) <= 12, f"生成的数据数量不合理: {len(multiple_data)}"
    
    # 验证每条数据的结构
    for i, data in enumerate(multiple_data):
        for field in required_fields:
            assert field in data, f"第{i+1}条数据缺少字段: {field}"
        assert data["uid"] == test_user_id, f"第{i+1}条数据用户ID不匹配"
    
    print(f"✓ 多个环境数据生成成功 (共{len(multiple_data)}条)")
    
    # 测试3: 保存环境数据
    print("\n测试3: 保存环境数据")
    generator.save_environment_data(test_user_id, multiple_data)
    
    # 验证文件是否存在
    file_path = os.path.join(generator.output_dir, f"{test_user_id}_environment_data.json")
    assert os.path.exists(file_path), f"文件未保存: {file_path}"
    
    # 验证文件内容
    with open(file_path, 'r', encoding='utf-8') as f:
        saved_data = json.load(f)
    
    assert len(saved_data) == len(multiple_data), "保存的数据数量不匹配"
    
    print(f"✓ 环境数据保存成功: {file_path}")
    
    print("\n🎉 所有测试通过！")

if __name__ == "__main__":
    test_health_environment_data()
