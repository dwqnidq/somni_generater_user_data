#!/usr/bin/env python3
"""
按 quiz_surveys._id 拉取问卷与关联 quiz_questions（zh），
经多轮翻译 + 模型校验后，将英文题目、ID 映射、英文问卷写入单个 JSON。

用法（项目根目录）:
  .venv/bin/python scripts/translate/translate_quiz_survey_questions.py \\
    --survey-id 69b11824e2888c42a8a59f24
  .venv/bin/python scripts/translate/translate_quiz_survey_questions.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from bson import ObjectId  # noqa: E402
from pymongo import MongoClient  # noqa: E402

from scripts.generate_data.generate_survey_structures_with_bound_ids import (  # noqa: E402
    build_question_signature,
    build_question_signature_by_dimension,
    build_question_signature_loose,
    to_jsonable,
    translate_recursive,
)
from scripts.translate._common import (  # noqa: E402
    DEFAULT_CACHE_FILE,
    MAX_VERIFY_ROUNDS,
    MODEL_NAME,
    TRANSLATE_PASSES,
    collect_chinese_strings,
    has_chinese,
    load_translation_cache,
    save_translation_cache,
    translate_document_list,
)

MONGO_URI = os.getenv(
    "MONGODB_URI",
    "mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive",
)
DB_NAME = "Fullive"
DEFAULT_SURVEY_ID = "69b11824e2888c42a8a59f24"

QUESTION_ID_KEYS = frozenset({"question_id", "depends_on_question_id"})


def parse_object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value.strip())
    except Exception as exc:
        raise argparse.ArgumentTypeError(f"无效的 ObjectId: {value}") from exc


def collect_question_ids(obj: Any, out: set[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in QUESTION_ID_KEYS and value:
                out.add(str(value))
            else:
                collect_question_ids(value, out)
    elif isinstance(obj, list):
        for item in obj:
            collect_question_ids(item, out)


def build_en_signature_maps(
    en_docs: list[dict[str, Any]],
) -> tuple[
    dict[tuple[Any, ...], list[dict[str, Any]]],
    dict[tuple[Any, ...], list[dict[str, Any]]],
    dict[tuple[Any, ...], list[dict[str, Any]]],
]:
    exact: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    loose: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    by_dimension: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for doc in en_docs:
        exact.setdefault(build_question_signature(doc), []).append(doc)
        loose.setdefault(build_question_signature_loose(doc), []).append(doc)
        by_dimension.setdefault(build_question_signature_by_dimension(doc), []).append(doc)
    return exact, loose, by_dimension


def pick_en_candidate(
    zh_doc: dict[str, Any],
    exact: dict[tuple[Any, ...], list[dict[str, Any]]],
    loose: dict[tuple[Any, ...], list[dict[str, Any]]],
    by_dimension: dict[tuple[Any, ...], list[dict[str, Any]]],
    used_en_ids: set[str],
) -> dict[str, Any] | None:
    candidates = exact.get(build_question_signature(zh_doc), [])
    if not candidates:
        candidates = loose.get(build_question_signature_loose(zh_doc), [])
    if not candidates:
        candidates = by_dimension.get(build_question_signature_by_dimension(zh_doc), [])
    for candidate in candidates:
        cid = str(candidate["_id"])
        if cid not in used_en_ids:
            return candidate
    return candidates[0] if candidates else None


def assign_zh_to_en_ids(
    zh_docs: list[dict[str, Any]],
    en_pool: list[dict[str, Any]],
    *,
    id_strategy: str,
) -> dict[str, str]:
    zh_to_en: dict[str, str] = {}
    used_en_ids: set[str] = set()
    exact, loose, by_dimension = build_en_signature_maps(en_pool)

    for zh in zh_docs:
        zh_id = str(zh["_id"])
        en_id: str | None = None
        if id_strategy == "bind":
            chosen = pick_en_candidate(zh, exact, loose, by_dimension, used_en_ids)
            if chosen is not None:
                en_id = str(chosen["_id"])
        if en_id is None:
            en_id = str(ObjectId())
        zh_to_en[zh_id] = en_id
        used_en_ids.add(en_id)
    return zh_to_en


def apply_question_id_map(obj: Any, zh_to_en: dict[str, str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in QUESTION_ID_KEYS and value is not None:
                qid = str(value)
                if qid in zh_to_en:
                    obj[key] = zh_to_en[qid]
            else:
                apply_question_id_map(value, zh_to_en)
    elif isinstance(obj, list):
        for item in obj:
            apply_question_id_map(item, zh_to_en)


def doc_to_output_id_str(doc: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(doc)
    if "_id" in out:
        out["_id"] = str(out["_id"])
    for field in ("create_time", "update_time"):
        if field in out and hasattr(out[field], "isoformat"):
            out[field] = out[field].isoformat()
    return out


def assert_fully_translated(payload: dict[str, Any]) -> None:
    remaining: list[str] = []
    collect_chinese_strings(payload, remaining)
    if remaining:
        preview = remaining[0][:60] + ("…" if len(remaining[0]) > 60 else "")
        raise RuntimeError(
            f"校验未通过：输出仍含 {len(remaining)} 处中文，示例: {preview}"
        )


def fetch_survey_and_questions(
    survey_id: ObjectId,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    try:
        db = client[DB_NAME]
        survey_raw = db["quiz_surveys"].find_one({"_id": survey_id})
        if not survey_raw:
            raise RuntimeError(f"未找到 quiz_surveys._id={survey_id}")

        qid_set: set[str] = set()
        collect_question_ids(survey_raw, qid_set)
        if not qid_set:
            raise RuntimeError("问卷中未找到任何 question_id")

        zh_questions: list[dict[str, Any]] = []
        missing: list[str] = []
        for qid in sorted(qid_set):
            doc = db["quiz_questions"].find_one(
                {"_id": ObjectId(qid), "language": "zh"},
            )
            if doc is None:
                missing.append(qid)
            else:
                zh_questions.append(to_jsonable(doc))

        if missing:
            raise RuntimeError(
                f"quiz_questions 缺少 {len(missing)} 条 zh 题目: {missing[:3]}…"
            )

        en_pool = [
            to_jsonable(d)
            for d in db["quiz_questions"].find({"language": "en"})
        ]
        return to_jsonable(survey_raw), zh_questions, en_pool
    finally:
        client.close()


def build_en_question_drafts(
    zh_docs: list[dict[str, Any]],
    zh_to_en: dict[str, str],
) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    for zh in zh_docs:
        en = deepcopy(zh)
        en["_id"] = zh_to_en[str(zh["_id"])]
        en["language"] = "en"
        drafts.append(en)
    return drafts


def build_en_survey_draft(
    survey_zh: dict[str, Any],
    zh_to_en: dict[str, str],
) -> dict[str, Any]:
    survey = deepcopy(survey_zh)
    source_id = str(survey.pop("_id", ""))
    apply_question_id_map(survey, zh_to_en)
    survey = translate_recursive(survey)
    survey["language"] = "en"
    if source_id:
        survey["source_survey_id"] = source_id
    return survey


def write_output_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def default_output_path(survey_id: str) -> Path:
    return PROJECT_ROOT / "output" / f"quiz_survey_translate_{survey_id}.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="翻译指定 quiz_surveys 及关联 quiz_questions，校验通过后写入单个 JSON",
    )
    parser.add_argument(
        "--survey-id",
        type=parse_object_id,
        default=parse_object_id(DEFAULT_SURVEY_ID),
        help=f"quiz_surveys._id（默认 {DEFAULT_SURVEY_ID}）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="输出 JSON 路径（默认 output/quiz_survey_translate_<survey_id>.json）",
    )
    parser.add_argument(
        "--id-strategy",
        choices=("bind", "new"),
        default="bind",
        help="英文题 _id：bind=先匹配库内 en 题否则新建；new=全部新建 ObjectId",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计待翻译，不写文件")
    parser.add_argument(
        "--translate-passes",
        type=int,
        default=3,
        help="每条中文独立翻译次数（默认 3，至少 2）",
    )
    parser.add_argument(
        "--max-verify-rounds",
        type=int,
        default=MAX_VERIFY_ROUNDS,
        help=f"校验失败后的最大重试轮数（默认 {MAX_VERIFY_ROUNDS}）",
    )
    parser.add_argument("--no-cache", action="store_true", help="不使用已校验翻译缓存")
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=DEFAULT_CACHE_FILE,
        help="翻译缓存路径",
    )
    args = parser.parse_args()

    survey_id = args.survey_id
    survey_id_str = str(survey_id)
    output_path = args.output or default_output_path(survey_id_str)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path

    translate_passes = max(2, args.translate_passes)
    max_verify_rounds = max(1, args.max_verify_rounds)
    use_cache = not args.no_cache

    print(f"读取问卷 quiz_surveys._id={survey_id_str} …")
    survey_zh, zh_questions, en_pool = fetch_survey_and_questions(survey_id)
    print(
        f"问卷 code={survey_zh.get('code')!r} language={survey_zh.get('language')!r} "
        f"关联题目 {len(zh_questions)} 条"
    )

    zh_to_en = assign_zh_to_en_ids(
        zh_questions,
        en_pool if args.id_strategy == "bind" else [],
        id_strategy=args.id_strategy,
    )
    bound_count = sum(
        1 for zid, eid in zh_to_en.items() if zid != eid
    )
    print(f"ID 映射 {len(zh_to_en)} 条（与 zh 不同 _id 的 {bound_count} 条）")

    chinese_in_questions: list[str] = []
    collect_chinese_strings(zh_questions, chinese_in_questions)
    chinese_in_survey: list[str] = []
    collect_chinese_strings(survey_zh, chinese_in_survey)
    unique_total = list(
        dict.fromkeys(chinese_in_questions + chinese_in_survey),
    )
    print(f"待翻译中文字符串 {len(unique_total)} 条（去重）")

    if args.dry_run:
        api_per = translate_passes + 1
        print(
            f"[dry-run] 模型 {MODEL_NAME}；每条约 {api_per} 次 API + "
            f"最多 {max_verify_rounds} 轮校验；预估 ~{len(unique_total) * api_per} 次调用"
        )
        return

    cache_path = (
        args.cache_file
        if args.cache_file.is_absolute()
        else PROJECT_ROOT / args.cache_file
    )
    cache = load_translation_cache(cache_path)
    print(f"缓存: {cache_path.relative_to(PROJECT_ROOT)}（{len(cache)} 条）")

    en_drafts = build_en_question_drafts(zh_questions, zh_to_en)
    print("\n=== 翻译 quiz_questions ===")
    questions_en = translate_document_list(
        en_drafts,
        cache,
        target_language="en",
        translate_passes=translate_passes,
        max_verify_rounds=max_verify_rounds,
        use_cache=use_cache,
    )

    survey_draft = build_en_survey_draft(survey_zh, zh_to_en)
    print("\n=== 翻译 quiz_surveys 文案 ===")
    surveys_en = translate_document_list(
        [survey_draft],
        cache,
        target_language="en",
        translate_passes=translate_passes,
        max_verify_rounds=max_verify_rounds,
        use_cache=use_cache,
    )
    survey_en = surveys_en[0]

    payload: dict[str, Any] = {
        "meta": {
            "source_survey_id": survey_id_str,
            "survey_code": survey_zh.get("code"),
            "source_language": survey_zh.get("language"),
            "target_language": "en",
            "question_count": len(questions_en),
            "id_strategy": args.id_strategy,
            "translated_at": datetime.now(timezone.utc).isoformat(),
        },
        "zh_to_en_question_id_map": zh_to_en,
        "questions_en": [doc_to_output_id_str(q) for q in questions_en],
        "survey_en": doc_to_output_id_str(survey_en),
    }

    print("\n=== 终检：输出不得含中文 ===")
    assert_fully_translated(payload)

    en_ids_from_map = set(zh_to_en.values())
    for doc in payload["questions_en"]:
        doc_id = str(doc.get("_id"))
        if doc_id not in en_ids_from_map:
            raise RuntimeError(f"题目 _id 与映射表 en_id 不一致: {doc_id}")

    write_output_atomic(output_path, payload)
    if unique_total:
        save_translation_cache(cache_path, cache)

    rel = output_path.relative_to(PROJECT_ROOT)
    print(f"\n校验通过，已写入 {rel}")
    print(f"  题目 {len(questions_en)} 条 | 映射 {len(zh_to_en)} 条")


if __name__ == "__main__":
    main()
