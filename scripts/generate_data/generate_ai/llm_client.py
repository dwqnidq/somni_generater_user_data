"""AI 生成链路共用：读 prompt、调用 OpenAI 兼容 API、解析 JSON（不依赖 generate_health_data）。"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from typing import Any, Optional

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
PROMPT_DIR = os.path.join(PROJECT_ROOT, "prompt")

sleep_report_llm_enabled = False

_QWEN_MAX_CONCURRENCY = max(
    1, int(os.getenv("QWEN_MAX_CONCURRENCY", os.getenv("DOUBAO_MAX_CONCURRENCY", "12")))
)
_QWEN_RETRIES = max(1, int(os.getenv("QWEN_RETRIES", os.getenv("DOUBAO_RETRIES", "3"))))
_QWEN_TIMEOUT = max(
    10, int(os.getenv("QWEN_TIMEOUT_SEC", os.getenv("DOUBAO_TIMEOUT_SEC", "300")))
)
_QWEN_MAX_TOKENS = 10000
_QWEN_SEM = threading.BoundedSemaphore(_QWEN_MAX_CONCURRENCY)


def set_model_switch(enabled: bool) -> None:
    global sleep_report_llm_enabled
    sleep_report_llm_enabled = bool(enabled)


def _llm_vendor() -> str:
    from llm_vendor_config import resolve_llm_vendor

    return resolve_llm_vendor()


def _qwen_chat_url() -> str:
    if _llm_vendor() == "doubao":
        base = (
            os.getenv("DOUBAO_BASE_URL")
            or os.getenv("BASE_URL")
            or "https://ark.cn-beijing.volces.com/api/v3"
        ).rstrip("/")
        return f"{base}/chat/completions"
    base = (
        os.getenv("QWEN_BASE_URL")
        or os.getenv("DASHSCOPE_BASE_URL")
        or os.getenv("TRANSLATE_BASE_URL")
        or os.getenv("BASE_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")
    return f"{base}/chat/completions"


def has_api_key() -> bool:
    return bool(_qwen_api_key())


def _qwen_api_key() -> str:
    if _llm_vendor() == "doubao":
        return os.getenv("DOUBAO_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""
    return os.getenv("DASHSCOPE_API_KEY") or os.getenv("DOUBAO_API_KEY") or ""


def _qwen_model_name() -> str:
    if _llm_vendor() == "doubao":
        return (
            os.getenv("DOUBAO_MODEL_NAME")
            or os.getenv("MODEL_NAME")
            or os.getenv("QWEN_MODEL_NAME")
            or "doubao-seed-2-0-mini-260215"
        )
    return os.getenv("QWEN_MODEL_NAME") or os.getenv("MODEL_NAME") or "qwen-plus"


def _qwen_clamp_max_tokens(max_tokens: Any) -> int:
    try:
        v = int(max_tokens)
    except (TypeError, ValueError):
        v = _QWEN_MAX_TOKENS
    return max(1, min(v, _QWEN_MAX_TOKENS))


def load_prompt_instruction(template_name: str) -> str:
    template_path = os.path.join(PROMPT_DIR, template_name)
    if not os.path.isfile(template_path):
        return ""
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def render_prompt_template(template_name: str, replacements: Optional[dict] = None) -> str:
    template_path = os.path.join(PROMPT_DIR, template_name)
    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()
    lines = content.splitlines()
    while lines and lines[0].startswith("# 来源"):
        lines.pop(0)
    if lines and not lines[0].strip():
        lines.pop(0)
    content = "\n".join(lines)
    if replacements:
        for key, value in replacements.items():
            content = content.replace(f"{{{{{key}}}}}", str(value))
    return content


def parse_json_from_response(content: str) -> Any:
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(content.strip())


def parse_model_json_array(text: Any) -> Optional[list]:
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    if t.startswith("```"):
        lines = t.splitlines()
        if len(lines) >= 2:
            inner = "\n".join(lines[1:])
            if inner.rstrip().endswith("```"):
                inner = inner.rstrip()[:-3].rstrip()
            t = inner.strip()
            if t.lower().startswith("json"):
                t = t[4:].lstrip().strip()
    try:
        parsed = json.loads(t)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    lb = t.find("[")
    rb = t.rfind("]")
    if lb != -1 and rb != -1 and rb > lb:
        try:
            parsed = json.loads(t[lb : rb + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            return None
    return None


def normalize_target_description_modules(items: Any) -> Optional[list]:
    if not isinstance(items, list):
        return None
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        tgt = str(it.get("target") or "").strip()
        desc = str(it.get("description") or "").strip()
        if tgt and desc:
            out.append({"target": tgt, "description": desc})
    return out


def call_qwen_api(
    user_prompt: str,
    system_prompt: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    *,
    top_p: Optional[float] = None,
    enable_thinking: bool = False,
    sleep_report_llm: bool = False,
) -> str:
    if sleep_report_llm:
        if not sleep_report_llm_enabled:
            return ""
    if not _qwen_api_key():
        return ""

    if temperature is None:
        temperature = float(os.getenv("QWEN_TEMPERATURE", os.getenv("DOUBAO_TEMPERATURE", "0.7")))
    temperature = max(0.0, min(1.0, float(temperature)))

    mt = _qwen_clamp_max_tokens(max_tokens)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    req_json: dict = {
        "model": _qwen_model_name(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": mt,
        "enable_thinking": bool(enable_thinking),
    }
    if top_p is not None:
        req_json["top_p"] = max(0.0, min(1.0, float(top_p)))

    last_error = None
    response_data = {}
    for attempt in range(_QWEN_RETRIES):
        try:
            with _QWEN_SEM:
                response = requests.post(
                    _qwen_chat_url(),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {_qwen_api_key()}",
                    },
                    json=req_json,
                    timeout=_QWEN_TIMEOUT,
                )
            response_data = response.json()
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                if attempt < _QWEN_RETRIES - 1:
                    time.sleep((2**attempt) * 0.8 + random.uniform(0, 0.3))
                    continue
            else:
                break
        except Exception as e:
            last_error = str(e)
            if attempt < _QWEN_RETRIES - 1:
                time.sleep((2**attempt) * 0.8 + random.uniform(0, 0.3))
                continue
            print(f"通义千问 API 请求出错: {last_error}")
            return ""
    else:
        print(f"通义千问 API 请求失败: {last_error}")
        return ""

    if "error" in response_data:
        print(f"API错误: {response_data['error'].get('message', '未知错误')}")
        return ""
    if "choices" not in response_data or not response_data["choices"]:
        print("错误：响应中没有'choices'字段")
        return ""
    return response_data["choices"][0]["message"]["content"].strip()


call_doubao_api = call_qwen_api
