"""
将 output 目录中所有 JSON 文件的中文字符串值翻译为英文
使用火山方舟 OpenAI 兼容接口调用豆包（.env：BASE_URL、DOUBAO_API_KEY、MODEL_NAME），批量处理以减少 API 调用次数
"""
# 文件作用：用于 translate output json 相关的数据处理或流程支持。


import os
import json
import re
import shutil
import time
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("DOUBAO_API_KEY")
TRANSLATE_BASE_URL = os.getenv(
    "BASE_URL",
    "https://ark.cn-beijing.volces.com/api/v3",
)
TRANSLATE_MODEL_NAME = os.getenv("MODEL_NAME", "doubao-seed-2-0-mini-260215")
TRANSLATE_ENABLE_THINKING = False
OUTPUT_DIR = os.getenv('OUTPUT_DIR', 'output')
TRANSLATE_BATCH_SIZE = int(os.getenv('TRANSLATE_BATCH_SIZE', '80'))
TRANSLATE_WORKERS = int(os.getenv('TRANSLATE_WORKERS', '8'))
TRANSLATE_TIMEOUT_SEC = int(os.getenv('TRANSLATE_TIMEOUT_SEC', '120'))
TRANSLATE_CACHE_FILE = os.getenv('TRANSLATE_CACHE_FILE', '.translation_cache.json')
TRANSLATE_RETRY_TIMES = int(os.getenv('TRANSLATE_RETRY_TIMES', '3'))
TRANSLATE_RETRY_BASE_DELAY_SEC = float(os.getenv('TRANSLATE_RETRY_BASE_DELAY_SEC', '2'))

HEADERS = {
    'Content-Type': 'application/json',
    'Authorization': f'Bearer {API_KEY}'
}

HAS_CHINESE_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')
_CACHE_LOCK = threading.Lock()
PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt")


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


def batch_translate(texts: list[str]) -> list[str]:
    """批量翻译中文字符串列表，返回对应的英文列表"""
    if not texts:
        return []

    # 构建批量翻译 prompt，用编号分隔每条文本
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    prompt = render_prompt_template(
        'translate_output_json__batch_translate.md',
        {"NUMBERED_INPUTS": numbered},
    )

    data = {
        'model': TRANSLATE_MODEL_NAME,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.1,
        'max_tokens': 4096,
        'enable_thinking': TRANSLATE_ENABLE_THINKING,
    }

    last_error = None
    max_attempts = max(1, TRANSLATE_RETRY_TIMES)
    for attempt in range(1, max_attempts + 1):
        try:
            base = TRANSLATE_BASE_URL.rstrip('/')
            response = requests.post(
                f'{base}/chat/completions',
                headers=HEADERS,
                json=data,
                timeout=TRANSLATE_TIMEOUT_SEC,
            )
            response.raise_for_status()
            result = response.json()
            break
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt == max_attempts:
                raise RuntimeError(
                    f"翻译请求失败（重试 {max_attempts} 次后仍失败）: {e}"
                ) from e
            delay_sec = TRANSLATE_RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1))
            print(
                f"  [重试] 翻译接口调用失败，第 {attempt}/{max_attempts} 次，"
                f"{delay_sec:.1f} 秒后重试: {e}"
            )
            time.sleep(delay_sec)

    if last_error and 'result' not in locals():
        raise RuntimeError(f"翻译请求失败: {last_error}") from last_error

    content = result['choices'][0]['message']['content'].strip()

    # 解析返回结果
    translations = {}
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r'^(\d+)\.\s*(.*)', line)
        if match:
            idx = int(match.group(1)) - 1
            translations[idx] = match.group(2).strip()

    # 按顺序返回，若某条解析失败则保留原文
    return [translations.get(i, texts[i]) for i in range(len(texts))]


def load_translation_cache(cache_file: str) -> dict:
    if not os.path.exists(cache_file):
        return {}
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_translation_cache(cache_file: str, cache: dict):
    tmp_file = f"{cache_file}.tmp"
    with open(tmp_file, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, cache_file)


def translate_texts_with_cache(texts: list[str], cache: dict, batch_size: int) -> dict:
    """
    返回 {原文: 译文}，优先走本地缓存，未命中的才请求模型。
    """
    translation_map = {}
    missing = []

    with _CACHE_LOCK:
        for t in texts:
            cached = cache.get(t)
            if cached:
                translation_map[t] = cached
            else:
                missing.append(t)

    if not missing:
        return translation_map

    for i in range(0, len(missing), batch_size):
        batch = missing[i:i + batch_size]
        translated = batch_translate(batch)
        with _CACHE_LOCK:
            for orig, trans in zip(batch, translated):
                translation_map[orig] = trans
                cache[orig] = trans

    return translation_map


def collect_chinese_strings(obj, results: list):
    """递归收集 JSON 中所有含中文的字符串值"""
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
    """递归替换 JSON 中所有含中文的字符串值"""
    if isinstance(obj, str):
        return translation_map.get(obj, obj)
    elif isinstance(obj, dict):
        return {k: replace_chinese_strings(v, translation_map) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [replace_chinese_strings(item, translation_map) for item in obj]
    return obj


def merge_original_and_translated(original_data, translated_data):
    """
    根节点是数组时：保留原中文数据，并把英文翻译副本追加到末尾。
    其他类型保持原行为（直接写入翻译结果）。
    """
    if isinstance(original_data, list) and isinstance(translated_data, list):
        return original_data + translated_data
    return translated_data


def translate_json_file(
    filepath: str,
    cache: dict,
    batch_size: int,
    backup_dir: Optional[str] = None,
):
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 收集所有含中文的字符串（去重）
    chinese_strings = []
    collect_chinese_strings(data, chinese_strings)
    unique_chinese = list(dict.fromkeys(chinese_strings))  # 保序去重

    if not unique_chinese:
        print(f"  [跳过] 无中文内容: {os.path.basename(filepath)}")
        return

    print(f"  [翻译] {os.path.basename(filepath)} — 共 {len(unique_chinese)} 条中文字符串")

    # 分批翻译（先查缓存，未命中才调用模型）
    translation_map = translate_texts_with_cache(unique_chinese, cache, batch_size)

    # 生成翻译结果
    translated_data = replace_chinese_strings(data, translation_map)

    # 翻译完成写回前：备份当前磁盘上的原始文件（含中文）
    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(filepath, os.path.join(backup_dir, os.path.basename(filepath)))

    # 如果是以下文件，将 language 字段改为 en
    # - sleep_report
    # - schedule_data
    # - sleep_events
    # - ai_analysis
    language_target_patterns = (
        '_sleep_report.json',
        '_schedule_data.json',
        '_sleep_events.json',
        '_ai_analysis.json',
    )
    if any(p in filepath for p in language_target_patterns):
        if isinstance(translated_data, list):
            for item in translated_data:
                if isinstance(item, dict):
                    item['language'] = 'en'
        elif isinstance(translated_data, dict):
            translated_data['language'] = 'en'

    # 根节点是数组时，保留中文并在末尾追加英文翻译副本
    output_data = merge_original_and_translated(data, translated_data)

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"  [完成] {os.path.basename(filepath)}")


def main():
    if not API_KEY:
        raise ValueError("请设置 DOUBAO_API_KEY（及 BASE_URL、MODEL_NAME）用于火山方舟 / 豆包")

    # 跳过不需要翻译的文件
    SKIP_PATTERNS = ['_quiz_result.json', '_sleep_plan_data.json']

    json_files = [
        os.path.join(OUTPUT_DIR, f)
        for f in os.listdir(OUTPUT_DIR)
        if f.endswith('.json') and not any(p in f for p in SKIP_PATTERNS)
    ]
    json_files.sort()

    print(f"共找到 {len(json_files)} 个 JSON 文件，开始翻译...\n")

    backup_dir = os.path.join(
        f"{OUTPUT_DIR}_backup",
        f"translate_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    )
    print(f"本次运行原始文件将备份至: {backup_dir}\n")

    # 翻译缓存（跨文件复用）
    cache_file = os.path.join(OUTPUT_DIR, TRANSLATE_CACHE_FILE) if not os.path.isabs(TRANSLATE_CACHE_FILE) else TRANSLATE_CACHE_FILE
    cache = load_translation_cache(cache_file)
    print(f"翻译缓存条目数: {len(cache)}")

    workers = max(1, min(TRANSLATE_WORKERS, 16))
    batch_size = max(10, min(TRANSLATE_BATCH_SIZE, 200))
    print(f"并发线程: {workers}，批次大小: {batch_size}\n")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [
            ex.submit(translate_json_file, filepath, cache, batch_size, backup_dir)
            for filepath in json_files
        ]
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as e:
                print(f"  [错误] 翻译任务失败: {e}")

    # 持久化缓存
    save_translation_cache(cache_file, cache)
    print(f"缓存已保存: {cache_file}（共 {len(cache)} 条）")

    print("\n全部完成。")


if __name__ == '__main__':
    main()
