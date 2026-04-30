"""文件作用：用于 clean json data 相关的数据处理或流程支持。"""

import json
import os
import shutil
from datetime import datetime

def clean_json_files():
    """清理output文件夹中的所有JSON文件"""
    output_dir = "output"
    
    # 创建备份文件夹
    backup_dir = os.path.join(output_dir, "backup")
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)
        print(f"创建备份文件夹: {backup_dir}")
    
    # 获取所有JSON文件
    json_files = [f for f in os.listdir(output_dir) if f.endswith('_health_data.json')]
    
    print(f"找到 {len(json_files)} 个JSON文件")
    
    for filename in json_files:
        filepath = os.path.join(output_dir, filename)
        print(f"\n处理文件: {filename}")
        
        try:
            # 读取原始文件
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 备份原始文件
            backup_path = os.path.join(backup_dir, filename)
            shutil.copy2(filepath, backup_path)
            print(f"  已备份到: {backup_path}")
            
            # 处理数据（可能是单个对象或数组）
            if isinstance(data, list):
                processed_data = [process_record(record) for record in data]
            else:
                processed_data = process_record(data)
            
            # 写入处理后的文件
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(processed_data, f, ensure_ascii=False, indent=2)
            
            print(f"  ✓ 文件处理完成")
            
        except Exception as e:
            print(f"  ✗ 处理文件时出错: {e}")

def process_record(record):
    """处理单个记录"""
    # 创建新记录的副本
    new_record = record.copy()
    
    # 删除顶层字段
    fields_to_remove = ['data_source', 'timestamp', 'dimension_type', '__v',]
    for field in fields_to_remove:
        if field in new_record:
            del new_record[field]
    
    # 修改user_id为uid
    if 'user_id' in new_record:
        new_record['uid'] = new_record.pop('user_id')
    
    # 删除raw_data中的指定字段
    if 'raw_data' in new_record:
        raw_data = new_record['raw_data'].copy()
        raw_fields_to_remove = ['leave_bed_minutes', 'leave_bed_count',"turnover_count"]
        for field in raw_fields_to_remove:
            if field in raw_data:
                del raw_data[field]
        new_record['raw_data'] = raw_data
    
    return new_record

if __name__ == "__main__":
    print("=" * 60)
    print("开始清理JSON文件")
    print("=" * 60)
    
    clean_json_files()
    
    print("\n" + "=" * 60)
    print("所有文件处理完成！")
    print("=" * 60)
