"""
按 QUIZ_PERSONALITY_OBJECT_ID_PAIRS：用键（hex）作为 _id 查询 quiz_questions，
翻译中文后把文档 _id 改为对应「值」，结果写入 output 目录下的 JSON。
"""
# 文件作用：用于 translate quiz questions 相关的数据处理或流程支持。


import json
import os
import re
import requests
from bson import ObjectId
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv('MONGODB_URI', 'mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive')
DB_NAME = 'Fullive'
COLLECTION = 'quiz_questions'
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(_SCRIPT_DIR, 'output')
OUTPUT_JSON = 'quiz_questions_translated_by_id_pairs.json'
BATCH_SIZE = 50

API_KEY = os.getenv('DOUBAO_API_KEY')
BASE_URL = os.getenv('BASE_URL', 'https://ark.cn-beijing.volces.com/api/v3')
MODEL_NAME = os.getenv('MODEL_NAME', 'doubao-seed-2-0-mini-260215')
DOUBAO_TIMEOUT_SEC = int(os.getenv('DOUBAO_TIMEOUT_SEC', '120'))

HEADERS = {
    'Content-Type': 'application/json',
    'Authorization': f'Bearer {API_KEY}'
}

HAS_CHINESE_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')
PROMPT_DIR = os.path.join(_SCRIPT_DIR, "prompt")

# insert_quiz_personalities_en_mongosh.js：L63-96 与 L99-132 两段 ObjectId 按行一一对应（同下标为一组）。
QUIZ_PERSONALITY_OBJECT_ID_PAIRS: list[tuple[str, str]] = [
    ("69b10cc516d7472aedf6bb6f", "69cf9d96fa2ec8a96251f32f"),
    ("69b10cc516d7472aedf6bb70", "69cf9d96fa2ec8a96251f330"),
    ("69b10cc516d7472aedf6bb71", "69d7d0089e02dc67856453cf"),
    ("69b10cc516d7472aedf6bb72", "69cf9d96fa2ec8a96251f332"),
    ("69b10cc516d7472aedf6bb73", "69d7d0089e02dc67856453d1"),
    ("69b10cc516d7472aedf6bb74", "69cf9d96fa2ec8a96251f334"),
    ("69b10cc516d7472aedf6bb75", "69cf9d96fa2ec8a96251f335"),
    ("69b10cc516d7472aedf6bb76", "69cf9d96fa2ec8a96251f336"),
    ("69b10cc516d7472aedf6bb77", "69cf9d96fa2ec8a96251f337"),
    ("69b10cc516d7472aedf6bb78", "69cf9d96fa2ec8a96251f338"),
    ("69b10cc516d7472aedf6bb79", "69cf9d96fa2ec8a96251f339"),
    ("69b10cc516d7472aedf6bb7a", "69cf9d96fa2ec8a96251f33a"),
    ("69b10cc516d7472aedf6bb7b", "69d7d0089e02dc67856453d9"),
    ("69b10cc516d7472aedf6bb7c", "69cf9d96fa2ec8a96251f33c"),
    ("69b10cc516d7472aedf6bb7d", "69d7d0089e02dc67856453db"),
    ("69b10cc516d7472aedf6bb7e", "69cf9d96fa2ec8a96251f33e"),
    ("69b10cc516d7472aedf6bb7f", "69cf9d96fa2ec8a96251f33f"),
    ("69b223eb7fa9cc1c5a1d733c", "69cf9d96fa2ec8a96251f340"),
    ("69b224757fa9cc1c5a1d734b", "69cf9d96fa2ec8a96251f341"),
    ("69b225077fa9cc1c5a1d7369", "69cf9d96fa2ec8a96251f342"),
    ("69b2253d7fa9cc1c5a1d736d", "69cf9d96fa2ec8a96251f343"),
    ("69b225697fa9cc1c5a1d7381", "69cf9d96fa2ec8a96251f344"),
    ("69b2259f7fa9cc1c5a1d7385", "69cf9d96fa2ec8a96251f345"),
    ("69b225f97fa9cc1c5a1d7389", "69cf9d96fa2ec8a96251f346"),
    ("69b226367fa9cc1c5a1d738d", "69cf9d96fa2ec8a96251f347"),
    ("69b2267c7fa9cc1c5a1d7391", "69cf9d96fa2ec8a96251f348"),
    ("69b226fa7fa9cc1c5a1d739e", "69cf9d96fa2ec8a96251f349"),
    ("69b2275e7fa9cc1c5a1d73ad", "69cf9d96fa2ec8a96251f34a"),
    ("69b227f87fa9cc1c5a1d73b3", "69cf9d96fa2ec8a96251f34b"),
    ("69b2281f7fa9cc1c5a1d73b7", "69cf9d96fa2ec8a96251f34c"),
    ("69b2288e7fa9cc1c5a1d73c1", "69cf9d96fa2ec8a96251f34d"),
    ("69b228da7fa9cc1c5a1d73dd", "69cf9d96fa2ec8a96251f34e"),
    ("69b229257fa9cc1c5a1d73e9", "69cf9d96fa2ec8a96251f34f"),
    ("69b2295c7fa9cc1c5a1d73ed", "69cf9d96fa2ec8a96251f350"),
]


def has_chinese(text: str) -> bool:
    return bool(HAS_CHINESE_RE.search(text))


def render_prompt_template(template_name: str, replacements: dict[str, str]) -> str:
    template_path = os.path.join(PROMPT_DIR, template_name)
    with open(template_path, 'r', encoding='utf-8') as f:
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


def batch_translate(texts: list) -> list:
    """批量翻译中文字符串列表，返回对应的英文列表"""
    if not texts:
        return []

    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    prompt = render_prompt_template(
        'translate_quiz_questions__batch_translate.md',
        {"NUMBERED_INPUTS": numbered},
    )

    data = {
        'model': MODEL_NAME,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.1,
        'max_tokens': 4096,
        'enable_thinking': False,
    }

    response = requests.post(
        f'{BASE_URL}/chat/completions', headers=HEADERS, json=data, timeout=DOUBAO_TIMEOUT_SEC
    )
    response.raise_for_status()
    content = response.json()['choices'][0]['message']['content'].strip()

    translations = {}
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r'^(\d+)\.\s*(.*)', line)
        if match:
            idx = int(match.group(1)) - 1
            translations[idx] = match.group(2).strip()

    return [translations.get(i, texts[i]) for i in range(len(texts))]


def collect_chinese_strings(obj, results: list):
    if isinstance(obj, str):
        if has_chinese(obj):
            results.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_chinese_strings(v, results)
    elif isinstance(obj, list):
        for item in obj:
            collect_chinese_strings(item, results)


def replace_chinese_strings(obj, translation_map: dict):
    if isinstance(obj, str):
        return translation_map.get(obj, obj)
    elif isinstance(obj, dict):
        return {k: replace_chinese_strings(v, translation_map) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [replace_chinese_strings(item, translation_map) for item in obj]
    return obj


def set_language_en(obj):
    """递归将所有 language 字段的值改为 'en'"""
    if isinstance(obj, dict):
        return {k: ('en' if k == 'language' else set_language_en(v)) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [set_language_en(item) for item in obj]
    return obj


def main():
    if not API_KEY:
        raise ValueError("DOUBAO_API_KEY 未设置")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, OUTPUT_JSON)

    print(f"连接数据库，按 QUIZ_PERSONALITY_OBJECT_ID_PAIRS 查询 {COLLECTION}...")
    client = MongoClient(MONGO_URI)
    coll = client[DB_NAME][COLLECTION]

    rows: list[tuple[str, str, dict]] = []
    missing_keys: list[str] = []
    for key_hex, value_hex in QUIZ_PERSONALITY_OBJECT_ID_PAIRS:
        doc = coll.find_one({'_id': ObjectId(key_hex)})
        if not doc:
            missing_keys.append(key_hex)
            continue
        rows.append((key_hex, value_hex, doc))

    client.close()

    if missing_keys:
        print(f"警告：以下键作为 _id 在 {COLLECTION} 中不存在（已跳过 {len(missing_keys)} 条）：")
        for k in missing_keys:
            print(f"  - {k}")

    if not rows:
        print("没有可翻译的数据，退出")
        return

    payloads: list[tuple[str, str, dict]] = []
    for key_hex, value_hex, doc in rows:
        as_dict = json.loads(json.dumps(doc, default=str))
        payloads.append((key_hex, value_hex, as_dict))

    chinese_strings: list[str] = []
    for _, _, d in payloads:
        collect_chinese_strings(d, chinese_strings)
    unique_chinese = list(dict.fromkeys(chinese_strings))
    print(f"共 {len(payloads)} 条文档，合并后需翻译的中文字符串 {len(unique_chinese)} 条")

    translation_map: dict[str, str] = {}
    for i in range(0, len(unique_chinese), BATCH_SIZE):
        batch = unique_chinese[i:i + BATCH_SIZE]
        print(f"  翻译第 {i+1}-{i+len(batch)} 条...")
        translated = batch_translate(batch)
        for orig, trans in zip(batch, translated):
            translation_map[orig] = trans

    out_docs: list[dict] = []
    for key_hex, value_hex, d in payloads:
        t = replace_chinese_strings(d, translation_map)
        t = set_language_en(t)
        t['_id'] = value_hex
        out_docs.append(t)
        print(f"  已处理：查询 _id={key_hex} → 输出 _id={value_hex}")

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(out_docs, f, ensure_ascii=False, indent=2)

    print(f"\n完成，共输出 {len(out_docs)} 条到 {output_path}")


if __name__ == '__main__':
    main()
