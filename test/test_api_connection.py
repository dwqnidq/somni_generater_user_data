"""文件作用：用于 test api connection 相关的数据处理或流程支持。"""

import requests
import json

def test_api_connection():
    """
    测试接口是否畅通
    GET请求，地址为https://bionode-test.fulai.tech/app/quiz/survey
    请求体为 { code: "somni_vip" }
    """
    url = "https://bionode-test.fulai.tech/app/quiz/survey"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "code": "somni_vip"
    }
    
    try:
        response = requests.get(url, headers=headers, json=data)
        print(f"请求状态码: {response.status_code}")
        print(f"响应内容: {response.text}")
        
        # 检查响应状态码
        if response.status_code == 200:
            print("接口测试成功！")
        else:
            print(f"接口测试失败，状态码: {response.status_code}")
            
    except Exception as e:
        print(f"请求异常: {str(e)}")

if __name__ == "__main__":
    test_api_connection()
