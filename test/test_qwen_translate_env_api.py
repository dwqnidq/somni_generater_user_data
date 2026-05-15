"""文件作用：烟测 .env 中通义千问（DashScope 兼容模式）配置：TRANSLATE_BASE_URL、TRANSLATE_MODEL_NAME、TRANSLATE_ENABLE_THINKING 与 DASHSCOPE_API_KEY。"""

import os
import sys

import requests
from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _qwen_chat_url() -> str:
    base = (
        os.getenv("TRANSLATE_BASE_URL")
        or os.getenv("QWEN_BASE_URL")
        or os.getenv("DASHSCOPE_BASE_URL")
        or os.getenv("BASE_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")
    return f"{base}/chat/completions"


def _qwen_api_key() -> str | None:
    key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("DOUBAO_API_KEY")
    return key if key else None


def _model_name() -> str:
    return (
        os.getenv("TRANSLATE_MODEL_NAME")
        or os.getenv("QWEN_MODEL_NAME")
        or os.getenv("MODEL_NAME")
        or "qwen-plus"
    )


def main() -> None:
    api_key = _qwen_api_key()
    if not api_key:
        raise ValueError("请设置 DASHSCOPE_API_KEY（或兼容的 DOUBAO_API_KEY）")

    url = _qwen_chat_url()
    model = _model_name()
    enable_thinking = _env_bool("TRANSLATE_ENABLE_THINKING", False)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "用一句话回答：1+1等于几？"}],
        "temperature": 0.3,
        "max_tokens": 128,
        "enable_thinking": enable_thinking,
    }

    timeout = int(os.getenv("QWEN_TIMEOUT_SEC", os.getenv("TRANSLATE_TIMEOUT_SEC", "120")))

    print("测试通义千问（DashScope OpenAI 兼容接口）…")
    print(f"请求 URL: {url}")
    print(f"模型: {model}")
    print(f"enable_thinking: {enable_thinking}")

    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    try:
        data = response.json()
    except Exception:
        print(f"HTTP {response.status_code}，非 JSON 响应正文（前 500 字符）:\n{response.text[:500]}")
        response.raise_for_status()
        return

    if response.status_code >= 400:
        err = data.get("error", {}) if isinstance(data, dict) else {}
        msg = err.get("message", data) if isinstance(err, dict) else data
        print(f"HTTP {response.status_code}，错误: {msg}")
        sys.exit(1)

    if not isinstance(data, dict) or "choices" not in data or not data["choices"]:
        print(f"响应异常（无 choices）: {data}")
        sys.exit(1)

    content = (data["choices"][0].get("message") or {}).get("content") or ""
    content = str(content).strip()
    print("\n模型回复:")
    print(content)
    print("\n测试成功。")


if __name__ == "__main__":
    main()
