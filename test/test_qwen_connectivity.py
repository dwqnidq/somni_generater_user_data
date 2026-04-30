"""最小化千问连通性测试脚本。

用法：
  python test/test_qwen_connectivity.py
"""

import os
import sys

import requests


def main() -> int:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        print("❌ 未检测到 DASHSCOPE_API_KEY，请先在环境变量或 .env 中配置。")
        return 1

    base_url = (
        os.getenv("QWEN_BASE_URL")
        or os.getenv("DASHSCOPE_BASE_URL")
        or os.getenv("TRANSLATE_BASE_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")
    model_name = os.getenv("QWEN_MODEL_NAME") or os.getenv("MODEL_NAME") or "qwen-plus"
    chat_url = f"{base_url}/chat/completions"

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "请只回复：ok"}],
        "temperature": 0,
        "max_tokens": 16,
        "enable_thinking": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        resp = requests.post(chat_url, headers=headers, json=payload, timeout=30)
        data = resp.json()
    except Exception as exc:
        print(f"❌ 请求失败: {exc}")
        return 1

    if resp.status_code >= 400 or "error" in data:
        print(f"❌ 接口报错: HTTP {resp.status_code}, body={data}")
        return 1

    choices = data.get("choices") or []
    if not choices:
        print(f"❌ 返回中没有 choices: {data}")
        return 1

    text = (choices[0].get("message", {}) or {}).get("content", "").strip()
    if not text:
        print(f"❌ 返回内容为空: {data}")
        return 1

    print("✅ 千问连通成功")
    print(f"model: {model_name}")
    print(f"reply: {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
