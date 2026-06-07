#!/usr/bin/env python3
"""对比 output/{uid}_*.json 与 output/_mongo_pull/{uid}_*.json（或现场拉取）。

用法：
  # 先拉取再对比（推荐）
  .venv/bin/python scripts/insert_data/pull_mongo_persona_data.py
  .venv/bin/python scripts/insert_data/compare_mongo_with_output.py

  # 对比时现场连库拉取（不写 _mongo_pull）
  .venv/bin/python scripts/insert_data/compare_mongo_with_output.py --pull-inline

  .venv/bin/python scripts/insert_data/compare_mongo_with_output.py --uid 69aea6eeaf5e6cbf08027969
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from pymongo import MongoClient

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from mongo_persona_output_specs import (  # noqa: E402
    MONGO_PULL_DIR,
    OutputTypeSpec,
    build_query,
    load_persona_uids,
    normalize_doc,
    output_file_path,
    parse_type_names,
    pull_file_path,
    resolve_mongo_uri,
)


def _load_json_list(path: str) -> list[dict[str, Any]]:
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return [data]
    return list(data)


def _index_by_key(
    rows: list[dict[str, Any]],
    spec: OutputTypeSpec,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        key_val = row.get(spec.compare_key) or row.get("_id")
        if key_val is None:
            continue
        indexed[str(key_val)] = row
    return indexed


def _diff_summary(
    local_rows: list[dict],
    mongo_rows: list[dict],
    spec: OutputTypeSpec,
) -> dict[str, Any]:
    local_ix = _index_by_key(local_rows, spec)
    mongo_ix = _index_by_key(mongo_rows, spec)
    local_keys = set(local_ix)
    mongo_keys = set(mongo_ix)
    only_local = sorted(local_keys - mongo_keys)
    only_mongo = sorted(mongo_keys - local_keys)
    common = local_keys & mongo_keys
    mismatch_keys: list[str] = []
    for k in sorted(common):
        if local_ix[k] != mongo_ix[k]:
            mismatch_keys.append(k)
    return {
        "local_count": len(local_rows),
        "mongo_count": len(mongo_rows),
        "only_local": len(only_local),
        "only_mongo": len(only_mongo),
        "content_mismatch": len(mismatch_keys),
        "only_local_samples": only_local[:3],
        "only_mongo_samples": only_mongo[:3],
        "mismatch_samples": mismatch_keys[:3],
        "match": (
            not only_local
            and not only_mongo
            and not mismatch_keys
            and len(local_rows) == len(mongo_rows)
        ),
    }


def _fetch_mongo_rows(db, uid: str, spec: OutputTypeSpec) -> list[dict[str, Any]]:
    if spec.collection not in db.list_collection_names():
        return []
    cursor = db[spec.collection].find(build_query(uid, spec))
    rows = [normalize_doc(d) for d in cursor]
    rows.sort(key=lambda r: str(r.get(spec.compare_key) or r.get("_id") or ""))
    return rows


def _compare_uid(
    uid: str,
    specs: list[OutputTypeSpec],
    *,
    db,
    use_pull_dir: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for spec in specs:
        local_path = output_file_path(uid, spec)
        local_rows = _load_json_list(local_path)
        if use_pull_dir:
            mongo_rows = _load_json_list(pull_file_path(uid, spec))
        else:
            mongo_rows = _fetch_mongo_rows(db, uid, spec) if db is not None else []
        summary = _diff_summary(local_rows, mongo_rows, spec)
        summary.update(
            {
                "uid": uid,
                "type": spec.output_suffix,
                "collection": spec.collection,
                "local_path": local_path,
                "local_exists": os.path.isfile(local_path),
            }
        )
        results.append(summary)
    return results


def _print_report(results: list[dict[str, Any]]) -> bool:
    all_ok = True
    by_uid: dict[str, list[dict]] = {}
    for r in results:
        by_uid.setdefault(r["uid"], []).append(r)

    for uid, items in by_uid.items():
        print(f"\n=== {uid} ===")
        for item in items:
            tag = "OK" if item["match"] else "DIFF"
            if not item["local_exists"]:
                tag = "MISSING_LOCAL"
            if not item["match"]:
                all_ok = False
            print(
                f"  [{tag}] {item['type']} ({item['collection']}): "
                f"local={item['local_count']} mongo={item['mongo_count']} "
                f"仅local={item['only_local']} 仅mongo={item['only_mongo']} "
                f"同键不一致={item['content_mismatch']}"
            )
            if tag != "OK":
                if item.get("only_local_samples"):
                    print(f"      仅 output 有: {item['only_local_samples']}")
                if item.get("only_mongo_samples"):
                    print(f"      仅 Mongo 有: {item['only_mongo_samples']}")
                if item.get("mismatch_samples"):
                    print(f"      内容不一致键: {item['mismatch_samples']}")

    print("\n" + ("[OK] 全部类型一致" if all_ok else "[FAIL] 存在差异，见上文"))
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="对比 output 与 Mongo 拉取结果")
    parser.add_argument("--uid", default="")
    parser.add_argument("--types", default="", help="逗号分隔 output 后缀")
    parser.add_argument(
        "--pull-inline",
        action="store_true",
        help="不读 _mongo_pull，现场连库查询对比",
    )
    parser.add_argument("--output", default="", help="写入 JSON 报告路径")
    args = parser.parse_args()

    try:
        specs = parse_type_names(args.types)
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    uids = load_persona_uids()
    if args.uid.strip():
        uids = [u for u in uids if u == args.uid.strip()]

    use_pull = not args.pull_inline
    if use_pull and not os.path.isdir(MONGO_PULL_DIR):
        print(
            f"错误: 未找到 {MONGO_PULL_DIR}，请先运行 pull_mongo_persona_data.py",
            file=sys.stderr,
        )
        return 2

    db = None
    client = None
    if args.pull_inline:
        try:
            uri, db_name = resolve_mongo_uri()
            client = MongoClient(uri, serverSelectionTimeoutMS=15000)
            client.admin.command("ping")
            db = client[db_name]
            print(f"现场拉取对比，数据库: {db_name}")
        except Exception as exc:
            print(f"MongoDB 连接失败: {exc}", file=sys.stderr)
            return 2
    else:
        print(f"对比目录: output/ vs {MONGO_PULL_DIR}/")

    results: list[dict[str, Any]] = []
    for uid in uids:
        results.extend(
            _compare_uid(uid, specs, db=db, use_pull_dir=use_pull)
        )

    if client is not None:
        client.close()

    ok = _print_report(results)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"报告已写入: {args.output}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
