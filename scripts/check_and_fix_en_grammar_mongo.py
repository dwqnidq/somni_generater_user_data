"""
检查并修复 MongoDB 中英文数据的语法问题（仅 language=en）。

需求约束：
1) 仅处理指定集合：
   - quiz_personalities
   - quiz_questions
   - somni_ai_insights
   - somni_events
   - somni_reports
   - somni_schedules
2) 仅处理 language == "en" 的文档
3) 逐条字符串调用大模型进行语法检查（one by one）
4) 发现语法问题则更新，没问题跳过
5) 严禁开启思考模式：请求中强制 enable_thinking=False
"""
# 文件作用：用于 check and fix en grammar mongo 相关的数据处理或流程支持。


from __future__ import annotations

import json
import os
import re
import time
import argparse
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv
from pymongo import MongoClient


load_dotenv()
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPT_DIR = os.path.join(PROJECT_ROOT, "prompt")


TARGET_COLLECTIONS = [
    "quiz_personalities",
    "quiz_questions",
    "somni_ai_insights",
    "somni_events",
    "somni_reports",
    "somni_schedules",
]

NO_UID_FILTER_COLLECTIONS = ["quiz_personalities", "quiz_questions"]
UID_FILTER_COLLECTIONS = [c for c in TARGET_COLLECTIONS if c not in NO_UID_FILTER_COLLECTIONS]


# ===== Mongo 配置 =====
DEFAULT_MONGO_URI = "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive"
MONGO_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or DEFAULT_MONGO_URI


def _extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


DB_NAME = os.getenv("MONGODB_DB") or _extract_db_name_from_uri(MONGO_URI, fallback="Fullive")


# ===== LLM 配置（火山方舟 / 豆包，与 .env 中 BASE_URL、DOUBAO_API_KEY、MODEL_NAME 一致）=====
API_KEY = os.getenv("DOUBAO_API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
MODEL_NAME = os.getenv("MODEL_NAME", "doubao-seed-2-0-mini-260215")
TIMEOUT_SEC = int(os.getenv("DOUBAO_TIMEOUT_SEC", os.getenv("QWEN_TIMEOUT_SEC", "120")))
RETRY_TIMES = int(os.getenv("DOUBAO_RETRY_TIMES", os.getenv("QWEN_RETRY_TIMES", "3")))
RETRY_BASE_DELAY_SEC = float(
    os.getenv("DOUBAO_RETRY_BASE_DELAY_SEC", os.getenv("QWEN_RETRY_BASE_DELAY_SEC", "2"))
)

# 按用户要求：严禁开启思考模式（硬编码）
ENABLE_THINKING = False

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}" if API_KEY else "",
}


# ===== 文本筛选 =====
HAS_EN_RE = re.compile(r"[A-Za-z]")
HAS_ZH_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
LIKELY_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"\s+")


def normalize_spaces(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text).strip()


def looks_like_english(text: str) -> bool:
    t = normalize_spaces(text)
    if not t:
        return False
    if len(t) < 4:
        return False
    if len(t) > 2000:
        return False
    if LIKELY_URL_RE.search(t):
        return False
    if HAS_ZH_RE.search(t):
        return False
    return bool(HAS_EN_RE.search(t))


def extract_json_substring(text: str) -> Optional[str]:
    t = (text or "").strip()
    if not t:
        return None
    if (t.startswith("{") and t.endswith("}")) or (t.startswith("[") and t.endswith("]")):
        return t

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t, re.IGNORECASE)
    if fence:
        inner = fence.group(1).strip()
        if inner.startswith("{") and "}" in inner:
            t = inner

    lo = t.find("{")
    ro = t.rfind("}")
    if lo != -1 and ro != -1 and ro > lo:
        return t[lo : ro + 1].strip()
    return None


@dataclass
class TextHit:
    path: str
    value: str


def iter_english_string_hits(obj: Any, base_path: str = "", hits: Optional[list[TextHit]] = None) -> list[TextHit]:
    if hits is None:
        hits = []

    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k)
            if key == "_id":
                continue
            child_path = key if not base_path else f"{base_path}.{key}"
            iter_english_string_hits(v, child_path, hits)
        return hits

    if isinstance(obj, list):
        for i, item in enumerate(obj):
            child_path = f"{base_path}.{i}" if base_path else str(i)
            iter_english_string_hits(item, child_path, hits)
        return hits

    if isinstance(obj, str) and looks_like_english(obj):
        hits.append(TextHit(path=base_path, value=obj))
    return hits


def render_prompt_template(template_name: str, replacements: dict[str, str]) -> str:
    template_path = os.path.join(PROMPT_DIR, template_name)
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


def call_qwen_grammar_check_one(text: str) -> dict:
    """
    单条文本语法检查。
    返回：
    {
      "has_error": bool,
      "issues": [str, ...],
      "suggestion": str
    }
    """
    prompt = render_prompt_template(
        "check_and_fix_en_grammar_mongo__call_qwen_grammar_check_one.md",
        {"TEXT": text},
    )

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "Output JSON only. Do not reveal hidden reasoning."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 512,
        "enable_thinking": ENABLE_THINKING,  # 强制 false
    }

    max_attempts = max(1, RETRY_TIMES)
    last_err: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(
                f"{BASE_URL}/chat/completions",
                headers=HEADERS,
                json=payload,
                timeout=TIMEOUT_SEC,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            json_text = extract_json_substring(content) or content
            parsed = json.loads(json_text)
            if not isinstance(parsed, dict):
                raise ValueError("Model output is not JSON object")
            return {
                "has_error": bool(parsed.get("has_error", False)),
                "issues": parsed.get("issues", []) if isinstance(parsed.get("issues", []), list) else [],
                "suggestion": str(parsed.get("suggestion", "") or ""),
            }
        except Exception as e:
            last_err = e
            if attempt == max_attempts:
                break
            time.sleep(RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1)))

    return {
        "has_error": False,
        "issues": [f"model_error: {last_err}"] if last_err else ["model_error"],
        "suggestion": "",
    }


def process_collection(db, coll_name: str) -> dict:
    coll = db[coll_name]
    cursor = coll.find({"language": "en"})

    total_docs = 0
    total_hits = 0
    checked_hits = 0
    fixed_hits = 0
    updated_docs = 0

    for doc in cursor:
        total_docs += 1
        doc_id = doc.get("_id")
        hits = iter_english_string_hits(doc)
        if not hits:
            continue

        total_hits += len(hits)
        set_updates = {}

        for hit in hits:
            checked_hits += 1
            result = call_qwen_grammar_check_one(hit.value)  # one by one
            if not result.get("has_error"):
                continue
            suggestion = normalize_spaces(str(result.get("suggestion", "")))
            if not suggestion or suggestion == normalize_spaces(hit.value):
                continue
            set_updates[hit.path] = suggestion
            fixed_hits += 1

        if set_updates:
            coll.update_one({"_id": doc_id}, {"$set": set_updates})
            updated_docs += 1

    return {
        "collection": coll_name,
        "user_id": "ALL",
        "total_docs_language_en": total_docs,
        "total_english_strings_found": total_hits,
        "total_english_strings_checked": checked_hits,
        "fixed_strings": fixed_hits,
        "updated_docs": updated_docs,
    }


def process_collection_for_user(db, coll_name: str, user_id: str) -> dict:
    coll = db[coll_name]
    cursor = coll.find({"language": "en", "uid": user_id})

    total_docs = 0
    total_hits = 0
    checked_hits = 0
    fixed_hits = 0
    updated_docs = 0

    for doc in cursor:
        total_docs += 1
        doc_id = doc.get("_id")
        hits = iter_english_string_hits(doc)
        if not hits:
            continue

        total_hits += len(hits)
        set_updates = {}

        for hit in hits:
            checked_hits += 1
            # 大数据量时也按“逐条文本”调用，避免上下文超限
            result = call_qwen_grammar_check_one(hit.value)
            if not result.get("has_error"):
                continue
            suggestion = normalize_spaces(str(result.get("suggestion", "")))
            if not suggestion or suggestion == normalize_spaces(hit.value):
                continue
            set_updates[hit.path] = suggestion
            fixed_hits += 1

        if set_updates:
            coll.update_one({"_id": doc_id}, {"$set": set_updates})
            updated_docs += 1

    return {
        "collection": coll_name,
        "user_id": user_id,
        "total_docs_language_en": total_docs,
        "total_english_strings_found": total_hits,
        "total_english_strings_checked": checked_hits,
        "fixed_strings": fixed_hits,
        "updated_docs": updated_docs,
    }


def _parse_user_ids(raw: str) -> list[str]:
    vals = [x.strip() for x in (raw or "").split(",")]
    return [x for x in vals if x]


def main() -> None:
    parser = argparse.ArgumentParser(description="Check and fix English grammar in MongoDB documents")
    parser.add_argument(
        "--user-ids",
        default="",
        help="可选：按 uid 过滤。多个用英文逗号分隔，如 69a...,69b...",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="并发线程数（用于后续支持 uid 的集合，默认 4）",
    )
    args = parser.parse_args()

    if not API_KEY:
        raise ValueError("缺少 DOUBAO_API_KEY（请在 .env 中配置 BASE_URL、DOUBAO_API_KEY、MODEL_NAME）")

    user_ids = _parse_user_ids(args.user_ids)

    print("Start English grammar check in MongoDB...")
    print(f"DB: {DB_NAME}")
    print(f"Model: {MODEL_NAME}")
    print(f"enable_thinking: {ENABLE_THINKING} (forced)")
    if user_ids:
        print(f"user_ids: {user_ids}")
        print(f"max_workers: {max(1, args.max_workers)}")
    else:
        print("user_ids: ALL (no uid filter)")

    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]

    try:
        reports = []
        existing = set(db.list_collection_names())

        # 1) 优先处理不支持 uid 过滤的集合（串行）
        for coll_name in NO_UID_FILTER_COLLECTIONS:
            if coll_name not in existing:
                print(f"[Skip] collection not found: {coll_name}")
                continue
            print(f"\n[Priority Processing] {coll_name} (no uid filter)")
            r = process_collection(db, coll_name)
            reports.append(r)
            print(
                f"  docs={r['total_docs_language_en']}, "
                f"strings={r['total_english_strings_checked']}, "
                f"fixed={r['fixed_strings']}, updated_docs={r['updated_docs']}"
            )

        # 2) 再处理其余集合
        if user_ids:
            # 其余集合按 (collection, user_id) 粒度并发处理
            tasks = []
            max_workers = max(1, args.max_workers)
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                for coll_name in UID_FILTER_COLLECTIONS:
                    if coll_name not in existing:
                        print(f"[Skip] collection not found: {coll_name}")
                        continue
                    for uid in user_ids:
                        tasks.append(ex.submit(process_collection_for_user, db, coll_name, uid))
                for fu in as_completed(tasks):
                    r = fu.result()
                    reports.append(r)
                    print(
                        f"[Done] {r['collection']} uid={r['user_id']} "
                        f"docs={r['total_docs_language_en']} "
                        f"strings={r['total_english_strings_checked']} "
                        f"fixed={r['fixed_strings']} updated_docs={r['updated_docs']}"
                    )
        else:
            # 不指定用户时，其余集合按全量 language=en 顺序处理
            for coll_name in UID_FILTER_COLLECTIONS:
                if coll_name not in existing:
                    print(f"[Skip] collection not found: {coll_name}")
                    continue
                print(f"\n[Processing] {coll_name}")
                r = process_collection(db, coll_name)
                reports.append(r)
                print(
                    f"  docs={r['total_docs_language_en']}, "
                    f"strings={r['total_english_strings_checked']}, "
                    f"fixed={r['fixed_strings']}, updated_docs={r['updated_docs']}"
                )

        print("\nDone.")
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
