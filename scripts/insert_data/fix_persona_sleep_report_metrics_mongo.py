#!/usr/bin/env python3
"""八人格 Mongo 睡眠报告结构修正：备份 → 按 health 公式校验 → 写回 somni_reports。

规则（与 sleep_report.sleep_score.build_sleep_structure_metrics 一致）：
  - percent：直接等于 health raw_data 四阶段占 TIB 的整数占比
  - minutes：TIB(bed_time→wake_up_time) × 上述占比分配
  - 删除 percent_of_net_sleep（通过整段替换 sleep_structure 实现）
  - 同步 sleep_summary.deep_sleep_minutes、各阶段 status

不以 output/ 为数据源；只读库内 somni_records + somni_reports。

推荐顺序（项目根，需 .env 中 MONGODB_URI）:
  python scripts/insert_data/fix_persona_sleep_report_metrics_mongo.py backup
  # 默认备份到 scripts/backup2/
  python scripts/insert_data/fix_persona_sleep_report_metrics_mongo.py apply --dry-run
  python scripts/insert_data/fix_persona_sleep_report_metrics_mongo.py apply --force

或一步（先备份再 apply）:
  python scripts/insert_data/fix_persona_sleep_report_metrics_mongo.py run --force
  python scripts/insert_data/fix_persona_sleep_report_metrics_mongo.py run --force --dry-run

仅同步本地 output（不改库）:
  python scripts/insert_data/fix_persona_sleep_report_metrics_mongo.py sync-local
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
GEN_DATA_DIR = os.path.join(PROJECT_ROOT, "scripts", "generate_data")
WRITE_BACK_DIR = os.path.join(GEN_DATA_DIR, "write_back")
for p in (GEN_DATA_DIR, SCRIPT_DIR, WRITE_BACK_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

_BUILD_FN = None


def _mongo():
    from pymongo import MongoClient, UpdateOne

    return MongoClient, UpdateOne


def _normalize_doc(doc: dict) -> dict:
    from mongo_persona_output_specs import normalize_doc

    return normalize_doc(doc)


def _load_persona_uids() -> list[str]:
    from mongo_persona_output_specs import load_persona_uids

    return load_persona_uids()


def _resolve_mongo_uri() -> tuple[str, str]:
    from mongo_persona_output_specs import resolve_mongo_uri

    return resolve_mongo_uri()


def _build_sleep_structure_metrics():
    global _BUILD_FN
    if _BUILD_FN is None:
        from import_sleep_metrics import load_build_sleep_structure_metrics

        _BUILD_FN = load_build_sleep_structure_metrics()
    return _BUILD_FN

DEFAULT_BACKUP_DIR = os.path.join(PROJECT_ROOT, "scripts", "backup2")
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
COLLECTION_REPORTS = "somni_reports"
COLLECTION_HEALTH = "somni_records"
STAGE_KEYS = ("awake", "deep_sleep", "light_sleep", "rem_sleep")
REM_SLEEP_KEYS = ("deep_sleep", "light_sleep", "rem_sleep")


def _load_sleep_standard() -> dict:
    path = os.path.join(PROJECT_ROOT, "config", "config.json")
    if not os.path.isfile(path):
        return {"deep": [15, 25], "light": [45, 60], "rem": [20, 25], "awake": 10}
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg.get(
        "sleepStandard",
        {"deep": [15, 25], "light": [45, 60], "rem": [20, 25], "awake": 10},
    )


def _backup_path(backup_dir: str, uid: str, suffix: str) -> str:
    return os.path.join(backup_dir, f"{uid}_{suffix}.json")


def _write_json(path: str, data: object) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _stage_minutes_from_report(report: dict) -> dict[str, int] | None:
    st = (report.get("quality_analysis") or {}).get("sleep_structure")
    if not isinstance(st, dict):
        return None
    out: dict[str, int] = {}
    for key in STAGE_KEYS:
        block = st.get(key)
        if not isinstance(block, dict):
            return None
        try:
            out[key] = int(block.get("minutes") or 0)
        except (TypeError, ValueError):
            return None
    return out


def _stage_minutes_from_structure(structure: dict) -> dict[str, int]:
    return {key: int((structure.get(key) or {}).get("minutes") or 0) for key in STAGE_KEYS}


def _has_percent_of_net_sleep(report: dict) -> bool:
    st = (report.get("quality_analysis") or {}).get("sleep_structure") or {}
    for key in REM_SLEEP_KEYS:
        blk = st.get(key)
        if isinstance(blk, dict) and blk.get("percent_of_net_sleep") is not None:
            return True
    return False


def _report_structure_matches(report: dict, structure: dict, deep_minutes: int) -> bool:
    current = _stage_minutes_from_report(report)
    if current is None:
        return False
    if current != _stage_minutes_from_structure(structure):
        return False
    try:
        summary_deep = int((report.get("sleep_summary") or {}).get("deep_sleep_minutes"))
    except (TypeError, ValueError):
        return False
    if summary_deep != int(deep_minutes):
        return False
    st = (report.get("quality_analysis") or {}).get("sleep_structure") or {}
    for key in STAGE_KEYS:
        cur_blk = st.get(key) if isinstance(st.get(key), dict) else {}
        exp_blk = structure.get(key) or {}
        if int(cur_blk.get("percent") or -1) != int(exp_blk.get("percent") or -2):
            return False
        if str(cur_blk.get("status") or "") != str(exp_blk.get("status") or ""):
            return False
    if _has_percent_of_net_sleep(report):
        return False
    return True


def _update_reasons(report: dict, structure: dict, deep_minutes: int) -> list[str]:
    reasons: list[str] = []
    if _has_percent_of_net_sleep(report):
        reasons.append("含percent_of_net_sleep")
    current = _stage_minutes_from_report(report)
    expected = _stage_minutes_from_structure(structure)
    if current is None:
        reasons.append("sleep_structure缺失或非法")
    elif current != expected:
        reasons.append("minutes不一致")
    try:
        if int((report.get("sleep_summary") or {}).get("deep_sleep_minutes")) != int(deep_minutes):
            reasons.append("summary.deep_sleep_minutes不一致")
    except (TypeError, ValueError):
        reasons.append("summary.deep_sleep_minutes缺失")
    st = (report.get("quality_analysis") or {}).get("sleep_structure") or {}
    for key in STAGE_KEYS:
        cur_blk = st.get(key) if isinstance(st.get(key), dict) else {}
        exp_blk = structure.get(key) or {}
        if int(cur_blk.get("percent") or -1) != int(exp_blk.get("percent") or -2):
            reasons.append(f"{key}.percent不一致")
            break
    return reasons


def _normalize_language(doc: dict) -> str:
    language = str(doc.get("language", "")).lower()
    if language in ("zh", "en"):
        return language
    old = str(doc.get("lang", "")).lower()
    if old in ("zh", "en"):
        return old
    return "zh"


def _language_allowed(doc: dict, languages: set[str] | None) -> bool:
    if not languages:
        return True
    return _normalize_language(doc) in languages


def cmd_backup(client, db_name: str, backup_dir: str, uids: list[str]) -> int:
    db = client[db_name]
    for coll in (COLLECTION_REPORTS, COLLECTION_HEALTH):
        if coll not in db.list_collection_names():
            print(f"错误: 集合不存在 {coll}", file=sys.stderr)
            return 2

    os.makedirs(backup_dir, exist_ok=True)
    meta = {
        "backed_up_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "db": db_name,
        "uids": uids,
        "collections": {COLLECTION_REPORTS: "sleep_report", COLLECTION_HEALTH: "health_data"},
    }
    _write_json(os.path.join(backup_dir, "_backup_meta.json"), meta)

    total_rep = total_health = 0
    for uid in uids:
        health_rows = [
            _normalize_doc(d)
            for d in db[COLLECTION_HEALTH].find({"uid": uid}).sort("record_date", 1)
        ]
        report_rows = [
            _normalize_doc(d)
            for d in db[COLLECTION_REPORTS].find({"uid": uid}).sort("record_date", 1)
        ]
        _write_json(_backup_path(backup_dir, uid, "health_data.json"), health_rows)
        _write_json(_backup_path(backup_dir, uid, "sleep_report.json"), report_rows)
        total_health += len(health_rows)
        total_rep += len(report_rows)
        print(f"  {uid[:12]}… health={len(health_rows)} report={len(report_rows)}")

    print(f"\n备份完成 → {backup_dir}")
    print(f"  health 共 {total_health} 条, sleep_report 共 {total_rep} 条")
    return 0


def cmd_apply(
    client,
    db_name: str,
    uids: list[str],
    *,
    dry_run: bool,
    force: bool,
    sleep_standard: dict,
    languages: set[str] | None,
) -> int:
    db = client[db_name]
    coll = db[COLLECTION_REPORTS]
    health_coll = db[COLLECTION_HEALTH]

    stats = {
        "checked": 0,
        "ok_skip": 0,
        "fixed": 0,
        "missing_health": 0,
        "bad_report_shape": 0,
        "lang_filtered": 0,
    }
    _, UpdateOne = _mongo()
    ops: list = []

    for uid in uids:
        health_by_date = {
            str(d.get("record_date") or ""): _normalize_doc(d)
            for d in health_coll.find({"uid": uid})
            if d.get("record_date")
        }
        for report in coll.find({"uid": uid}):
            stats["checked"] += 1
            report_norm = _normalize_doc(report)
            if not _language_allowed(report_norm, languages):
                stats["lang_filtered"] += 1
                continue

            rd = str(report_norm.get("record_date") or "")
            if not rd:
                stats["bad_report_shape"] += 1
                continue

            health_row = health_by_date.get(rd)
            if not health_row:
                stats["missing_health"] += 1
                continue

            structure, deep_minutes, *_ = _build_sleep_structure_metrics()(
                health_row, sleep_standard
            )
            if not force and _report_structure_matches(report_norm, structure, deep_minutes):
                stats["ok_skip"] += 1
                continue

            language = _normalize_language(report_norm)
            reasons = _update_reasons(report_norm, structure, deep_minutes)
            if dry_run:
                cur = _stage_minutes_from_report(report_norm) or {}
                exp = _stage_minutes_from_structure(structure)
                deep_blk = structure.get("deep_sleep") or {}
                print(
                    f"  [将更新] uid={uid[:12]}… date={rd} lang={language} "
                    f"原因={','.join(reasons) or 'force'} | "
                    f"deep分 {cur.get('deep_sleep')}→{exp.get('deep_sleep')} "
                    f"percent→{deep_blk.get('percent')}"
                )
            else:
                ops.append(
                    UpdateOne(
                        {"uid": uid, "record_date": rd, "language": language},
                        {
                            "$set": {
                                "quality_analysis.sleep_structure": structure,
                                "sleep_summary.deep_sleep_minutes": deep_minutes,
                            }
                        },
                    )
                )
            stats["fixed"] += 1

    if not dry_run and ops:
        result = coll.bulk_write(ops, ordered=False)
        print(
            f"\nMongo 写入: matched={result.matched_count} modified={result.modified_count}"
        )
    elif dry_run:
        print("\n(dry-run，未写入 Mongo)")

    print(
        f"\n统计: 检查 {stats['checked']} 条 | 正确跳过 {stats['ok_skip']} | "
        f"将修正 {stats['fixed']} | 缺 health {stats['missing_health']} | "
        f"结构异常 {stats['bad_report_shape']} | 语言过滤 {stats['lang_filtered']}"
        + (" | mode=force" if force else "")
    )
    return 0


def cmd_sync_local(output_dir: str, uids: list[str]) -> int:
    """仅更新本地 output/*_health_data + *_sleep_report.json（不改 Mongo）。"""
    from refresh_sleep_report_stage_metrics import (  # noqa: E402
        refresh_sleep_report_stage_metrics_for_uid,
    )

    sleep_standard = _load_sleep_standard()
    results = []
    for uid in uids:
        results.append(
            refresh_sleep_report_stage_metrics_for_uid(
                uid, output_dir, sleep_standard
            )
        )
    total = sum(int(r.get("days_updated") or 0) for r in results)
    print(f"本地 output 已更新 {len(results)} 个用户、共 {total} 天")
    for r in results:
        print(f"  {r.get('uid')}: {r.get('days_updated', 0)} 天")
    return 0


def _parse_languages(raw: str) -> set[str] | None:
    text = (raw or "").strip().lower()
    if not text or text == "all":
        return None
    langs = {x.strip() for x in text.split(",") if x.strip()}
    bad = langs - {"zh", "en"}
    if bad:
        raise ValueError(f"不支持的语言: {', '.join(sorted(bad))}")
    return langs


def _filter_uids(all_uids: list[str], uid_arg: str) -> list[str]:
    if not uid_arg.strip():
        return all_uids
    want = uid_arg.strip()
    matched = [u for u in all_uids if u == want]
    if not matched:
        raise ValueError(f"配置中无 uid={want}")
    return matched


def _connect_mongo():
    MongoClient, _ = _mongo()
    uri, db_name = _resolve_mongo_uri()
    client = MongoClient(uri, serverSelectionTimeoutMS=20000)
    client.admin.command("ping")
    return client, db_name


def main() -> int:
    parser = argparse.ArgumentParser(
        description="八人格睡眠报告 sleep_structure 修正（Mongo + 可选本地 output）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--uid", default="", help="仅处理指定 uid")

    p_backup = sub.add_parser("backup", help="① 从 Mongo 备份 health + sleep_report")
    add_common(p_backup)
    p_backup.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR)

    p_apply = sub.add_parser("apply", help="② 按公式校验并写回 somni_reports")
    add_common(p_apply)
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.add_argument(
        "--force",
        action="store_true",
        help="不跳过「看似正确」的记录，强制重写 sleep_structure（可清掉 percent_of_net_sleep）",
    )
    p_apply.add_argument(
        "--language",
        default="all",
        help="只处理指定语言，逗号分隔 zh,en；默认 all",
    )

    p_run = sub.add_parser("run", help="① backup + ② apply（推荐首次全量修正用 --force）")
    add_common(p_run)
    p_run.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR)
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--skip-backup", action="store_true", help="跳过备份（仅当你已备份过）")
    p_run.add_argument("--language", default="all")

    p_local = sub.add_parser("sync-local", help="仅同步本地 output 睡眠报告（不改库）")
    add_common(p_local)
    p_local.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)

    args = parser.parse_args()

    try:
        uids = _filter_uids(_load_persona_uids(), getattr(args, "uid", ""))
        languages = _parse_languages(getattr(args, "language", "all"))
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    if args.command == "sync-local":
        return cmd_sync_local(os.path.abspath(args.output_dir), uids)

    try:
        client, db_name = _connect_mongo()
    except Exception as exc:
        print(f"MongoDB 连接失败: {exc}", file=sys.stderr)
        return 2

    sleep_standard = _load_sleep_standard()
    try:
        if args.command == "backup":
            return cmd_backup(client, db_name, os.path.abspath(args.backup_dir), uids)
        if args.command == "run":
            if not args.skip_backup:
                print("=== 步骤 1/2: 备份 ===")
                code = cmd_backup(client, db_name, os.path.abspath(args.backup_dir), uids)
                if code != 0:
                    return code
            print("\n=== 步骤 2/2: 应用修正 ===")
            return cmd_apply(
                client,
                db_name,
                uids,
                dry_run=args.dry_run,
                force=args.force,
                sleep_standard=sleep_standard,
                languages=languages,
            )
        return cmd_apply(
            client,
            db_name,
            uids,
            dry_run=args.dry_run,
            force=getattr(args, "force", False),
            sleep_standard=sleep_standard,
            languages=languages,
        )
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
