"""文件作用：用于 test doubao api 相关的数据处理或流程支持。"""

import os
import requests
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 获取API配置
api_key = os.getenv('DOUBAO_API_KEY')
base_url = os.getenv('BASE_URL', 'https://ark.cn-beijing.volces.com/api/v3')
model_name = os.getenv('MODEL_NAME', 'doubao-seed-2-0-mini-260215')

if not api_key:
    raise ValueError("DOUBAO_API_KEY环境变量未设置")

# 构建请求数据
headers = {
    'Content-Type': 'application/json',
    'Authorization': f'Bearer {api_key}'
}

data = {
    'model': model_name,
    'messages': [
        {
            'role': 'user',
            'content': '你好豆包'
        }
    ],
    'temperature': 0.7,
    'max_tokens': 100,
    'enable_thinking': False,
}

print("测试豆包大模型API...")
print(f"使用模型: {model_name}")
print(f"发送提示词: 你好豆包")

# 发送请求
try:
    response = requests.post(
        f'{base_url}/chat/completions',
        headers=headers,
        json=data,
        timeout=int(os.getenv('DOUBAO_TIMEOUT_SEC', '120')),
    )
    response.raise_for_status()
    result = response.json()
    
    # 提取响应内容
    if 'choices' in result and result['choices']:
        content = result['choices'][0]['message']['content'].strip()
        print(f"\n豆包响应:")
        print(content)
        print("\n测试成功！豆包大模型正常工作。")
    else:
        print("\n错误：响应中没有'choices'字段")
        print(f"完整响应: {result}")
        print("\n测试失败！")
        
except Exception as e:
    print(f"\n测试失败！发生错误: {e}")
    import traceback
    traceback.print_exc()
