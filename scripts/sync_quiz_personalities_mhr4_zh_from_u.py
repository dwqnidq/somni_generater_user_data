#!/usr/bin/env python3
"""
将 quiz_personalities 中 language=zh 且 mhr_codes 长度为 4 的文档按前三位分组，
以第四位为 U 的文档为主数据，同步组内其余文档内容后写回库。

流程：
1. 查询 zh + len(mhr_codes)==4，排除 mhr_name 为「测试」/「Test」
2. 按 (codes[0], codes[1], codes[2]) 分组
3. 每组以 codes[3]==U 为主，深拷贝主文档并仅保留各条自身的 mhr_codes
4. delete_many(同上查询条件，不含测试数据)
5. insert_many(同步后的全部文档)

用法（项目根目录）:
  python scripts/sync_quiz_personalities_mhr4_zh_from_u.py --dry-run
  python scripts/sync_quiz_personalities_mhr4_zh_from_u.py
  python scripts/sync_quiz_personalities_mhr4_zh_from_u.py --no-backup
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from pymongo import MongoClient

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
COLLECTION = "quiz_personalities"
MASTER_ANCHOR = "U"
LANGUAGE = "zh"
EXCLUDED_MHR_NAMES = frozenset({"测试", "Test"})
DEFAULT_BACKUP = Path("output/quiz_personalities_mhr4_zh_before_sync.json")


def _extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


def _mhr_prefix(codes: list[Any]) -> tuple[str, str, str] | None:
    if not isinstance(codes, list) or len(codes) < 3:
        return None
    return (str(codes[0]), str(codes[1]), str(codes[2]))


def _fourth_anchor(codes: list[Any]) -> str | None:
    if not isinstance(codes, list) or len(codes) < 4:
        return None
    return str(codes[3]).upper()


def _codes_key(codes: list[Any]) -> str:
    return "-".join(str(c) for c in codes)


def _is_excluded_test_doc(doc: dict[str, Any]) -> bool:
    name = doc.get("mhr_name")
    if name is None:
        return False
    return str(name).strip() in EXCLUDED_MHR_NAMES


def base_query() -> dict[str, Any]:
    return {
        "language": LANGUAGE,
        "$expr": {"$eq": [{"$size": "$mhr_codes"}, 4]},
        "mhr_name": {"$nin": list(EXCLUDED_MHR_NAMES)},
    }


def group_docs(docs: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for doc in docs:
        codes = doc.get("mhr_codes")
        pre = _mhr_prefix(codes) if isinstance(codes, list) else None
        if pre is None:
            raise ValueError(f"无效 mhr_codes: {codes!r} (_id={doc.get('_id')})")
        groups[pre].append(doc)
    return dict(groups)


def pick_master(group: list[dict[str, Any]]) -> dict[str, Any]:
    masters = [d for d in group if _fourth_anchor(d.get("mhr_codes") or []) == MASTER_ANCHOR]
    if len(masters) == 1:
        return masters[0]
    if not masters:
        pre = _mhr_prefix(group[0].get("mhr_codes") or [])
        raise ValueError(f"组 {pre} 缺少第四位为 {MASTER_ANCHOR} 的主数据")
    keys = [_codes_key(d.get("mhr_codes") or []) for d in masters]
    raise ValueError(f"组 {_mhr_prefix(group[0].get('mhr_codes') or [])} 存在多条主数据: {keys}")


def validate_groups(
    groups: dict[tuple[str, str, str], list[dict[str, Any]]],
    *,
    allow_incomplete: bool,
) -> list[str]:
    """返回无法同步的组前缀说明；allow_incomplete 为 False 时由调用方据此退出。"""
    problems: list[str] = []
    for pre, group in sorted(groups.items()):
        masters = [d for d in group if _fourth_anchor(d.get("mhr_codes") or []) == MASTER_ANCHOR]
        keys = [_codes_key(d.get("mhr_codes") or []) for d in group]
        if len(masters) == 1:
            continue
        if not masters:
            problems.append(
                f"  {'-'.join(pre)}: 无 {MASTER_ANCHOR} 主数据，共 {len(group)} 条 {keys}"
            )
        else:
            dup = [_codes_key(d.get("mhr_codes") or []) for d in masters]
            problems.append(f"  {'-'.join(pre)}: 多条 {MASTER_ANCHOR} 主数据 {dup}")
    if problems and not allow_incomplete:
        raise ValueError("以下组无法选定主数据:\n" + "\n".join(problems))
    return problems


def sync_doc_from_master(master: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    codes = doc.get("mhr_codes")
    if not isinstance(codes, list) or len(codes) != 4:
        raise ValueError(f"同步目标 mhr_codes 无效: {codes!r}")
    synced = copy.deepcopy(master)
    synced.pop("_id", None)
    synced["mhr_codes"] = list(codes)
    synced["language"] = LANGUAGE
    return synced


def build_synced_records(
    groups: dict[tuple[str, str, str], list[dict[str, Any]]],
    *,
    allow_incomplete: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pre in sorted(groups):
        group = groups[pre]
        keys = [_codes_key(d.get("mhr_codes") or []) for d in group]
        try:
            master = pick_master(group)
        except ValueError:
            if not allow_incomplete:
                raise
            for doc in sorted(group, key=lambda d: _codes_key(d.get("mhr_codes") or [])):
                kept = copy.deepcopy(doc)
                kept.pop("_id", None)
                out.append(kept)
            print(f"  组 {'-'.join(pre)}: [跳过同步] 原样保留 {len(group)} 条 → {keys}")
            continue

        master_key = _codes_key(master.get("mhr_codes") or [])
        for doc in sorted(group, key=lambda d: _codes_key(d.get("mhr_codes") or [])):
            out.append(sync_doc_from_master(master, doc))
        print(
            f"  组 {'-'.join(pre)}: 主数据 {master_key}, "
            f"同步 {len(group)} 条 → {keys}"
        )
    return out


def main() -> None:
    os.chdir(PROJECT_ROOT)
    load_dotenv(PROJECT_ROOT / ".env")

    ap = argparse.ArgumentParser(
        description="按前三位分组，以 U 为主同步 quiz_personalities 四位 zh 文档并写回"
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只分组与同步预览，不删除/插入",
    )
    ap.add_argument(
        "--backup",
        type=Path,
        default=DEFAULT_BACKUP,
        help=f"写库前备份路径（默认 {DEFAULT_BACKUP}）",
    )
    ap.add_argument(
        "--no-backup",
        action="store_true",
        help="不写备份文件",
    )
    ap.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="无 U 主数据或重复 U 的组不中断，该组文档原样写回（仍参与删后插）",
    )
    args = ap.parse_args()

    uri = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
    if not uri:
        print("请在 .env 中配置 MONGODB_URI（或 MONGO_URI）", file=sys.stderr)
        sys.exit(1)

    db_name = os.getenv("MONGODB_DB") or _extract_db_name_from_uri(uri)
    query = base_query()

    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    try:
        coll = client[db_name][COLLECTION]
        raw = list(
            coll.find(
                {
                    "language": LANGUAGE,
                    "$expr": {"$eq": [{"$size": "$mhr_codes"}, 4]},
                }
            )
        )
        docs = [d for d in raw if not _is_excluded_test_doc(d)]
        skipped = [d for d in raw if _is_excluded_test_doc(d)]
    finally:
        client.close()

    print(
        f"查询到 {len(docs)} 条 (language={LANGUAGE}, len(mhr_codes)=4, "
        f"已排除 mhr_name∈{sorted(EXCLUDED_MHR_NAMES)})"
    )
    if skipped:
        for d in skipped:
            codes = d.get("mhr_codes")
            print(f"  已跳过测试数据: mhr_name={d.get('mhr_name')!r}, mhr_codes={codes}")
    if not docs:
        print("无数据，退出")
        return

    groups = group_docs(docs)
    print(f"共 {len(groups)} 组")
    try:
        problems = validate_groups(groups, allow_incomplete=args.allow_incomplete)
        if problems:
            print("警告: 以下组未做 U 主同步（需 --allow-incomplete 才会写库）:")
            print("\n".join(problems))
        synced = build_synced_records(groups, allow_incomplete=args.allow_incomplete)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

    if len(synced) != len(docs):
        print(f"同步条数异常: {len(synced)} != {len(docs)}", file=sys.stderr)
        sys.exit(1)

    if not args.no_backup:
        backup_path = args.backup if args.backup.is_absolute() else PROJECT_ROOT / args.backup
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(
            json.dumps(docs, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"已备份原始数据 → {backup_path}")

    if args.dry_run:
        print(f"[dry-run] 将删除 {len(docs)} 条，插入 {len(synced)} 条 → {db_name}.{COLLECTION}")
        return

    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    try:
        coll = client[db_name][COLLECTION]
        del_result = coll.delete_many(query)
        ins_result = coll.insert_many(synced)
        print(f"已删除 {del_result.deleted_count} 条")
        print(f"已插入 {len(ins_result.inserted_ids)} 条")
    finally:
        client.close()


if __name__ == "__main__":
    main()
