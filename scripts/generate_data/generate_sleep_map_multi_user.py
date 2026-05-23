"""
生成多用户多日期睡眠地图数据（somni_sleep_analysis）。

规则：
  - 每个区分配 --users-per-district 个用户，UID 为 ObjectId 格式随机生成
  - 日期范围 --start 到 --end，每用户每天一条记录
  - 用户质量分层（固定到用户）：高分少 ~8%、中分适量 ~27%、低分最多 ~65%
  - 同一区同一日期：不同用户综合分自然呈由高到低分布
  - city_avg / city_score 按 (区, 日期) 计算

用法：
  python scripts/generate_data/generate_sleep_map_multi_user.py
  python scripts/generate_data/generate_sleep_map_multi_user.py \\
      --users-per-district 13 --start 2026-01-01 --end 2026-03-31 --seed 42
"""

import argparse
import json
import math
import os
import random
import sys
import struct
import time as _time
from datetime import date, timedelta, datetime, timezone
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from utils import format_sleep_map_pool_user_name  # noqa: E402

# ─── 北京市行政区划 ───────────────────────────────────────────────────────────

PROVINCE_CODE = "110000"
CITY_CODE = "110100"

DISTRICTS = [
    {"name": "东城区",   "district_code": "110101"},
    {"name": "西城区",   "district_code": "110102"},
    {"name": "朝阳区",   "district_code": "110105"},
    {"name": "丰台区",   "district_code": "110106"},
    {"name": "石景山区", "district_code": "110107"},
    {"name": "海淀区",   "district_code": "110108"},
    {"name": "门头沟区", "district_code": "110109"},
    {"name": "房山区",   "district_code": "110111"},
    {"name": "通州区",   "district_code": "110112"},
    {"name": "顺义区",   "district_code": "110113"},
    {"name": "昌平区",   "district_code": "110114"},
    {"name": "大兴区",   "district_code": "110115"},
    {"name": "怀柔区",   "district_code": "110116"},
    {"name": "平谷区",   "district_code": "110117"},
    {"name": "密云区",   "district_code": "110118"},
    {"name": "延庆区",   "district_code": "110119"},
]

# 各维权重（产品规格）
WEIGHTS = {
    "sleep_duration":     0.25,
    "deep_sleep":         0.25,
    "abnormal_events":    0.20,
    "sleep_efficiency":   0.15,
    "routine_regularity": 0.15,
}

# ─── 用户质量分层配置 ─────────────────────────────────────────────────────────
#
# 每层定义目标综合分区间及各维底层参数的采样范围，最终用拒绝采样保证落在区间内。
#
# 分层比例：高分 8%、中分 27%、低分 65%（对应产品要求的分布态势）

TIERS = {
    "very_high": {
        "prob":        0.008,
        "score_range": (82, 98),
        # 底层参数范围（用于 gen_params_for_tier）
        "sleep_sec":   (27000, 32400),   # 7.5-9h 满分区间
        "deep_ratio":  (0.22, 0.30),     # ≥22%，高深睡
        "onset_sec":   (300, 900),       # 5-15min，快速入睡
        "abn_weights": [55, 28, 10, 4, 2, 1, 0, 0, 0],   # 事件次数 0-8
        "fluctuation": (5, 30),          # 波动小，规律性好
    },
    "high": {
        "prob":        0.014,
        "score_range": (70, 81),
        "sleep_sec":   (23400, 28800),   # 6.5-8h
        "deep_ratio":  (0.14, 0.20),     # 14-20%
        "onset_sec":   (900, 2400),      # 15-40min
        "abn_weights": [18, 22, 22, 16, 10, 7, 3, 2, 0],
        "fluctuation": (30, 90),
    },
    "medium": {
        "prob":        0.010,
        "score_range": (60, 69),
        "sleep_sec":   (21600, 27000),   # 6-7.5h
        "deep_ratio":  (0.10, 0.16),     # 10-16%
        "onset_sec":   (1500, 3300),     # 25-55min
        "abn_weights": [10, 15, 20, 18, 15, 12, 6, 3, 1],
        "fluctuation": (50, 130),
    },
    "low": {
        "prob":        0.965,
        "score_range": (30, 59),
        "sleep_sec":   (14400, 27000),   # 4-7.5h，偏短
        "deep_ratio":  (0.03, 0.14),     # 深睡不足
        "onset_sec":   (1800, 5400),     # 30-90min，入睡慢
        "abn_weights": [5, 8, 12, 15, 18, 16, 13, 8, 5],
        "fluctuation": (80, 200),        # 作息不规律
    },
}

TIER_NAMES = list(TIERS.keys())
TIER_PROBS  = [TIERS[t]["prob"] for t in TIER_NAMES]


# ─── 五维评分公式 ─────────────────────────────────────────────────────────────

def score_sleep_duration(sleep_sec):
    s4, s7, s9 = 14400, 25200, 32400
    if sleep_sec < s4:
        return 0
    if sleep_sec <= s7:
        deficit = (s7 - sleep_sec) / 1800.0
        return max(0, round(100 - deficit * 10))
    if sleep_sec <= s9:
        return 100
    excess = (sleep_sec - s9) / 1800.0
    return max(0, round(100 - excess * 5))


def score_deep_sleep(deep_sec, ratio):
    if deep_sec < 1800:
        return 0
    if ratio >= 0.20:
        return 100
    if ratio >= 0.15:
        return 80
    if ratio >= 0.10:
        return 60
    return 40


def score_abnormal_events(sleep_sec, abn_duration, abn_count):
    if sleep_sec == 0:
        return 0
    basic_ratio = (sleep_sec - abn_duration) / sleep_sec
    if abn_count <= 2:
        final_ratio = basic_ratio
    elif abn_count <= 5:
        final_ratio = basic_ratio * 0.8
    else:
        final_ratio = basic_ratio * 0.6
    if final_ratio < 0.30:
        return 0
    return min(100, round(final_ratio * 100))


def score_sleep_efficiency(onset_sec):
    if onset_sec <= 900:
        return 100
    if onset_sec <= 1800:
        return 80
    if onset_sec <= 2700:
        return 60
    if onset_sec <= 3600:
        return 40
    return 0


def score_routine_regularity(fluctuation_min):
    if fluctuation_min <= 30:
        return 100
    if fluctuation_min <= 120:
        return math.floor(100 * (120 - fluctuation_min) / 90)
    return 0


def comprehensive_score(dim_scores):
    return round(
        dim_scores["sleep_duration"]       * WEIGHTS["sleep_duration"]
        + dim_scores["deep_sleep"]         * WEIGHTS["deep_sleep"]
        + dim_scores["abnormal_events"]    * WEIGHTS["abnormal_events"]
        + dim_scores["sleep_efficiency"]   * WEIGHTS["sleep_efficiency"]
        + dim_scores["routine_regularity"] * WEIGHTS["routine_regularity"]
    )


def routine_text(score):
    if score >= 90:
        return "Excellent"
    if score >= 70:
        return "Great"
    if score >= 50:
        return "Good"
    if score >= 30:
        return "Fair"
    return "Poor"


# ─── ID 生成 ──────────────────────────────────────────────────────────────────

def make_object_id(ts=None):
    """生成 MongoDB ObjectId 格式的 24 位十六进制字符串。
    前 4 字节为 Unix 时间戳，后 8 字节随机。
    """
    if ts is None:
        ts = int(_time.time())
    ts_hex = format(ts & 0xFFFFFFFF, "08x")
    rand_hex = "".join(random.choices("0123456789abcdef", k=16))
    return ts_hex + rand_hex


# ─── 分层参数生成器 ───────────────────────────────────────────────────────────

def gen_params_for_tier(tier_cfg):
    """在指定分层的参数范围内随机采样一组底层参数。"""
    sleep_sec = random.randint(*tier_cfg["sleep_sec"])
    deep_ratio = random.uniform(*tier_cfg["deep_ratio"])
    deep_sec = round(sleep_sec * deep_ratio)
    actual_ratio = deep_sec / sleep_sec if sleep_sec else 0.0

    onset_sec = random.randint(*tier_cfg["onset_sec"])

    abn_count = random.choices(range(9), weights=tier_cfg["abn_weights"])[0]
    if abn_count == 0:
        abn_duration = 0
    else:
        max_frac = min(0.60, abn_count * 0.09)
        frac = random.uniform(0.005 * abn_count, max_frac)
        abn_duration = round(sleep_sec * frac)

    fluctuation = random.uniform(*tier_cfg["fluctuation"])

    return sleep_sec, deep_sec, actual_ratio, onset_sec, abn_count, abn_duration, fluctuation


def gen_dim_scores(sleep_sec, deep_sec, actual_ratio, onset_sec, abn_count, abn_duration, fluctuation):
    return {
        "sleep_duration":     score_sleep_duration(sleep_sec),
        "deep_sleep":         score_deep_sleep(deep_sec, actual_ratio),
        "abnormal_events":    score_abnormal_events(sleep_sec, abn_duration, abn_count),
        "sleep_efficiency":   score_sleep_efficiency(onset_sec),
        "routine_regularity": score_routine_regularity(fluctuation),
    }


def sample_record_for_tier(tier_name, max_tries=200):
    """拒绝采样：生成一组落在该分层目标得分区间内的参数。"""
    tier_cfg = TIERS[tier_name]
    lo, hi = tier_cfg["score_range"]
    for _ in range(max_tries):
        params = gen_params_for_tier(tier_cfg)
        sleep_sec, deep_sec, actual_ratio, onset_sec, abn_count, abn_duration, fluctuation = params
        dim_scores = gen_dim_scores(*params)
        total = comprehensive_score(dim_scores)
        if lo <= total <= hi:
            return params, dim_scores, total
    # 兜底：返回最后一次结果，综合分夹到 [40, 98]
    total = max(40, min(98, total))
    return params, dim_scores, total


# ─── 用户档案 ─────────────────────────────────────────────────────────────────

def create_user_profiles(district, n_users, reg_ts_base):
    """
    为一个区创建 n_users 个用户档案，按分层比例分配质量层。
    返回 list of dict，每个 dict 含 uid / tier。
    """
    tiers = random.choices(TIER_NAMES, weights=TIER_PROBS, k=n_users)
    profiles = []
    for idx, tier in enumerate(tiers, start=1):
        uid = make_object_id(ts=reg_ts_base + random.randint(0, 86400 * 30))
        profiles.append({
            "uid":       uid,
            "tier":      tier,
            "district":  district,
            "user_name": format_sleep_map_pool_user_name(idx),
        })
    return profiles


# ─── 单条文档生成（带城市均值占位） ──────────────────────────────────────────

def make_daily_record(profile, stats_date_str, now_iso):
    tier_name = profile["tier"]
    district  = profile["district"]
    (sleep_sec, deep_sec, actual_ratio,
     onset_sec, abn_count, abn_duration, fluctuation), dim_scores, total = sample_record_for_tier(tier_name)

    return {
        "_id":                make_object_id(),
        "uid":                profile["uid"],
        "stats_date":         stats_date_str,
        "region": {
            "province_code": PROVINCE_CODE,
            "city_code":     CITY_CODE,
            "district_code": district["district_code"],
        },
        "user_name":          profile["user_name"],
        "score":              total,
        "sleep_seconds":      sleep_sec,
        "deep_sleep_seconds": deep_sec,
        "deep_sleep_ratio":   round(actual_ratio * 100),
        "evaluation":         "睡眠综合评价 {} · {}".format(district["name"], stats_date_str),
        "is_env_sensitive":   random.random() < 0.30,
        "create_time":        now_iso,
        "update_time":        now_iso,
        # 临时字段，city 均值计算后填充并删除
        "_dim_scores": dim_scores,
        "_raw": {
            "onset_sec":    onset_sec,
            "abn_count":    abn_count,
            "abn_duration": abn_duration,
            "fluctuation":  round(fluctuation, 2),
        },
    }


# ─── 城市均值附加 ─────────────────────────────────────────────────────────────

def attach_city_fields(records):
    """
    对 (district_code, stats_date) 分组计算各维均值，写入 dimensions 子文档，
    删除临时字段 _dim_scores / _raw。
    """
    groups = defaultdict(list)
    for r in records:
        key = (r["region"]["district_code"], r["stats_date"])
        groups[key].append(r)

    result = []
    for (dc, sd), grp in groups.items():
        n = len(grp)
        city_avg = {
            "deep_ratio": sum(r["deep_sleep_ratio"] for r in grp) / n,
            "sleep_sec":  round(sum(r["sleep_seconds"] for r in grp) / n),
            "onset_sec":  round(sum(r["_raw"]["onset_sec"] for r in grp) / n),
            "abn_count":  round(sum(r["_raw"]["abn_count"] for r in grp) / n),
            "routine_sc": round(sum(r["_dim_scores"]["routine_regularity"] for r in grp) / n),
        }
        city_sc = {
            key: round(sum(r["_dim_scores"][key] for r in grp) / n)
            for key in WEIGHTS
        }

        for r in grp:
            ds  = r["_dim_scores"]
            raw = r["_raw"]
            doc = {k: v for k, v in r.items() if k not in ("_dim_scores", "_raw")}
            doc["dimensions"] = {
                "deep_sleep": {
                    "score":      ds["deep_sleep"],
                    "weight":     WEIGHTS["deep_sleep"],
                    "value":      r["deep_sleep_ratio"],
                    "city_avg":   round(city_avg["deep_ratio"]),
                    "city_score": city_sc["deep_sleep"],
                },
                "sleep_duration": {
                    "score":      ds["sleep_duration"],
                    "weight":     WEIGHTS["sleep_duration"],
                    "value":      r["sleep_seconds"],
                    "city_avg":   city_avg["sleep_sec"],
                    "city_score": city_sc["sleep_duration"],
                },
                "sleep_efficiency": {
                    "score":      ds["sleep_efficiency"],
                    "weight":     WEIGHTS["sleep_efficiency"],
                    "value":      round(raw["onset_sec"] / 60),
                    "city_avg":   round(city_avg["onset_sec"] / 60),
                    "city_score": city_sc["sleep_efficiency"],
                },
                "abnormal_events": {
                    "score":      ds["abnormal_events"],
                    "weight":     WEIGHTS["abnormal_events"],
                    "value":      raw["abn_count"],
                    "city_avg":   city_avg["abn_count"],
                    "city_score": city_sc["abnormal_events"],
                },
                "routine_regularity": {
                    "score":      ds["routine_regularity"],
                    "weight":     WEIGHTS["routine_regularity"],
                    "value":      routine_text(ds["routine_regularity"]),
                    "city_avg":   routine_text(city_avg["routine_sc"]),
                    "city_score": city_sc["routine_regularity"],
                },
            }
            result.append(doc)

    return result


# ─── 命令行参数 ───────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="生成多用户多日期睡眠地图数据")
    p.add_argument("--users-per-district", type=int, default=13,
                   help="每个区的用户数量（默认 13）。16 个区各自独立分配该数量的用户，总用户数 = 该值 × 16")
    p.add_argument("--start", default="2026-01-01",
                   help="起始日期 YYYY-MM-DD（默认 2026-01-01）")
    p.add_argument("--end", default="2026-03-31",
                   help="结束日期 YYYY-MM-DD（默认 2026-03-31）")
    p.add_argument("--seed", type=int, default=42,
                   help="随机种子（默认 42，0 表示不固定）")
    p.add_argument("--output", default="output/beijing_sleep_map_multi_user.json",
                   help="输出文件路径（相对于项目根目录）")
    return p.parse_args()


# ─── 主流程 ───────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    if args.seed:
        random.seed(args.seed)

    def parse_date(s):
        """兼容 YYYY-M-D / YYYY-MM-D / YYYY-M-DD / YYYY-MM-DD 格式。"""
        parts = s.split("-")
        if len(parts) != 3:
            raise ValueError("日期格式应为 YYYY-MM-DD，收到：{}".format(s))
        return date(int(parts[0]), int(parts[1]), int(parts[2]))

    start_date = parse_date(args.start)
    end_date   = parse_date(args.end)
    if end_date < start_date:
        print("错误：--end 日期早于 --start 日期")
        sys.exit(1)

    date_list  = []
    d = start_date
    while d <= end_date:
        date_list.append(d.isoformat())
        d += timedelta(days=1)

    n_days = len(date_list)
    n_upd  = args.users_per_district
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # 用于生成用户注册时间戳的基准（比 start_date 早 90 天）
    reg_ts_base = int(datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc).timestamp()) - 86400 * 90

    print("=" * 64)
    print("北京市睡眠地图多用户数据生成")
    print("日期范围: {} ~ {}  共 {} 天".format(args.start, args.end, n_days))
    print("每区用户数: {}  共 {} 区  总用户数: {}".format(
        n_upd, len(DISTRICTS), n_upd * len(DISTRICTS)))
    print("预估总记录数: {:,}".format(n_upd * len(DISTRICTS) * n_days))
    print("=" * 64)

    # 1. 创建所有用户档案（固定 tier，跨日期保持）
    all_profiles = []
    for district in DISTRICTS:
        profiles = create_user_profiles(district, n_upd, reg_ts_base)
        all_profiles.extend(profiles)
        tier_counts = {}
        for p in profiles:
            tier_counts[p["tier"]] = tier_counts.get(p["tier"], 0) + 1
        print("  [{}] {} 用户  分层: {}".format(
            district["district_code"], district["name"],
            " / ".join("{} {}".format(t, tier_counts.get(t, 0)) for t in TIER_NAMES)
        ))

    # 2. 逐日生成每个用户的记录
    print("\n生成每日记录中...")
    raw_records = []
    for i, stats_date_str in enumerate(date_list):
        if (i + 1) % 10 == 0 or i == n_days - 1:
            print("  进度 {}/{}（{}）".format(i + 1, n_days, stats_date_str), end="\r")
        for profile in all_profiles:
            raw_records.append(make_daily_record(profile, stats_date_str, now_iso))

    print("\n附加城市均值字段...")
    final_records = attach_city_fields(raw_records)

    # 3. 按 stats_date ASC、区 ASC、综合分 DESC 排序
    final_records.sort(key=lambda r: (
        r["stats_date"],
        r["region"]["district_code"],
        -r["score"],
    ))

    # 4. 输出
    out_path = os.path.join(PROJECT_ROOT, args.output)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(final_records, f, ensure_ascii=False, indent=2)

    # 5. 统计摘要
    all_scores = [r["score"] for r in final_records]
    buckets = [(82, 98), (63, 81), (40, 62)]
    labels  = ["高分(82-98)", "中分(63-81)", "低分(40-62)"]
    print("\n" + "=" * 64)
    print("总记录数: {:,}  得分范围: {} ~ {}".format(
        len(final_records), min(all_scores), max(all_scores)))
    print("得分分布：")
    for (lo, hi), label in zip(buckets, labels):
        cnt = sum(1 for s in all_scores if lo <= s <= hi)
        print("  {:12s}  {:>6,} 条  ({:.1f}%)".format(
            label, cnt, cnt / len(all_scores) * 100))
    print("输出 → {}".format(out_path))


if __name__ == "__main__":
    main()
