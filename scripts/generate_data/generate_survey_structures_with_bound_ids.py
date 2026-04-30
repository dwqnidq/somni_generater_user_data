"""文件作用：用于 generate survey structures with bound ids 相关的数据处理或流程支持。"""

import json
import os
import sys
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

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

QUIZ_QUESTIONS_FILE = "output/quiz_questions.json"
OUT_DIR = "output"

TARGET_SURVEY_CODES = ["somni_vip", "somni_001"]


ZH_EN_TEXT_MAP = {
    "Somni 睡眠测评（体验室版本）": "Somni Sleep Assessment (Experience Room Edition)",
    "Somni 睡眠测评": "Somni Sleep Assessment",
    "测试": "Test",
    "基于 MEQ 晨夜型量表、FIRST-C 量表、HSP 量表和嗅觉心理学进行的展会轻量化改编问卷": "A lightweight exhibition-adapted questionnaire based on the MEQ, FIRST-C, HSP, and olfactory psychology.",
    "基础信息": "Basic Information",
    "基础信息不参与任何判定，仅用于参数校准": "Basic information does not participate in scoring and is only used for parameter calibration.",
    "人格判定": "Personality Evaluation",
    "该模块问题参与计算。": "Questions in this module are included in scoring.",
    "睡眠阶段区分": "Sleep Stage Differentiation",
    "不参与人格计算": "Not included in personality scoring.",
    "极简与一页化，不参与计算": "Minimal one-page section, not included in scoring.",
    "参与计算": "Included in scoring.",
    "不参与计算": "Not included in scoring.",
    "问卷题目01": "Question 01",
    "问卷题目02": "Question 02",
    "问卷题目03": "Question 03",
    "问卷题目04": "Question 04",
    "问卷题目05": "Question 05",
    "问卷题目06": "Question 06",
    "行业身份与深度偏好页": "Industry Identity and Preference Detail Page",
    "节律判定 (M 晨型 / E 夜型)": "Rhythm Classification (M Morning / E Evening)",
    "应激判定 （H 高觉醒 / L 低觉醒)": "Stress Classification (H High Arousal / L Low Arousal)",
    "感官判定 (R 强耐受 / C 敏感茧房)": "Sensory Classification (R Resistant / C Sensitive Cocoon)",
    "干预锚点 (U / S / M / W)": "Intervention Anchor (U / S / M / W)",
}


def to_jsonable(doc: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(doc, default=str))


def build_question_signature(doc: Dict[str, Any]) -> Tuple[Any, ...]:
    options = doc.get("options") or []
    option_sig = tuple((o.get("option_id"), o.get("sort_order"), o.get("score")) for o in options)
    tags = tuple(doc.get("tags") or [])
    return (
        doc.get("create_time"),
        doc.get("update_time"),
        doc.get("business_type"),
        doc.get("input_type"),
        doc.get("scoring_type"),
        doc.get("dimension"),
        doc.get("status"),
        doc.get("is_extra_input"),
        len(options),
        option_sig,
        tags,
    )


def build_question_signature_loose(doc: Dict[str, Any]) -> Tuple[Any, ...]:
    options = doc.get("options") or []
    option_sig = tuple((o.get("option_id"), o.get("sort_order")) for o in options)
    return (
        doc.get("create_time"),
        doc.get("update_time"),
        doc.get("business_type"),
        doc.get("input_type"),
        doc.get("status"),
        len(options),
        option_sig,
    )


def build_question_signature_by_dimension(doc: Dict[str, Any]) -> Tuple[Any, ...]:
    options = doc.get("options") or []
    option_sig = tuple((o.get("option_id"), o.get("sort_order")) for o in options)
    return (
        doc.get("business_type"),
        doc.get("input_type"),
        doc.get("scoring_type"),
        doc.get("dimension"),
        len(options),
        option_sig,
    )


def translate_text(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    return ZH_EN_TEXT_MAP.get(text, text)


def translate_recursive(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: translate_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [translate_recursive(v) for v in obj]
    return translate_text(obj)


def convert_for_output(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "_id":
                out["_id"] = str(v)
            else:
                out[k] = convert_for_output(v)
        return out
    if isinstance(obj, list):
        return [convert_for_output(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def main() -> None:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    db = client[DB_NAME]

    en_docs_raw = list(db["quiz_questions"].find({"language": "en"}))
    zh_docs_raw = list(db["quiz_questions"].find({"language": "zh"}))

    en_docs = [to_jsonable(d) for d in en_docs_raw]
    zh_docs = [to_jsonable(d) for d in zh_docs_raw]

    # 建立 zh_id -> en_id 绑定
    en_sig_map: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    en_loose_sig_map: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    en_dimension_sig_map: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for d in en_docs:
        en_sig_map.setdefault(build_question_signature(d), []).append(d)
        en_loose_sig_map.setdefault(build_question_signature_loose(d), []).append(d)
        en_dimension_sig_map.setdefault(build_question_signature_by_dimension(d), []).append(d)

    zh_to_en_id: Dict[str, str] = {}
    for zh in zh_docs:
        zh_id = str(zh["_id"])
        candidates = en_sig_map.get(build_question_signature(zh), [])
        if not candidates:
            candidates = en_loose_sig_map.get(build_question_signature_loose(zh), [])
        if not candidates:
            candidates = en_dimension_sig_map.get(build_question_signature_by_dimension(zh), [])
        if not candidates:
            continue
        # 使用第一个未被占用的英文题目
        chosen: Optional[Dict[str, Any]] = None
        used_ids = set(zh_to_en_id.values())
        for c in candidates:
            cid = str(c["_id"])
            if cid not in used_ids:
                chosen = c
                break
        if chosen is None:
            chosen = candidates[0]
        zh_to_en_id[zh_id] = str(chosen["_id"])

    # 1) 给本地英文文件回填 _id
    with open(QUIZ_QUESTIONS_FILE, "r", encoding="utf-8") as f:
        local_en_questions = json.load(f)

    db_en_exact_map: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for d in en_docs:
        key = (
            d.get("title"),
            d.get("create_time"),
            d.get("update_time"),
            d.get("input_type"),
            len(d.get("options") or []),
        )
        db_en_exact_map.setdefault(key, []).append(d)

    patched_local = []
    used_local_match_ids = set()
    for q in local_en_questions:
        key = (
            q.get("title"),
            q.get("create_time"),
            q.get("update_time"),
            q.get("input_type"),
            len(q.get("options") or []),
        )
        candidates = db_en_exact_map.get(key, [])
        chosen = None
        for c in candidates:
            cid = str(c["_id"])
            if cid not in used_local_match_ids:
                chosen = c
                break
        if chosen:
            used_local_match_ids.add(str(chosen["_id"]))
            new_q = deepcopy(q)
            new_q["_id"] = str(chosen["_id"])
            patched_local.append(new_q)
        else:
            patched_local.append(q)

    with open(QUIZ_QUESTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(patched_local, f, ensure_ascii=False, indent=2)

    # 2) 读取中文问卷定义，替换 question_id / rule_config.question_id 为英文 id，再翻译中文字段
    surveys_raw = list(db["quiz_surveys"].find({"code": {"$in": TARGET_SURVEY_CODES}, "language": "zh"}))
    survey_outputs: Dict[str, Dict[str, Any]] = {}

    for s in surveys_raw:
        survey = convert_for_output(s)
        survey_id = survey.pop("_id", None)
        if survey_id:
            survey["source_survey_id"] = survey_id

        for page in survey.get("pages", []):
            for item in page.get("items", []):
                qid = item.get("question_id")
                if qid in zh_to_en_id:
                    item["question_id"] = zh_to_en_id[qid]
                dr = item.get("display_rules")
                if isinstance(dr, dict):
                    dep_qid = dr.get("depends_on_question_id")
                    if dep_qid in zh_to_en_id:
                        dr["depends_on_question_id"] = zh_to_en_id[dep_qid]

        for rc in survey.get("rule_config", []):
            for q in rc.get("questions", []):
                qid = q.get("question_id")
                if qid in zh_to_en_id:
                    q["question_id"] = zh_to_en_id[qid]

        survey = translate_recursive(survey)
        # 强制英文语言标记
        survey["language"] = "en"
        survey_outputs[survey["code"]] = survey

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "zh_to_en_question_id_map.json"), "w", encoding="utf-8") as f:
        json.dump(zh_to_en_id, f, ensure_ascii=False, indent=2)

    with open(os.path.join(OUT_DIR, "generated_somni_vip_en.json"), "w", encoding="utf-8") as f:
        json.dump(survey_outputs.get("somni_vip", {}), f, ensure_ascii=False, indent=2)

    with open(os.path.join(OUT_DIR, "generated_somni_001_en.json"), "w", encoding="utf-8") as f:
        json.dump(survey_outputs.get("somni_001", {}), f, ensure_ascii=False, indent=2)

    client.close()

    print(f"英文题库条数: {len(en_docs)}")
    print(f"中文题库条数: {len(zh_docs)}")
    print(f"成功绑定 zh->en 题目ID: {len(zh_to_en_id)}")
    print(f"已更新: {QUIZ_QUESTIONS_FILE}")
    print("已生成:")
    print(" - output/zh_to_en_question_id_map.json")
    print(" - output/generated_somni_vip_en.json")
    print(" - output/generated_somni_001_en.json")


if __name__ == "__main__":
    main()
