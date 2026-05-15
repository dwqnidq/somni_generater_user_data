"""豆包（火山方舟 OpenAI 兼容）：同一提示词下，不同 temperature 与 top_p 的输出对比。

依赖 `.env` 中常见三项（与仓库内其它豆包脚本一致）：
- `BASE_URL`：如 `https://ark.cn-beijing.volces.com/api/v3`
- `DOUBAO_API_KEY`
- `MODEL_NAME`

运行：
- 打印全部组合：`python test/test_model_temperature_outputs.py`
- 联网烟测（需 `SOMNI_RUN_NETWORK_LLM_TESTS=1`）：`python test/test_model_temperature_outputs.py --unittest`
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from itertools import product
from typing import Any

import requests
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

# ---------------------------------------------------------------------------
# 可调：温度列表、top_p 列表、提示词、解码长度
# ---------------------------------------------------------------------------
TEMPERATURES_FOR_LLM_TEST: tuple[float, ...] = (0.0, 0.35, 0.7, 1.0)
# None 表示请求体不传 top_p，由服务端默认；也可写 0.0–1.0
TOP_P_VALUES_FOR_LLM_TEST: tuple[float | None, ...] = (None, 0.5, 0.95)

USER_PROMPT = (
    "用一个比喻描述「刚睡醒」的感觉，不超过40个汉字；"
    "只输出这一句，不要解释、不要前后缀。"
)
SYSTEM_PROMPT = "你只按要求输出中文正文，不要加引号或 Markdown。"

MAX_TOKENS_FOR_LLM_TEST = 128
# False = 关闭深度思考（请求体 enable_thinking；本脚本内写死为关，勿依赖改此常量传参）
ENABLE_THINKING = False

# unittest 只跑少量组合，减少费用
TEMPERATURES_FOR_UNITTEST: tuple[float, ...] = (0.0, 0.7)
TOP_P_VALUES_FOR_UNITTEST: tuple[float | None, ...] = (None, 0.9)
MAX_TOKENS_FOR_UNITTEST = 64


def _doubao_base_url() -> str:
    return (
        os.getenv("BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    )


def _doubao_chat_url() -> str:
    return f"{_doubao_base_url()}/chat/completions"


def _doubao_api_key() -> str | None:
    key = os.getenv("DOUBAO_API_KEY")
    return key if key else None


def _doubao_model_name() -> str:
    return os.getenv("MODEL_NAME", "doubao-seed-2-0-mini-260215")


def _doubao_timeout_sec() -> int:
    return max(10, int(os.getenv("DOUBAO_TIMEOUT_SEC", "120")))


def call_doubao_chat(
    *,
    user_prompt: str,
    system_prompt: str | None,
    temperature: float,
    top_p: float | None,
    max_tokens: int,
) -> str:
    """POST 方舟 chat/completions，成功返回正文，失败返回空串并在 stderr 打简要原因。"""
    api_key = _doubao_api_key()
    if not api_key:
        print("未设置 DOUBAO_API_KEY。", file=sys.stderr)
        return ""
    url = _doubao_chat_url()
    model = _doubao_model_name()
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": max(0.0, min(1.0, float(temperature))),
        "max_tokens": max(1, int(max_tokens)),
        # 关闭深度思考（enable_thinking；由上方 ENABLE_THINKING 控制，默认 False）
        "enable_thinking": bool(ENABLE_THINKING),
    }
    if top_p is not None:
        body["top_p"] = max(0.0, min(1.0, float(top_p)))

    try:
        r = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json=body,
            timeout=_doubao_timeout_sec(),
        )
        data = r.json()
    except Exception as e:
        print(f"请求异常: {e}", file=sys.stderr)
        return ""

    if r.status_code >= 400:
        err = data.get("error", data) if isinstance(data, dict) else data
        print(f"HTTP {r.status_code}: {err}", file=sys.stderr)
        return ""

    if not isinstance(data, dict) or "choices" not in data or not data["choices"]:
        print(f"响应无 choices: {json.dumps(data, ensure_ascii=False)[:500]}", file=sys.stderr)
        return ""

    msg = data["choices"][0].get("message") or {}
    content = msg.get("content") or ""
    return str(content).strip()


def collect_outputs_matrix(
    *,
    temperatures: tuple[float, ...] | None = None,
    top_p_values: tuple[float | None, ...] | None = None,
    user_prompt: str | None = None,
    system_prompt: str | None = None,
    max_tokens: int | None = None,
) -> list[tuple[float, float | None, str]]:
    """同一用户提示词，遍历 (temperature, top_p) 组合，返回列表 (t, p, text)。"""
    temps = temperatures if temperatures is not None else TEMPERATURES_FOR_LLM_TEST
    ps = top_p_values if top_p_values is not None else TOP_P_VALUES_FOR_LLM_TEST
    up = user_prompt if user_prompt is not None else USER_PROMPT
    sp = system_prompt if system_prompt is not None else SYSTEM_PROMPT
    mt = max_tokens if max_tokens is not None else MAX_TOKENS_FOR_LLM_TEST
    rows: list[tuple[float, float | None, str]] = []
    for t, p in product(temps, ps):
        text = call_doubao_chat(
            user_prompt=up,
            system_prompt=sp,
            temperature=float(t),
            top_p=p,
            max_tokens=mt,
        )
        rows.append((float(t), p, text))
    return rows


def _has_doubao_credentials() -> bool:
    return bool(_doubao_api_key())


def _run_network_llm_tests() -> bool:
    return os.getenv("SOMNI_RUN_NETWORK_LLM_TESTS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _print_temperature_top_p_matrix() -> None:
    if not _has_doubao_credentials():
        print("未设置 DOUBAO_API_KEY，退出。")
        sys.exit(1)
    print(f"URL: {_doubao_chat_url()}", flush=True)
    print(f"模型: {_doubao_model_name()}", flush=True)
    print(
        f"深度思考: {'开启' if ENABLE_THINKING else '关闭'} (enable_thinking={ENABLE_THINKING})",
        flush=True,
    )
    print(f"temperature: {TEMPERATURES_FOR_LLM_TEST}", flush=True)
    print(f"top_p: {TOP_P_VALUES_FOR_LLM_TEST}（None=不传该字段）", flush=True)
    print(flush=True)
    print("用户提示词:", flush=True)
    print(USER_PROMPT, flush=True)
    print(flush=True)
    for t, p, text in collect_outputs_matrix():
        p_label = "不传" if p is None else repr(p)
        print(f"=== temperature={t}  top_p={p_label} ===", flush=True)
        print("模型输出:", flush=True)
        print(text or "(空)", flush=True)
        print(flush=True)


@unittest.skipUnless(
    _has_doubao_credentials() and _run_network_llm_tests(),
    "需要 DOUBAO_API_KEY 且设置 SOMNI_RUN_NETWORK_LLM_TESTS=1",
)
class TestDoubaoTemperatureTopP(unittest.TestCase):
    def test_matrix_subset_non_empty(self):
        print(
            f"深度思考: {'开启' if ENABLE_THINKING else '关闭'} (enable_thinking={ENABLE_THINKING})",
            flush=True,
        )
        for t, p, text in collect_outputs_matrix(
            temperatures=TEMPERATURES_FOR_UNITTEST,
            top_p_values=TOP_P_VALUES_FOR_UNITTEST,
            max_tokens=MAX_TOKENS_FOR_UNITTEST,
        ):
            p_label = "不传" if p is None else repr(p)
            print(f"=== [unittest] temperature={t}  top_p={p_label} ===", flush=True)
            print("模型输出:", flush=True)
            print(text or "(空)", flush=True)
            print(flush=True)
            self.assertIsInstance(text, str)
            self.assertGreater(
                len(text),
                0,
                f"temperature={t} top_p={p!r} 时返回为空",
            )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--unittest", "-u"):
        del sys.argv[1]
        unittest.main()
    elif len(sys.argv) > 1 and sys.argv[1] in ("--print", "-p"):
        _print_temperature_top_p_matrix()
    else:
        _print_temperature_top_p_matrix()
