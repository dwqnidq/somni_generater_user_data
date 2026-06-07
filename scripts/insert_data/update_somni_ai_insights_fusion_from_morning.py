#!/usr/bin/env python3
"""将 {uid}_morning_alarm_insight.json 的 alarm_insight 回写到 Mongo somni_ai_insights.fusion_insight。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError

MORNING_FILE_SUFFIX = "_morning_alarm_insight.json"
DEFAULT_COLLECTION = "somni_ai_insights"
DEFAULT_SOURCE_DIR_NAME = "output"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from mongo_persona_output_specs import load_persona_uids  # noqa: E402


def _load_env_file_fallback(env_path: str) -> None:
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                key = k.strip()
                if not key or key in os.environ:
                    continue
                val = v.strip()
                if (val.startswith('"') and val.endswith('"')) or (
                    val.startswith("'") and val.endswith("'")
                ):
                    val = val[1:-1]
                os.environ[key] = val
    except OSError:
        return


def load_project_env() -> None:
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if load_dotenv is not None:
        load_dotenv(env_path)
    else:
        _load_env_file_fallback(env_path)


def extract_db_name_from_uri(uri: str, fallback: str = "Fullive") -> str:
    try:
        parsed = urlparse(uri)
        db_name = (parsed.path or "").lstrip("/")
        if db_name:
            return db_name.split("?")[0]
    except Exception:
        pass
    return fallback


def default_source_dir() -> str:
    return os.path.join(PROJECT_ROOT, DEFAULT_SOURCE_DIR_NAME)


@dataclass(frozen=True)
class FusionRow:
    uid: str
    record_date: str
    fusion_insight: str


def morning_file_path(source_dir: str, uid: str) -> str:
    return os.path.join(source_dir, f"{uid}{MORNING_FILE_SUFFIX}")


def load_morning_json(source_dir: str, uid: str) -> list[dict[str, Any]]:
    path = morning_file_path(source_dir, uid)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict)]


def rows_to_fusion_updates(raw_rows: list[dict[str, Any]], uid: str) -> list[FusionRow]:
    out: list[FusionRow] = []
    for row in raw_rows:
        row_uid = str(row.get("uid") or uid).strip()
        record_date = str(row.get("record_date") or "").strip()
        insight = row.get("alarm_insight")
        if not row_uid or not record_date:
            continue
        if insight is None:
            fusion_text = ""
        elif not isinstance(insight, str):
            fusion_text = str(insight)
        else:
            fusion_text = insight
        out.append(FusionRow(uid=row_uid, record_date=record_date, fusion_insight=fusion_text))
    return out


def apply_date_filters(
    rows: list[FusionRow],
    *,
    record_date: str,
    start_date: str,
    end_date: str,
) -> list[FusionRow]:
    filtered = rows
    if record_date:
        filtered = [r for r in filtered if r.record_date == record_date]
    if start_date:
        filtered = [r for r in filtered if r.record_date >= start_date]
    if end_date:
        filtered = [r for r in filtered if r.record_date <= end_date]
    return sorted(filtered, key=lambda x: (x.uid, x.record_date))


def build_update_ops(rows: list[FusionRow], language: str) -> list[UpdateOne]:
    ops: list[UpdateOne] = []
    for row in rows:
        flt: dict[str, Any] = {"uid": row.uid, "record_date": row.record_date}
        if language:
            flt["language"] = language
        ops.append(
            UpdateOne(flt, {"$set": {"fusion_insight": row.fusion_insight}}, upsert=False)
        )
    return ops


def resolve_mongo_uri(cli_uri: str) -> str:
    uri = cli_uri or os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or ""
    if not uri:
        raise RuntimeError("未配置 MONGODB_URI 或 MONGO_URI（.env），且未传 --uri")
    return uri


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "仅读取 {uid}_morning_alarm_insight.json，"
            "将其中 alarm_insight 写入 somni_ai_insights.fusion_insight。"
        )
    )
    parser.add_argument("--uid", default="", help="单个用户 uid（省略则处理配置中八人格）")
    parser.add_argument("--record-date", default="", help="仅更新单日 YYYY-MM-DD")
    parser.add_argument("--start-date", default="", help="开始日期 YYYY-MM-DD（可选）")
    parser.add_argument("--end-date", default="", help="结束日期 YYYY-MM-DD（可选）")
    parser.add_argument(
        "--source-dir",
        default=default_source_dir(),
        help=f"morning_alarm_insight 目录（默认 {DEFAULT_SOURCE_DIR_NAME}/）",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help=f"目标集合（默认 {DEFAULT_COLLECTION}）",
    )
    parser.add_argument("--uri", default="", help="Mongo URI（默认读环境变量）")
    parser.add_argument("--db", default="", help="数据库名（默认从 URI 推断）")
    parser.add_argument(
        "--language",
        default="zh",
        choices=["", "zh", "en"],
        help="文档语言过滤（默认 zh）",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不写库")
    return parser.parse_args()


def collect_rows_for_uid(source_dir: str, uid: str, args: argparse.Namespace) -> list[FusionRow]:
    raw = load_morning_json(source_dir, uid)
    parsed = rows_to_fusion_updates(raw, uid)
    return apply_date_filters(
        parsed,
        record_date=str(args.record_date).strip(),
        start_date=str(args.start_date).strip(),
        end_date=str(args.end_date).strip(),
    )


def run_for_uids(uids: list[str], args: argparse.Namespace) -> int:
    source_dir = os.path.abspath(args.source_dir)
    language = str(args.language).strip()

    all_rows: list[FusionRow] = []
    missing_files: list[str] = []
    for uid in uids:
        try:
            all_rows.extend(collect_rows_for_uid(source_dir, uid, args))
        except FileNotFoundError:
            missing_files.append(uid)

    if missing_files:
        print(f"跳过（无 morning 文件）: {', '.join(missing_files)}", file=sys.stderr)

    if not all_rows:
        print("没有可更新的 fusion_insight 数据")
        return 0 if not missing_files else 1

    ops = build_update_ops(all_rows, language)
    dates = sorted({r.record_date for r in all_rows})
    processed_uids = sorted({r.uid for r in all_rows})
    print(
        f"源: *{MORNING_FILE_SUFFIX}  配置/指定用户数: {len(uids)}  "
        f"有数据用户数: {len(processed_uids)}  待更新条数: {len(ops)}"
    )
    print(f"日期范围: {dates[0]} ~ {dates[-1]}（共 {len(dates)} 天）")
    if language:
        print(f"语言过滤: {language}")

    if args.dry_run:
        sample = all_rows[0]
        preview = sample.fusion_insight[:80]
        if len(sample.fusion_insight) > 80:
            preview += "…"
        print(
            f"dry-run 示例: uid={sample.uid}, date={sample.record_date}, "
            f"fusion_insight={preview!r}"
        )
        return 0

    mongo_uri = resolve_mongo_uri(str(args.uri).strip())
    db_name = str(args.db).strip() or os.getenv("MONGODB_DB") or extract_db_name_from_uri(mongo_uri)

    client = MongoClient(mongo_uri)
    try:
        db = client[db_name]
        if args.collection not in db.list_collection_names():
            print(f"集合 {args.collection!r} 不存在", file=sys.stderr)
            return 1
        result = db[args.collection].bulk_write(ops, ordered=False)
        print(
            f"更新完成: matched={result.matched_count}, "
            f"modified={result.modified_count}"
        )
        if result.matched_count < len(ops):
            print(
                f"警告: {len(ops) - result.matched_count} 条未匹配到文档（uid+record_date+language）",
                file=sys.stderr,
            )
    except BulkWriteError as exc:
        print(f"批量更新失败: {exc.details}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"更新失败: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
    return 0


def main() -> int:
    load_project_env()
    args = parse_args()

    if args.record_date and (args.start_date or args.end_date):
        print("参数冲突：--record-date 与 --start-date/--end-date 不能同时传", file=sys.stderr)
        return 1

    uid = str(args.uid).strip()
    if uid:
        uids = [uid]
    else:
        uids = load_persona_uids()
        if not uids:
            print("配置中未找到人格 uid（health_data_personas_config.json）", file=sys.stderr)
            return 1

    return run_for_uids(uids, args)


if __name__ == "__main__":
    sys.exit(main())
