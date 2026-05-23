"""
从 beijing_sleep_map_multi_user.json 聚合生成两份输出文件：

  1. somni_sleep_analysis（个人 × 日）
     output/somni_sleep_analysis.json
     - 严格对齐表结构
     - deep_sleep_ratio 修正为 0~1 小数
     - is_env_sensitive 按综合分重新判定（方案 B）

  2. somni_sleep_district（区级 × 日）
     output/somni_sleep_district.json
     - 按 (district_code, stats_date) 聚合
     - score / sleep_seconds / deep_sleep_ratio / sensitive_user_ratio

用法：
  python scripts/generate_data/generate_sleep_map_aggregated.py
  python scripts/generate_data/generate_sleep_map_aggregated.py \\
      --input output/beijing_sleep_map_multi_user.json \\
      --seed 42
"""

import argparse
import json
import os
import random
import sys
import time as _time
from collections import defaultdict
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

# ─── 环境敏感判定（方案 B） ────────────────────────────────────────────────────
# 低分(40-62): 70%  中分(63-81): 30%  高分(82-98): 10%

def is_sensitive_by_score(score: int) -> bool:
    if score <= 62:
        prob = 0.70
    elif score <= 81:
        prob = 0.30
    else:
        prob = 0.10
    return random.random() < prob


# ─── ObjectId 生成 ─────────────────────────────────────────────────────────────

def make_object_id(ts=None) -> str:
    if ts is None:
        ts = int(_time.time())
    ts_hex = format(ts & 0xFFFFFFFF, "08x")
    rand_hex = "".join(random.choices("0123456789abcdef", k=16))
    return ts_hex + rand_hex


# ─── somni_sleep_analysis 输出 ────────────────────────────────────────────────

def build_analysis_record(raw: dict, now_iso: str) -> dict:
    score = raw["score"]
    return {
        "_id":                  raw["_id"],
        "uid":                  raw["uid"],
        "stats_date":           raw["stats_date"],
        "region":               raw["region"],
        "user_name":            raw.get("user_name", ""),
        "score":                score,
        "sleep_seconds":        raw["sleep_seconds"],
        "deep_sleep_seconds":   raw["deep_sleep_seconds"],
        "deep_sleep_ratio":     round(raw["deep_sleep_ratio"] / 100, 4),
        "evaluation":           raw.get("evaluation", ""),
        "dimensions":           raw["dimensions"],
        "is_env_sensitive":     is_sensitive_by_score(score),
        "create_time":          raw.get("create_time", now_iso),
        "update_time":          now_iso,
    }


# ─── somni_sleep_district 输出 ────────────────────────────────────────────────

def filter_records_by_stats_date(
    records: list,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list:
    """按 stats_date 闭区间过滤；start/end 为空则不限制该侧。"""
    if not start_date and not end_date:
        return records
    out: list = []
    for r in records:
        d = str(r.get("stats_date") or "")
        if start_date and d < start_date:
            continue
        if end_date and d > end_date:
            continue
        out.append(r)
    return out


def build_district_records(raw_list: list, now_iso: str) -> list:
    groups = defaultdict(list)
    for r in raw_list:
        key = (r["region"]["district_code"], r["stats_date"])
        groups[key].append(r)

    records = []
    for (district_code, stats_date), grp in groups.items():
        n = len(grp)
        avg_score       = round(sum(r["score"] for r in grp) / n)
        avg_sleep_sec   = round(sum(r["sleep_seconds"] for r in grp) / n)
        avg_deep_ratio  = round(sum(r["deep_sleep_ratio"] for r in grp) / n)

        # sensitive_user_ratio：用已生成的 is_env_sensitive 标记统计占比
        # （需在 analysis 生成之后调用，这里直接用原始 score 重新判定一次保持一致性）
        sensitive_count = sum(1 for r in grp if is_sensitive_by_score(r["score"]))
        sensitive_ratio = round(sensitive_count / n, 1)

        first = grp[0]
        records.append({
            "_id":                  make_object_id(),
            "stats_date":           stats_date,
            "region": {
                "province_code": first["region"]["province_code"],
                "city_code":     first["region"]["city_code"],
                "district_code": district_code,
            },
            "score":                avg_score,
            "sleep_seconds":        avg_sleep_sec,
            "deep_sleep_ratio":     avg_deep_ratio,
            "sensitive_user_ratio": sensitive_ratio,
            "heatmap_url":          "",
            "create_time":          now_iso,
            "update_time":          now_iso,
        })

    records.sort(key=lambda r: (r["stats_date"], r["region"]["district_code"]))
    return records


# ─── 命令行参数 ────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="聚合生成 somni_sleep_analysis / somni_sleep_district")
    p.add_argument("--input",  default="output/beijing_sleep_map_multi_user.json")
    p.add_argument("--out-analysis",  default="output/somni_sleep_analysis.json")
    p.add_argument("--out-district",  default="output/somni_sleep_district.json")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--start",
        default="",
        help="仅保留 stats_date >= 该日期的记录（YYYY-MM-DD，空表示不限制）",
    )
    p.add_argument(
        "--end",
        default="",
        help="仅保留 stats_date <= 该日期的记录（YYYY-MM-DD，空表示不限制）",
    )
    return p.parse_args()


# ─── 主流程 ───────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    if args.seed:
        random.seed(args.seed)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    print("读取源数据：{}".format(args.input))
    with open(os.path.join(PROJECT_ROOT, args.input), encoding="utf-8") as f:
        raw_data = json.load(f)
    n_before = len(raw_data)
    start_s = (args.start or "").strip()
    end_s = (args.end or "").strip()
    if start_s or end_s:
        raw_data = filter_records_by_stats_date(raw_data, start_s or None, end_s or None)
        print(
            "  日期过滤 {} ~ {}：{:,} → {:,} 条".format(
                start_s or "(不限)",
                end_s or "(不限)",
                n_before,
                len(raw_data),
            )
        )
        if not raw_data:
            print("错误：过滤后无记录", file=sys.stderr)
            sys.exit(1)
    else:
        print("  共 {:,} 条记录".format(n_before))

    # ── 文件一：somni_sleep_analysis ──────────────────────────────────────────
    print("\n生成 somni_sleep_analysis ...")
    analysis_records = [build_analysis_record(r, now_iso) for r in raw_data]
    analysis_records.sort(key=lambda r: (
        r["stats_date"],
        r["region"]["district_code"],
        -r["score"],
    ))

    out_analysis = os.path.join(PROJECT_ROOT, args.out_analysis)
    os.makedirs(os.path.dirname(out_analysis), exist_ok=True)
    with open(out_analysis, "w", encoding="utf-8") as f:
        json.dump(analysis_records, f, ensure_ascii=False, indent=2)
    print("  {:,} 条 → {}".format(len(analysis_records), out_analysis))

    # ── 文件二：somni_sleep_district ──────────────────────────────────────────
    print("\n生成 somni_sleep_district ...")
    district_records = build_district_records(raw_data, now_iso)

    out_district = os.path.join(PROJECT_ROOT, args.out_district)
    with open(out_district, "w", encoding="utf-8") as f:
        json.dump(district_records, f, ensure_ascii=False, indent=2)
    print("  {:,} 条 → {}".format(len(district_records), out_district))

    # ── 摘要 ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 56)
    print("somni_sleep_analysis  {:>7,} 条".format(len(analysis_records)))
    print("somni_sleep_district  {:>7,} 条".format(len(district_records)))

    scores = [r["score"] for r in district_records]
    print("区级综合分范围: {} ~ {}".format(min(scores), max(scores)))
    ratios = [r["sensitive_user_ratio"] for r in district_records]
    print("区级敏感占比范围: {:.1%} ~ {:.1%}".format(min(ratios), max(ratios)))
    print("=" * 56)


if __name__ == "__main__":
    main()
