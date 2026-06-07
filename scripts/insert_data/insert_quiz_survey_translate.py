#!/usr/bin/env python3
"""
将 translate_quiz_survey_questions.py 输出的 JSON 写入 MongoDB：
  - questions_en → quiz_questions（insert_many，保留 JSON 中的 _id）
  - survey_en    → quiz_surveys（insert_one，由 MongoDB 分配 _id）

用法（项目根目录）:
  python scripts/insert_data/insert_quiz_survey_translate.py --dry-run
  python scripts/insert_data/insert_quiz_survey_translate.py
  python scripts/insert_data/insert_quiz_survey_translate.py \\
    --input output/quiz_survey_translate_69b11824e2888c42a8a59f24.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

load_dotenv()

MONGO_URI = os.getenv(
    "MONGODB_URI",
    "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive",
)
DB_NAME = "Fullive"
COLLECTION_QUESTIONS = "quiz_questions"
COLLECTION_SURVEYS = "quiz_surveys"
DEFAULT_SURVEY_ID = "69b11824e2888c42a8a59f24"
DEFAULT_INPUT = PROJECT_ROOT / "output" / f"quiz_survey_translate_{DEFAULT_SURVEY_ID}.json"

DATETIME_FIELDS = ("create_time", "update_time")


def parse_datetime(value: Any) -> Any:
    """将时间字符串转为 datetime：支持 ISO（含 Z）与 'YYYY-MM-DD HH:MM:SS.ffffff'"""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.endswith("Z") or "T" in text:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(text)


def normalize_datetimes(doc: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(doc)
    for field in DATETIME_FIELDS:
        if field in out and isinstance(out[field], str):
            out[field] = parse_datetime(out[field])
    return out


def normalize_question(record: dict[str, Any]) -> dict[str, Any]:
    doc = normalize_datetimes(record)
    raw_id = doc.get("_id")
    if isinstance(raw_id, str):
        doc["_id"] = ObjectId(raw_id)
    return doc


def normalize_survey(doc: dict[str, Any]) -> dict[str, Any]:
    out = normalize_datetimes(doc)
    source_id = out.get("source_survey_id")
    if isinstance(source_id, str) and ObjectId.is_valid(source_id):
        out["source_survey_id"] = ObjectId(source_id)
    return out


def load_translate_payload(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    questions_raw = payload.get("questions_en")
    if not isinstance(questions_raw, list) or not questions_raw:
        raise ValueError(f"{path}: 缺少非空 questions_en 数组")

    survey_raw = payload.get("survey_en")
    if not isinstance(survey_raw, dict) or not survey_raw:
        raise ValueError(f"{path}: 缺少非空 survey_en 对象")

    code = survey_raw.get("code")
    language = survey_raw.get("language")
    if not code or not language:
        raise ValueError(f"{path}: survey_en 须包含 code 与 language")

    return questions_raw, survey_raw


def insert_questions(
    db: Any,
    records: list[dict[str, Any]],
    *,
    dry_run: bool,
) -> None:
    normalized = [normalize_question(record) for record in records]

    if dry_run:
        sample_ids = [str(doc["_id"]) for doc in normalized[:3]]
        print(
            f"[dry-run] quiz_questions: 将 insert_many {len(normalized)} 条 "
            f"(示例 _id: {', '.join(sample_ids)}…)"
        )
        return

    result = db[COLLECTION_QUESTIONS].insert_many(normalized)
    print(f"quiz_questions: insert 成功，共 {len(result.inserted_ids)} 条")


def insert_survey(
    db: Any,
    survey_raw: dict[str, Any],
    *,
    dry_run: bool,
) -> None:
    doc = normalize_survey(survey_raw)

    if dry_run:
        print(
            f"[dry-run] quiz_surveys: 将 insert 1 条 "
            f"code={doc['code']!r}, language={doc['language']!r}"
        )
        return

    result = db[COLLECTION_SURVEYS].insert_one(doc)
    print(
        f"quiz_surveys: insert 成功 _id={result.inserted_id}, "
        f"code={doc['code']!r}, language={doc['language']!r}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将 quiz_survey_translate_*.json 的 questions_en / survey_en 写入 MongoDB",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"翻译输出 JSON（默认 {DEFAULT_INPUT.relative_to(PROJECT_ROOT)}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只校验并打印将写入的条数，不连接写库",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = args.input if args.input.is_absolute() else PROJECT_ROOT / args.input
    if not input_path.is_file():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    questions_raw, survey_raw = load_translate_payload(input_path)
    print(f"读取 {input_path.relative_to(PROJECT_ROOT)}")
    print(f"  questions_en: {len(questions_raw)} 条")
    print(
        f"  survey_en: code={survey_raw.get('code')!r}, "
        f"language={survey_raw.get('language')!r}"
    )

    if args.dry_run:
        insert_questions(None, questions_raw, dry_run=True)
        insert_survey(None, survey_raw, dry_run=True)
        print("dry-run 完成，未写入数据库")
        return

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    try:
        db = client[DB_NAME]
        insert_questions(db, questions_raw, dry_run=False)
        insert_survey(db, survey_raw, dry_run=False)
    finally:
        client.close()

    print("完成")


if __name__ == "__main__":
    main()
