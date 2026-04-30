#!/usr/bin/env python3
"""
审计睡眠离群日历：读取 config 中用户的 date 范围与人格编码，
调用 generate_health_data.build_sleep_outlier_mode_by_date，统计并可选落盘每日 mode。

说明：health_data.json 落盘前会去掉 good_sleep_day/bad_sleep_day，本脚本用于对照「应排多少 good/bad」
及在固定 --seed 下可复现的日期列表（与生成器使用同一函数与 random 顺序时一致）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
GEN_DIR = os.path.join(PROJECT_ROOT, "scripts", "generate_data")


def _bootstrap() -> None:
    for p in (PROJECT_ROOT, GEN_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.chdir(PROJECT_ROOT)


def _parse_date(s: str):
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def main() -> None:
    _bootstrap()
    import random

    import generate_health_data as gh

    ap = argparse.ArgumentParser(
        description="审计各用户睡眠离群日历（与 generate_health_data 排期一致）"
    )
    ap.add_argument(
        "--config",
        default="config/config.json",
        help="相对工程根目录的配置路径",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="固定 random 种子，便于与同种子的生成过程对照",
    )
    ap.add_argument(
        "--user-id",
        action="append",
        default=[],
        dest="user_ids",
        metavar="ID",
        help="只审计指定 user_id，可重复传入；省略则处理配置中全部用户",
    )
    ap.add_argument(
        "--dump-json",
        action="store_true",
        help="将每个用户的 record_date -> mode 写入 output/{{user_id}}_sleep_calendar_audit.json",
    )
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    cfg_path = os.path.join(PROJECT_ROOT, args.config)
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    users = cfg.get("user_profiles") or cfg
    wanted = {x.strip() for x in args.user_ids} if args.user_ids else None

    rows = []
    for u in users:
        uid = (u.get("user_id") or "").strip()
        if not uid:
            continue
        if wanted is not None and uid not in wanted:
            continue
        pt = (u.get("personalInformation") or {}).get("type") or "M-L-C"
        dc = u.get("date") or {}
        start_s, end_s = dc.get("start"), dc.get("end")
        if not start_s or not end_s:
            rows.append((uid, pt, 0, 0, 0, "missing date.start/end"))
            continue
        start = _parse_date(str(start_s))
        end = _parse_date(str(end_s))
        n_days = (end - start).days + 1
        cal = gh.build_sleep_outlier_mode_by_date(start, end, pt)
        n_bad = sum(1 for v in cal.values() if v == "bad")
        n_good = sum(1 for v in cal.values() if v == "good")
        rows.append((uid, pt, n_days, n_bad, n_good, ""))
        if args.dump_json:
            out_dir = os.path.join(PROJECT_ROOT, "output")
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f"{uid}_sleep_calendar_audit.json")
            payload = {
                "user_id": uid,
                "personality_type": pt,
                "date_start": start_s,
                "date_end": end_s,
                "n_days": n_days,
                "n_bad": n_bad,
                "n_good": n_good,
                "seed": args.seed,
                "calendar": cal,
            }
            with open(out_path, "w", encoding="utf-8") as wf:
                json.dump(payload, wf, ensure_ascii=False, indent=2)

    print("user_id | personality | n_days | n_bad | n_good | note")
    for uid, pt, n_days, n_bad, n_good, note in rows:
        print(f"{uid} | {pt} | {n_days} | {n_bad} | {n_good} | {note}".rstrip())

    if args.dump_json:
        print(f"\n已写入 output/*_sleep_calendar_audit.json（共 {len(rows)} 个用户）")


if __name__ == "__main__":
    main()
