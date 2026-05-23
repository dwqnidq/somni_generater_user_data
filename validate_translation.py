#!/usr/bin/env python3
"""Validate translated output_en/ files.

Checks:
1. JSON syntax validity
2. language field is "en" where present
3. No remaining Chinese characters in string values (for translated file types)
4. File structure matches original (same keys, same types)
"""

import json
import re
import sys
from pathlib import Path

OUTPUT_DIR = Path("output")
OUTPUT_EN_DIR = Path("output_en")
USER_ID_RE = re.compile(r"^[0-9a-f]{24}$")
CHINESE_RE = re.compile(r"[一-鿿]")

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


def extract_type(filename: str):
    m = re.match(r"^[0-9a-f]{24}_(.+)$", filename)
    return m.group(1) if m else filename


def find_chinese_strings(obj, path=""):
    """Find all string values containing Chinese characters."""
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if isinstance(v, str) and CHINESE_RE.search(v):
                results.append((p, v))
            elif isinstance(v, (dict, list)):
                results.extend(find_chinese_strings(v, p))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            results.extend(find_chinese_strings(item, f"{path}[{i}]"))
    return results


def validate_file(en_path: Path, orig_path: Path) -> list[str]:
    """Validate a single translated file. Returns list of errors."""
    errors = []
    fname = en_path.name
    ftype = extract_type(fname)
    is_translated_type = ftype in [t + ".json" for t in TRANSLATE_FILE_TYPES]

    # 1. JSON syntax
    try:
        data = json.loads(en_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"INVALID JSON: {e}"]

    # 2. language field
    items = data if isinstance(data, list) else [data]
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        if "language" in item and item["language"] != "en":
            loc = f"[{i}]" if isinstance(data, list) else ""
            errors.append(f"{fname}{loc}: language is '{item['language']}', expected 'en'")

    # 3. No remaining Chinese (only for translated file types)
    if is_translated_type:
        chinese = find_chinese_strings(data)
        for path, val in chinese:
            # Allow very short strings that might be proper nouns or codes
            if len(val.strip()) <= 2:
                continue
            errors.append(f"{fname}: Chinese text at '{path}': '{val[:50]}...'")

    # 4. Structure match
    if orig_path.exists():
        try:
            orig_data = json.loads(orig_path.read_text(encoding="utf-8"))
            orig_items = orig_data if isinstance(orig_data, list) else [orig_data]

            if len(items) != len(orig_items):
                errors.append(f"{fname}: item count mismatch: {len(items)} vs {len(orig_items)} original")

            # Check first item keys match
            if items and orig_items and isinstance(items[0], dict) and isinstance(orig_items[0], dict):
                en_keys = set(items[0].keys())
                orig_keys = set(orig_items[0].keys())
                missing = orig_keys - en_keys
                extra = en_keys - orig_keys
                if missing:
                    errors.append(f"{fname}: missing keys: {missing}")
                if extra:
                    errors.append(f"{fname}: extra keys: {extra}")
        except json.JSONDecodeError:
            errors.append(f"{fname}: could not parse original file for comparison")

    return errors


def main():
    if not OUTPUT_EN_DIR.exists():
        print(f"Error: {OUTPUT_EN_DIR} does not exist. Run translate_zh_to_en.py first.")
        sys.exit(1)

    all_errors = []
    checked = 0

    for en_path in sorted(OUTPUT_EN_DIR.iterdir()):
        if not en_path.is_file():
            continue
        if not USER_ID_RE.match(en_path.name[:24]):
            continue

        checked += 1
        orig_path = OUTPUT_DIR / en_path.name
        errors = validate_file(en_path, orig_path)
        all_errors.extend(errors)

        status = "OK" if not errors else f"FAIL ({len(errors)} issues)"
        print(f"  {en_path.name}: {status}")

    print(f"\n{'='*60}")
    print(f"Checked: {checked} files")

    if all_errors:
        print(f"Errors found: {len(all_errors)}\n")
        for e in all_errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("All files valid!")


if __name__ == "__main__":
    main()
