"""生成北京市各区睡眠地图排行数据（somni_sleep_analysis）。

每个区独立生成几百条记录，综合得分范围 40-98，分布随机。
五维指标与综合分严格按产品公式计算，底层原始参数与得分保持一致。

用法：
    python scripts/generate_data/generate_sleep_map_ranking_data.py
    python scripts/generate_data/generate_sleep_map_ranking_data.py --count 500
    python scripts/generate_data/generate_sleep_map_ranking_data.py --date 2026-03-10 --seed 123
"""

import argparse
import json
import math
import os
import random
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

from utils import format_sleep_map_pool_user_name  # noqa: E402

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")

# ─── 北京市行政区划 ────────────────────────────────────────────────────────────

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


# ─── 五维评分公式 ──────────────────────────────────────────────────────────────

def score_sleep_duration(sleep_sec):
    """睡眠时长得分（权重 25%）。
    7-9h 满分；< 7h 每少 30min 扣 10 分（< 4h 得 0）；> 9h 每多 30min 扣 5 分。
    """
    s4, s7, s9 = 4 * 3600, 7 * 3600, 9 * 3600
    if sleep_sec < s4:
        return 0
    if sleep_sec <= s7:
        deficit_units = (s7 - sleep_sec) / 1800.0
        return max(0, round(100 - deficit_units * 10))
    if sleep_sec <= s9:
        return 100
    excess_units = (sleep_sec - s9) / 1800.0
    return max(0, round(100 - excess_units * 5))


def score_deep_sleep(deep_sec, ratio):
    """深睡充足度得分（权重 25%）。
    深睡时长 < 30min → 0；≥20% → 100；15-19% → 80；10-14% → 60；< 10% → 40。
    """
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
    """异常事件得分（权重 20%）。
    基础占比 = (睡眠时长 - 异常时长) / 睡眠时长；
    次数修正：≤2 不变，3-5 ×0.8，≥6 ×0.6；
    最终占比 × 100，最终占比 < 30% 得 0。
    """
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
    """入睡效率得分（权重 15%）。
    ≤15min → 100；16-30min → 80；31-45min → 60；46-60min → 40；> 60min → 0。
    """
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
    """作息规律度得分（权重 15%）。
    综合波动值（分钟）：≤30 → 100；30-120 线性；> 120 → 0。
    score = floor(100 × (120 - fluctuation) / 90)
    """
    if fluctuation_min <= 30:
        return 100
    if fluctuation_min <= 120:
        return math.floor(100 * (120 - fluctuation_min) / 90)
    return 0


def comprehensive_score(dim_scores):
    """综合得分 = 加权求和，取整。"""
    return round(
        dim_scores["sleep_duration"]     * WEIGHTS["sleep_duration"]
        + dim_scores["deep_sleep"]       * WEIGHTS["deep_sleep"]
        + dim_scores["abnormal_events"]  * WEIGHTS["abnormal_events"]
        + dim_scores["sleep_efficiency"] * WEIGHTS["sleep_efficiency"]
        + dim_scores["routine_regularity"] * WEIGHTS["routine_regularity"]
    )


# ─── 辅助：作息规律度文案 ─────────────────────────────────────────────────────

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


# ─── 原始参数随机生成器 ───────────────────────────────────────────────────────

def gen_sleep_sec():
    """随机生成睡眠总时长（秒），分布偏向 7-9h 正常区间。"""
    r = random.random()
    if r < 0.08:
        return random.randint(14400, 21599)   # 4-6h（睡眠不足，差）
    if r < 0.25:
        return random.randint(21600, 25199)   # 6-7h（略不足）
    if r < 0.72:
        return random.randint(25200, 32400)   # 7-9h（最优）
    return random.randint(32401, 39600)       # 9-11h（睡眠过长）


def gen_deep_sleep(sleep_sec):
    """随机生成深睡时长与占比。"""
    r = random.random()
    if r < 0.08:
        ratio = random.uniform(0.04, 0.099)   # 深睡不足
    elif r < 0.28:
        ratio = random.uniform(0.10, 0.149)   # 偏低
    elif r < 0.68:
        ratio = random.uniform(0.15, 0.199)   # 中等
    else:
        ratio = random.uniform(0.20, 0.28)    # 充足
    deep_sec = round(sleep_sec * ratio)
    actual_ratio = deep_sec / sleep_sec if sleep_sec > 0 else 0.0
    return deep_sec, actual_ratio


def gen_onset_sec():
    """随机生成入睡耗时（秒）。"""
    r = random.random()
    if r < 0.35:
        return random.randint(300, 900)     # ≤15min（快速入睡）
    if r < 0.60:
        return random.randint(901, 1800)    # 16-30min
    if r < 0.78:
        return random.randint(1801, 2700)   # 31-45min
    if r < 0.90:
        return random.randint(2701, 3600)   # 46-60min
    return random.randint(3601, 5400)       # >60min（入睡困难）


def gen_abnormal(sleep_sec):
    """随机生成异常事件次数与累计时长（秒）。"""
    count = random.choices(
        [0, 1, 2, 3, 4, 5, 6, 7, 8],
        weights=[25, 20, 15, 13, 10, 7, 5, 3, 2],
    )[0]
    if count == 0:
        return 0, 0
    # 异常时长不超过睡眠时长的 60%
    max_frac = min(0.60, count * 0.09)
    frac = random.uniform(0.005 * count, max_frac)
    duration = round(sleep_sec * frac)
    return count, duration


def gen_fluctuation():
    """随机生成综合波动值（分钟）。"""
    r = random.random()
    if r < 0.30:
        return random.uniform(5, 30)     # 规律性好
    if r < 0.62:
        return random.uniform(30, 80)    # 一般
    if r < 0.85:
        return random.uniform(80, 120)   # 较差
    return random.uniform(120, 175)      # 差


def hex_id(n=24):
    return "".join(random.choices("0123456789abcdef", k=n))


# ─── 单条记录生成（含拒绝采样） ───────────────────────────────────────────────

def make_one_record(district, uid, user_idx):
    """
    生成一条满足综合得分 [40, 98] 的记录。
    若参数组合超出范围则返回 None，由调用方重试。
    """
    sleep_sec = gen_sleep_sec()
    deep_sec, deep_ratio = gen_deep_sleep(sleep_sec)
    onset_sec = gen_onset_sec()
    abn_count, abn_duration = gen_abnormal(sleep_sec)
    fluctuation = gen_fluctuation()

    dim_scores = {
        "sleep_duration":     score_sleep_duration(sleep_sec),
        "deep_sleep":         score_deep_sleep(deep_sec, deep_ratio),
        "abnormal_events":    score_abnormal_events(sleep_sec, abn_duration, abn_count),
        "sleep_efficiency":   score_sleep_efficiency(onset_sec),
        "routine_regularity": score_routine_regularity(fluctuation),
    }
    total = comprehensive_score(dim_scores)
    if not (40 <= total <= 98):
        return None

    return {
        "_id":                hex_id(),
        "uid":                uid,
        "stats_date":         None,   # 由外层填充
        "region": {
            "province_code": PROVINCE_CODE,
            "city_code":     CITY_CODE,
            "district_code": district["district_code"],
        },
        "user_name":          format_sleep_map_pool_user_name(user_idx),
        "score":              total,
        "sleep_seconds":      sleep_sec,
        "deep_sleep_seconds": deep_sec,
        "deep_sleep_ratio":   round(deep_ratio, 8),
        "evaluation":         "睡眠综合评价 {} · {}".format(district["name"], "{}"),
        "is_env_sensitive":   random.random() < 0.30,
        # 内部字段，最终不输出
        "_dim_scores":        dim_scores,
        "_raw": {
            "onset_sec":    onset_sec,
            "abn_count":    abn_count,
            "abn_duration": abn_duration,
            "fluctuation":  round(fluctuation, 2),
        },
    }


# ─── 按区批量生成 ─────────────────────────────────────────────────────────────

def generate_district_records(district, count, _start_idx=1):
    records = []
    uid_pool = set()
    retry_count = 0

    while len(records) < count:
        uid = hex_id()
        while uid in uid_pool:
            uid = hex_id()
        uid_pool.add(uid)

        rec = make_one_record(district, uid, len(records) + 1)
        if rec is None:
            retry_count += 1
            uid_pool.discard(uid)
            continue

        records.append(rec)

    return records, retry_count


# ─── 附加同城均值字段 ─────────────────────────────────────────────────────────

def attach_city_fields(district_records, stats_date, now_iso):
    """
    对同一区内所有记录计算维度均值（city_avg / city_score），
    并组装最终输出字段，删除内部临时字段。
    """
    n = len(district_records)

    # 各维度同城均值
    city_avg = {
        "deep_ratio":   sum(r["deep_sleep_ratio"] for r in district_records) / n,
        "sleep_sec":    round(sum(r["sleep_seconds"] for r in district_records) / n),
        "onset_sec":    round(sum(r["_raw"]["onset_sec"] for r in district_records) / n),
        "abn_count":    round(sum(r["_raw"]["abn_count"] for r in district_records) / n),
        "routine_sc":   round(sum(r["_dim_scores"]["routine_regularity"] for r in district_records) / n),
    }
    city_score = {
        key: round(sum(r["_dim_scores"][key] for r in district_records) / n)
        for key in WEIGHTS
    }

    finalized = []
    for r in district_records:
        ds = r["_dim_scores"]
        raw = r["_raw"]
        doc = {
            "_id":                r["_id"],
            "uid":                r["uid"],
            "stats_date":         stats_date,
            "region":             r["region"],
            "user_name":          r["user_name"],
            "score":              r["score"],
            "sleep_seconds":      r["sleep_seconds"],
            "deep_sleep_seconds": r["deep_sleep_seconds"],
            "deep_sleep_ratio":   r["deep_sleep_ratio"],
            "evaluation":         r["evaluation"].format(stats_date),
            "dimensions": {
                "deep_sleep": {
                    "score":      ds["deep_sleep"],
                    "weight":     0.25,
                    "value":      r["deep_sleep_ratio"],
                    "city_avg":   round(city_avg["deep_ratio"], 6),
                    "city_score": city_score["deep_sleep"],
                },
                "sleep_duration": {
                    "score":      ds["sleep_duration"],
                    "weight":     0.25,
                    "value":      r["sleep_seconds"],
                    "city_avg":   city_avg["sleep_sec"],
                    "city_score": city_score["sleep_duration"],
                },
                "sleep_efficiency": {
                    "score":      ds["sleep_efficiency"],
                    "weight":     0.15,
                    "value":      raw["onset_sec"],
                    "city_avg":   city_avg["onset_sec"],
                    "city_score": city_score["sleep_efficiency"],
                },
                "abnormal_events": {
                    "score":      ds["abnormal_events"],
                    "weight":     0.20,
                    "value":      raw["abn_count"],
                    "city_avg":   city_avg["abn_count"],
                    "city_score": city_score["abnormal_events"],
                },
                "routine_regularity": {
                    "score":      ds["routine_regularity"],
                    "weight":     0.15,
                    "value":      routine_text(ds["routine_regularity"]),
                    "city_avg":   routine_text(city_avg["routine_sc"]),
                    "city_score": city_score["routine_regularity"],
                },
            },
            "is_env_sensitive": r["is_env_sensitive"],
            "create_time":      now_iso,
            "update_time":      now_iso,
        }
        finalized.append(doc)

    return finalized


# ─── 命令行参数 ───────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="生成北京市睡眠地图排行数据")
    parser.add_argument(
        "--count", type=int, default=300,
        help="每个区生成的记录数量（默认 300）",
    )
    parser.add_argument(
        "--date", default="2026-03-10",
        help="stats_date（默认 2026-03-10）",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="随机种子（默认 42，0 表示不固定）",
    )
    parser.add_argument(
        "--output", default="output/beijing_sleep_map_ranking.json",
        help="输出文件相对路径（默认 output/beijing_sleep_map_ranking.json）",
    )
    return parser.parse_args()


# ─── 主流程 ───────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    if args.seed:
        random.seed(args.seed)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    out_path = os.path.join(PROJECT_ROOT, args.output)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    print("=" * 60)
    print("北京市睡眠地图排行数据生成")
    print("stats_date: {}  count/district: {}  seed: {}".format(
        args.date, args.count, args.seed
    ))
    print("=" * 60)

    all_records = []

    for district in DISTRICTS:
        raw_recs, retries = generate_district_records(district, args.count)
        final_recs = attach_city_fields(raw_recs, args.date, now_iso)
        # 区内按综合分降序排列
        final_recs.sort(key=lambda r: r["score"], reverse=True)
        all_records.extend(final_recs)

        scores = [r["score"] for r in final_recs]
        print("  [{:4s}] {:5s}  {:>3d} 条  得分 {:2d}-{:2d}  均值 {:.1f}  重试 {}".format(
            district["district_code"],
            district["name"],
            len(final_recs),
            min(scores), max(scores),
            sum(scores) / len(scores),
            retries,
        ))

    # 全局同样按综合分降序（区内顺序保留，区间也可按分排）
    all_records.sort(key=lambda r: r["score"], reverse=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)

    total = len(all_records)
    all_scores = [r["score"] for r in all_records]
    print("=" * 60)
    print("共生成 {} 条  总得分范围 {}-{}  输出 → {}".format(
        total, min(all_scores), max(all_scores), out_path
    ))


if __name__ == "__main__":
    main()
