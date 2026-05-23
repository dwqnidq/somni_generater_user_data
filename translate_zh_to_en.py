#!/usr/bin/env python3
"""Translate Chinese fields in output/ user data files to English.

Translates 8 file types that contain Chinese text:
  ai_analysis_14d, calendar_events, morning_alarm_insight, sleep_art_data,
  sleep_events, sleep_map_ranking_reason, sleep_report, somni_sleep_analysis

Other files are copied as-is. Files with a "language" field get it set to "en".
"""

import json
import os
import re
import sys
import time
import copy
import glob
import shutil
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OUTPUT_DIR = Path("output")
OUTPUT_EN_DIR = Path("output_en")
USER_ID_RE = re.compile(r"^[0-9a-f]{24}$")

# File types that need Chinese → English translation
TRANSLATE_FILE_TYPES = {
    "ai_analysis_14d",
    "calendar_events",
    "morning_alarm_insight",
    "sleep_art_data",
    "sleep_events",
    "sleep_map_ranking_reason",
    "sleep_report",
    "somni_sleep_analysis",
}

CHINESE_RE = re.compile(r"[一-鿿]")

# Enum / short status translations (used as fallback for very short strings)
ENUM_MAP = {
    "最佳": "Optimal",
    "良好": "Good",
    "过高": "Too High",
    "过低": "Too Low",
    "正常": "Normal",
    "偏高": "Slightly High",
    "偏低": "Slightly Low",
    "眠质不良": "Poor Sleep Quality",
    "深睡守护者": "Deep Sleep Guardian",
    "AI主动干预": "AI Proactive Intervention",
    "入睡困难": "Difficulty Falling Asleep",
    "心率上升": "Heart Rate Increase",
    "环境声响": "Environmental Noise",
    "咳嗽": "Coughing",
    "能量正在温和回升": "Energy Gradually Recovering",
    "能量高度充沛": "Energy Highly Replenished",
    "能量充足": "Energy Sufficient",
    "能量偏低": "Energy Low",
}

client = None


def init_client():
    global client
    client = anthropic.Anthropic()


def has_chinese(s: str) -> bool:
    return bool(CHINESE_RE.search(s))


def find_chinese_entries(obj, path=""):
    """Yield (json_path, chinese_value) for every string containing Chinese."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if isinstance(v, str) and has_chinese(v):
                yield (p, v)
            elif isinstance(v, (dict, list)):
                yield from find_chinese_entries(v, p)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from find_chinese_entries(item, f"{path}[{i}]")


def set_value_at_path(obj, path, value):
    """Set a value in a nested dict/list by dot/bracket path."""
    parts = re.split(r"\.(?![^\[]*\])", path)  # split on dots not inside brackets
    current = obj
    for part in parts[:-1]:
        # Handle array indexing: key[0][1]
        indices = re.findall(r"\[(\d+)\]", part)
        key = re.sub(r"\[\d+\]", "", part)
        if key:
            current = current[key]
        for idx in indices:
            current = current[int(idx)]
    # Last part
    last = parts[-1]
    indices = re.findall(r"\[(\d+)\]", last)
    key = re.sub(r"\[\d+\]", "", last)
    if key:
        if indices:
            current = current[key]
            for idx in indices[:-1]:
                current = current[int(idx)]
            current[int(indices[-1])] = value
        else:
            current[key] = value
    else:
        for idx in indices[:-1]:
            current = current[int(idx)]
        current[int(indices[-1])] = value


def translate_batch(texts: list[str], retries=3) -> list[str]:
    """Translate a list of Chinese strings to English via Claude API."""
    if not texts:
        return []

    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))

    prompt = f"""Translate the following Chinese strings to English. These are from sleep/health monitoring app data.
Rules:
- Translate naturally and accurately
- Keep medical/health terminology precise
- Keep any numbers, percentages, and units as-is
- Output ONLY the translations, one per line, numbered to match the input
- Do NOT add explanations

{numbered}"""

    for attempt in range(retries):
        try:
            model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
            message = client.messages.create(
                model=model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            # Parse numbered lines
            results = []
            for line in raw.split("\n"):
                line = line.strip()
                if not line:
                    continue
                # Remove leading number + dot/paren
                cleaned = re.sub(r"^\d+[\.\)]\s*", "", line)
                results.append(cleaned)

            if len(results) == len(texts):
                return results

            # Try harder to parse
            if len(results) > len(texts):
                results = results[: len(texts)]
            while len(results) < len(texts):
                results.append(texts[len(results)])

            return results

        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  API error: {e}, retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"  API failed after {retries} attempts: {e}")
                raise


def translate_file(src_path: Path, dst_path: Path):
    """Translate a single file's Chinese fields."""
    data = json.loads(src_path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else [data]
    is_single = not isinstance(data, list)

    for item in items:
        entries = list(find_chinese_entries(item))
        if not entries:
            # No Chinese text, just fix language if present
            if "language" in item:
                item["language"] = "en"
            continue

        paths, originals = zip(*entries)

        # Check enum map first for short strings
        translations = []
        to_translate_indices = []
        to_translate_texts = []

        for i, (p, text) in enumerate(entries):
            stripped = text.strip()
            if stripped in ENUM_MAP and len(stripped) <= 6:
                translations.append(ENUM_MAP[stripped])
            else:
                translations.append(None)
                to_translate_indices.append(i)
                to_translate_texts.append(text)

        # Batch translate remaining
        if to_translate_texts:
            print(f"  Translating {len(to_translate_texts)} strings via API...")
            api_translations = translate_batch(to_translate_texts)
            for i, t in zip(to_translate_indices, api_translations):
                translations[i] = t

        # Apply translations
        for path, trans in zip(paths, translations):
            set_value_at_path(item, path, trans)

        # Set language
        if "language" in item:
            item["language"] = "en"

    # Write output
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if is_single:
        dst_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        dst_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def copy_file(src_path: Path, dst_path: Path):
    """Copy a file, setting language to 'en' if the field exists."""
    data = json.loads(src_path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else [data]
    for item in items:
        if isinstance(item, dict) and "language" in item:
            item["language"] = "en"
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def extract_user_id_and_type(filename: str):
    """Extract (user_id, file_type) from a filename like '69aea..._sleep_report.json'."""
    m = re.match(r"^([0-9a-f]{24})_(.+)$", filename)
    if m:
        return m.group(1), m.group(2)
    return None, filename


def main():
    init_client()

    if not OUTPUT_DIR.exists():
        print(f"Error: {OUTPUT_DIR} does not exist")
        sys.exit(1)

    OUTPUT_EN_DIR.mkdir(exist_ok=True)

    files = sorted(OUTPUT_DIR.iterdir())
    total = len(files)
    translated_count = 0
    copied_count = 0
    skipped_count = 0

    for i, fpath in enumerate(files):
        if not fpath.is_file():
            continue

        fname = fpath.name
        uid, ftype = extract_user_id_and_type(fname)

        # Skip non-user-ID files
        if uid is None:
            print(f"[{i+1}/{total}] SKIP (no user ID): {fname}")
            skipped_count += 1
            continue

        dst = OUTPUT_EN_DIR / fname

        if ftype in [t + ".json" for t in TRANSLATE_FILE_TYPES]:
            print(f"[{i+1}/{total}] TRANSLATE: {fname}")
            translate_file(fpath, dst)
            translated_count += 1
        else:
            print(f"[{i+1}/{total}] COPY: {fname}")
            copy_file(fpath, dst)
            copied_count += 1

    print(f"\nDone! Translated: {translated_count}, Copied: {copied_count}, Skipped: {skipped_count}")
    print(f"Output: {OUTPUT_EN_DIR}/")


if __name__ == "__main__":
    main()
