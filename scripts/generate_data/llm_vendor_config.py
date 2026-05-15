"""大模型供应方解析：generate_ai / generate_health_data 共用。"""

from __future__ import annotations

import os

_DOUBAO_ALIASES = frozenset({"doubao", "volc", "volces", "ark", "bytedance", "字节"})
_QWEN_ALIASES = frozenset({"qwen", "dashscope", "tongyi", "通义"})


def resolve_llm_vendor() -> str:
    """返回 ``doubao`` 或 ``qwen``。

    显式 ``LLM_VENDOR`` / ``LLM_BACKEND`` 优先；未设置且已配置 ``DOUBAO_API_KEY`` 时默认豆包。
    """
    raw = (os.getenv("LLM_VENDOR") or os.getenv("LLM_BACKEND") or "").strip().lower()
    if raw in _DOUBAO_ALIASES:
        return "doubao"
    if raw in _QWEN_ALIASES:
        return "qwen"
    if (os.getenv("DOUBAO_API_KEY") or "").strip():
        return "doubao"
    return "qwen"


def apply_doubao_vendor_env(*, strict: bool = False) -> bool:
    """将环境固定为火山方舟豆包（``LLM_VENDOR=doubao``）。配置不齐时 strict 则退出进程。"""
    base_url = (os.getenv("BASE_URL") or os.getenv("DOUBAO_BASE_URL") or "").strip()
    api_key = (os.getenv("DOUBAO_API_KEY") or "").strip()
    model_name = (
        os.getenv("DOUBAO_MODEL_NAME") or os.getenv("MODEL_NAME") or ""
    ).strip()
    missing = [
        k
        for k, v in [
            ("BASE_URL/DOUBAO_BASE_URL", base_url),
            ("DOUBAO_API_KEY", api_key),
            ("MODEL_NAME/DOUBAO_MODEL_NAME", model_name),
        ]
        if not v
    ]
    if missing:
        msg = "豆包配置缺失，请在 .env 中配置: " + ", ".join(missing)
        if strict:
            raise SystemExit(msg)
        print(f"[警告] {msg}，LLM 调用将被跳过", file=__import__("sys").stderr)
        return False
    os.environ["LLM_VENDOR"] = "doubao"
    if not (os.getenv("BASE_URL") or "").strip():
        os.environ["BASE_URL"] = base_url
    return True
