"""
处理 quiz_personalities 集合并生成多语言 JSON：
1) 为集合中所有文档写入 language=zh
2) 查询中文数据输出到 output/quiz_personalities_zh.json
3) 将中文内容翻译为英文，并为每条文档写入 language=en
4) 输出到 output/quiz_personalities_en.json
"""
# 文件作用：用于 generate quiz personalities language json 相关的数据处理或流程支持。


from __future__ import annotations

import json
import os
import sys
import re
from typing import Any

import requests
from dotenv import load_dotenv
from pymongo import MongoClient

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

load_dotenv()

MONGO_URI = os.getenv(
    "MONGODB_URI",
    "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive",
)
DB_NAME = "Fullive"
COLLECTION = "quiz_personalities"

OUT_DIR = "output"
ZH_OUTPUT_FILE = os.path.join(OUT_DIR, "quiz_personalities_zh.json")
EN_OUTPUT_FILE = os.path.join(OUT_DIR, "quiz_personalities_en.json")

API_KEY = os.getenv("DOUBAO_API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
MODEL_NAME = os.getenv("MODEL_NAME", "doubao-seed-2-0-mini-250415")

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}",
}
PROMPT_DIR = os.path.join(PROJECT_ROOT, "prompt")

HAS_CHINESE_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def has_chinese(text: str) -> bool:
    return bool(HAS_CHINESE_RE.search(text))


def to_jsonable_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return json.loads(json.dumps(docs, default=str))


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


def batch_translate(texts: list[str]) -> list[str]:
    if not texts:
        return []
    if not API_KEY:
        raise ValueError("DOUBAO_API_KEY 未设置，无法执行英文翻译")

    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    prompt = render_prompt_template(
        "generate_quiz_personalities_language_json__batch_translate.md",
        {"NUMBERED_INPUTS": numbered},
    )

    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 4096,
        "enable_thinking": False,
    }
    response = requests.post(
        f"{BASE_URL}/chat/completions",
        headers=HEADERS,
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"].strip()

    parsed: dict[int, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\.\s*(.*)$", line)
        if not m:
            continue
        idx = int(m.group(1)) - 1
        parsed[idx] = m.group(2).strip()

    return [parsed.get(i, texts[i]) for i in range(len(texts))]


def set_top_level_language(docs: list[dict[str, Any]], language: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for d in docs:
        item = dict(d)
        item["language"] = language
        out.append(item)
    return out


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    db = client[DB_NAME]
    coll = db[COLLECTION]

    # 1) 写入 zh 语言标记（按你的要求对集合所有数据覆盖为 zh）
    update_result = coll.update_many({}, {"$set": {"language": "zh"}})
    print(
        f"已写入 language=zh，matched={update_result.matched_count}, "
        f"modified={update_result.modified_count}"
    )

    # 2) 查询并导出中文文档
    zh_docs_raw = list(coll.find({}))
    zh_docs = set_top_level_language(to_jsonable_docs(zh_docs_raw), "zh")
    with open(ZH_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(zh_docs, f, ensure_ascii=False, indent=2)
    print(f"已输出中文文件: {ZH_OUTPUT_FILE}（{len(zh_docs)} 条）")

    if not zh_docs:
        print("集合为空，跳过英文翻译输出")
        client.close()
        return

    # 3) 翻译英文
    chinese_strings: list[str] = []
    collect_chinese_strings(zh_docs, chinese_strings)
    unique_chinese = list(dict.fromkeys(chinese_strings))
    print(f"检测到需翻译中文字符串 {len(unique_chinese)} 条")

    translation_map: dict[str, str] = {}
    batch_size = 50
    for i in range(0, len(unique_chinese), batch_size):
        batch = unique_chinese[i : i + batch_size]
        print(f"翻译中: {i + 1}-{i + len(batch)}")
        translated = batch_translate(batch)
        for src, dst in zip(batch, translated):
            translation_map[src] = dst

    en_docs = replace_strings(zh_docs, translation_map)
    en_docs = set_top_level_language(en_docs, "en")

    # 4) 输出英文文件
    with open(EN_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(en_docs, f, ensure_ascii=False, indent=2)
    print(f"已输出英文文件: {EN_OUTPUT_FILE}（{len(en_docs)} 条）")

    client.close()


if __name__ == "__main__":
    main()
