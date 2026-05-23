"""翻译脚本共用：逐条多轮翻译 + 模型校验，通过后才写入结果。"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.getenv("DOUBAO_API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
MODEL_NAME = os.getenv("MODEL_NAME", "doubao-seed-2-0-mini-260215")
TRANSLATE_PASSES = int(os.getenv("TRANSLATE_PASSES", "2"))
MAX_VERIFY_ROUNDS = int(os.getenv("TRANSLATE_MAX_VERIFY_ROUNDS", "3"))
TIMEOUT_SEC = int(os.getenv("TRANSLATE_TIMEOUT_SEC", "120"))
RETRY_TIMES = int(os.getenv("TRANSLATE_RETRY_TIMES", "3"))
RETRY_BASE_DELAY_SEC = float(os.getenv("TRANSLATE_RETRY_BASE_DELAY_SEC", "2"))

PROMPT_DIR = PROJECT_ROOT / "prompt"
DEFAULT_CACHE_FILE = PROJECT_ROOT / "output" / ".translation_verified_cache.json"

HAS_CHINESE_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
VERDICT_OK_RE = re.compile(r"^VERDICT:\s*OK\s*$", re.IGNORECASE | re.MULTILINE)
VERDICT_RETRY_RE = re.compile(r"^VERDICT:\s*RETRY\s*$", re.IGNORECASE | re.MULTILINE)
FINAL_RE = re.compile(r"^FINAL:\s*(.+)$", re.IGNORECASE | re.MULTILINE)

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}",
}


@dataclass
class VerifyResult:
    ok: bool
    final: str = ""
    reason: str = ""


def has_chinese(text: str) -> bool:
    return bool(HAS_CHINESE_RE.search(text))


def collect_chinese_strings(obj: Any, out: list[str]) -> None:
    if isinstance(obj, str):
        if has_chinese(obj):
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_chinese_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_chinese_strings(v, out)


def replace_strings(obj: Any, mapping: dict[str, str]) -> Any:
    if isinstance(obj, str):
        return mapping.get(obj, obj)
    if isinstance(obj, dict):
        return {k: replace_strings(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [replace_strings(v, mapping) for v in obj]
    return obj


def render_prompt_template(template_name: str, replacements: dict[str, str]) -> str:
    template_path = PROMPT_DIR / template_name
    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()
    lines = content.splitlines()
    while lines and lines[0].startswith("# 来源"):
        lines.pop(0)
    if lines and not lines[0].strip():
        lines.pop(0)
    content = "\n".join(lines)
    for key, value in replacements.items():
        content = content.replace(f"{{{{{key}}}}}", str(value))
    return content


def call_model(prompt: str, *, temperature: float = 0.1) -> str:
    if not API_KEY:
        raise ValueError("DOUBAO_API_KEY 未设置，无法执行英文翻译")

    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": 1024,
        "enable_thinking": False,
    }

    last_error: Exception | None = None
    for attempt in range(1, max(1, RETRY_TIMES) + 1):
        try:
            response = requests.post(
                f"{BASE_URL.rstrip('/')}/chat/completions",
                headers=HEADERS,
                json=payload,
                timeout=TIMEOUT_SEC,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
        except requests.RequestException as e:
            last_error = e
            if attempt >= RETRY_TIMES:
                raise RuntimeError(f"模型请求失败（已重试 {RETRY_TIMES} 次）: {e}") from e
            delay = RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1))
            print(f"    [重试] {delay:.1f}s 后重试: {e}")
            time.sleep(delay)
    raise RuntimeError(f"模型请求失败: {last_error}")


def _normalize_translation(text: str) -> str:
    s = text.strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    return s


def translate_once(source: str, *, translate_template: str = "translate_single__translate.md") -> str:
    prompt = render_prompt_template(
        translate_template,
        {"TEXT": source},
    )
    return _normalize_translation(call_model(prompt, temperature=0.2))


def _parse_verify_response(content: str) -> VerifyResult:
    if VERDICT_OK_RE.search(content):
        m = FINAL_RE.search(content)
        if not m:
            return VerifyResult(ok=False, reason="校验通过但缺少 FINAL 行")
        final = _normalize_translation(m.group(1))
        if has_chinese(final):
            return VerifyResult(ok=False, reason="FINAL 仍含中文")
        return VerifyResult(ok=True, final=final)

    if VERDICT_RETRY_RE.search(content):
        reason_m = re.search(r"^REASON:\s*(.+)$", content, re.IGNORECASE | re.MULTILINE)
        reason = reason_m.group(1).strip() if reason_m else "未说明"
        return VerifyResult(ok=False, reason=reason)

    return VerifyResult(ok=False, reason="无法解析校验回复")


def verify_candidates(source: str, candidates: list[str]) -> VerifyResult:
    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(candidates))
    prompt = render_prompt_template(
        "translate_single__verify.md",
        {
            "SOURCE": source,
            "CANDIDATE_COUNT": str(len(candidates)),
            "CANDIDATES": numbered,
        },
    )
    content = call_model(prompt, temperature=0.0)
    return _parse_verify_response(content)


def translate_string_with_verification(
    source: str,
    *,
    translate_passes: int = TRANSLATE_PASSES,
    max_verify_rounds: int = MAX_VERIFY_ROUNDS,
    translate_template: str = "translate_single__translate.md",
) -> str:
    """
    对单条中文：独立翻译 translate_passes 次，再调用模型校验；
    未通过则重新翻译，直至通过或超过 max_verify_rounds。
    """
    passes = max(2, translate_passes)
    rounds = max(1, max_verify_rounds)

    for round_idx in range(1, rounds + 1):
        candidates: list[str] = []
        for pass_idx in range(1, passes + 1):
            print(f"    第 {round_idx} 轮 · 翻译 {pass_idx}/{passes}")
            candidates.append(translate_once(source, translate_template=translate_template))

        print(f"    第 {round_idx} 轮 · 校验")
        verdict = verify_candidates(source, candidates)
        if verdict.ok:
            print(f"    ✓ 校验通过")
            return verdict.final

        print(f"    ✗ 校验未通过: {verdict.reason}")

    raise RuntimeError(
        f"超过最大校验轮数 ({rounds})，仍未通过: {source[:80]}{'…' if len(source) > 80 else ''}"
    )


def load_translation_cache(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_translation_cache(path: Path, cache: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def build_verified_translation_map(
    unique_chinese: list[str],
    cache: dict[str, str],
    *,
    translate_passes: int = TRANSLATE_PASSES,
    max_verify_rounds: int = MAX_VERIFY_ROUNDS,
    use_cache: bool = True,
    translate_template: str = "translate_single__translate.md",
) -> dict[str, str]:
    translation_map: dict[str, str] = {}
    total = len(unique_chinese)

    for idx, source in enumerate(unique_chinese, start=1):
        preview = source if len(source) <= 48 else source[:48] + "…"
        if use_cache and source in cache:
            translation_map[source] = cache[source]
            print(f"  [{idx}/{total}] 缓存命中: {preview}")
            continue

        print(f"  [{idx}/{total}] {preview}")
        final = translate_string_with_verification(
            source,
            translate_passes=translate_passes,
            max_verify_rounds=max_verify_rounds,
            translate_template=translate_template,
        )
        translation_map[source] = final
        cache[source] = final

    return translation_map


def set_language_on_docs(docs: list[dict[str, Any]], language: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for d in docs:
        item = dict(d)
        item["language"] = language
        out.append(item)
    return out


def translate_document_list(
    docs: list[dict[str, Any]],
    cache: dict[str, str],
    *,
    target_language: str = "en",
    translate_passes: int = TRANSLATE_PASSES,
    max_verify_rounds: int = MAX_VERIFY_ROUNDS,
    use_cache: bool = True,
    translate_template: str = "translate_single__translate.md",
) -> list[dict[str, Any]]:
    chinese_strings: list[str] = []
    collect_chinese_strings(docs, chinese_strings)
    unique_chinese = list(dict.fromkeys(chinese_strings))

    if not unique_chinese:
        return set_language_on_docs(docs, target_language)

    print(
        f"  待翻译 {len(unique_chinese)} 条（去重）；"
        f"每条 {translate_passes} 次翻译 + 最多 {max_verify_rounds} 轮校验"
    )
    translation_map = build_verified_translation_map(
        unique_chinese,
        cache,
        translate_passes=translate_passes,
        max_verify_rounds=max_verify_rounds,
        use_cache=use_cache,
        translate_template=translate_template,
    )
    translated = replace_strings(docs, translation_map)
    if not isinstance(translated, list):
        raise TypeError("翻译结果应为列表")
    return set_language_on_docs(translated, target_language)
