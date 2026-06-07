"""独立生成睡眠艺术星球数据（sleep_art_data）。

不依赖 health_data，直接构造，覆盖映射表中所有档位。
输出到 output/sleep_art_data/{uid}_sleep_art.json
"""

import json
import os
import random
import string
from datetime import date, datetime, timedelta, timezone

# ── UID 列表 ──────────────────────────────────
UIDS = [
    "69aea593af5e6cbf08027964",
    "69aea63eaf5e6cbf08027965",
    "69aea6d8af5e6cbf08027966",
    "69aea6e3af5e6cbf08027967",
    "69aea6e8af5e6cbf08027968",
    "69aea6eeaf5e6cbf08027969",
    "69aea6f3af5e6cbf0802796a",
    "69aea6f8af5e6cbf0802796b",
    "c",
]

START_DATE = date(2026, 5, 15)
END_DATE = date(2026, 5, 30)

# ── 映射表定义 ─────────────────────────────────

# 1. 星球大小：total_sleep_minutes → planet_size
PLANET_SIZE_TIERS = [
    {"min_minutes": 0,   "max_minutes": 359,  "size": 0.6},
    {"min_minutes": 360, "max_minutes": 419,  "size": 0.75},
    {"min_minutes": 420, "max_minutes": 479,  "size": 0.88},
    {"min_minutes": 480, "max_minutes": 999,  "size": 1.0},
]

# 2. 星球颜色：sleep_efficiency → colors
COLOR_TIERS = [
    {"min_eff": 85,  "max_eff": 100, "land": "#8ec5f5", "water": "#284e71", "ring": "#a5c8e9"},
    {"min_eff": 75,  "max_eff": 84,  "land": "#38e2e5", "water": "#286c71", "ring": "#acebec"},
    {"min_eff": 65,  "max_eff": 74,  "land": "#d6ce9f", "water": "#6f6544", "ring": "#dedbc4"},
    {"min_eff": 0,   "max_eff": 64,  "land": "#dea282", "water": "#945838", "ring": "#dec0af"},
]

# 3. 星球光晕：deep_sleep_pct → ring_radius
RING_RADIUS_TIERS = [
    {"min_pct": 0,   "max_pct": 11,  "radius": 1.4},
    {"min_pct": 12,  "max_pct": 17,  "radius": 1.6},
    {"min_pct": 18,  "max_pct": 20,  "radius": 1.8},
    {"min_pct": 21,  "max_pct": 100, "radius": 2.0},
]

# 4. 星球纹理：stability_score → noise_density
NOISE_THRESHOLDS = [
    {"min_score": 90, "density": 0.25},
    {"min_score": 80, "density": 0.45},
    {"min_score": 70, "density": 0.65},
    {"min_score": 0,  "density": 0.85},
]

TITLES = [
    "梦境星空", "星河物语", "月夜微光", "深海幻境",
    "星尘漫步", "银河织梦", "晨曦初露", "极光之境",
    "星云漫步", "暗夜流光", "星际迷航", "月光宝盒",
]


def _pick_tier(tiers, value, min_key, max_key):
    for tier in tiers:
        if tier[min_key] <= value <= tier[max_key]:
            return tier
    return tiers[-1]


def _random_seed():
    return "".join(random.choices(string.hexdigits[:16], k=22))


def _noise_density(stab_score):
    for t in NOISE_THRESHOLDS:
        if stab_score >= t["min_score"]:
            return t["density"]
    return 0.85


def _efficiency_grade_label(eff: int) -> str:
    """与 generate_sleep_art_data.efficiency_grade_label 一致。"""
    if eff >= 85:
        return "Fast"
    if eff >= 75:
        return "Optimal"
    if eff >= 65:
        return "Normal"
    return "Slow"


def _stability_grade_label(score: int) -> str:
    """与 generate_sleep_art_data.stability_grade_label 一致。"""
    if score <= 40:
        return "Poor"
    if score <= 60:
        return "Fair"
    if score <= 80:
        return "Good"
    return "Great"


def generate_record(uid, record_date, spec):
    total_min = random.randint(spec["sleep_range"][0], spec["sleep_range"][1])
    efficiency = random.randint(spec["eff_range"][0], spec["eff_range"][1])
    deep_pct = random.randint(spec["deep_range"][0], spec["deep_range"][1])
    deep_min = int(total_min * deep_pct / 100)
    stab_score = random.randint(spec["stab_range"][0], spec["stab_range"][1])

    planet_size = _pick_tier(PLANET_SIZE_TIERS, total_min, "min_minutes", "max_minutes")["size"]
    ct = _pick_tier(COLOR_TIERS, efficiency, "min_eff", "max_eff")
    ring_radius = _pick_tier(RING_RADIUS_TIERS, deep_pct, "min_pct", "max_pct")["radius"]
    noise = _noise_density(stab_score)

    eff_label = _efficiency_grade_label(efficiency)
    stab_label = _stability_grade_label(stab_score)

    dt = datetime(record_date.year, record_date.month, record_date.day,
                  12, 0, 0, tzinfo=timezone.utc)

    return {
        "uid": uid,
        "record_date": record_date.isoformat(),
        "metrics": {
            "total_sleep_minutes": total_min,
            "deep_min": deep_min,
            "deep_pct": deep_pct,
            "efficiency": {"score": efficiency, "grade_label": eff_label},
            "stability": {"score": stab_score, "grade_label": stab_label},
        },
        "title": "梦境星空",
        "description": "您的专属睡眠具象艺术",
        "planet": {
            "planet_size": planet_size,
            "land_color": ct["land"],
            "water_color": ct["water"],
            "ring_color": ct["ring"],
            "ring_radius": ring_radius,
            "planet_noise_density": noise,
        },
        "illustrations": [
            {"url": f"https://picsum.photos/seed/{_random_seed()}/720/1280", "sort_order": 0},
            {"url": f"https://picsum.photos/seed/{_random_seed()}/720/1280", "sort_order": 1},
        ],
        "create_time": dt.isoformat(),
        "update_time": dt.isoformat(),
    }


def build_schedule(n_days):
    """为 n_days 天构建档位分配计划，确保所有档位都被覆盖。

    每个维度独立洗牌，保证 30 天内覆盖全部 4 档。
    各指标使用合理的数据采样范围，而非映射表的分类边界。
    """
    # 每个维度：先保证每档至少 min_per_tier 天，剩余随机补
    def distribute(n_tiers, min_per_tier):
        indices = []
        for i in range(n_tiers):
            indices.extend([i] * min_per_tier)
        while len(indices) < n_days:
            indices.append(random.randint(0, n_tiers - 1))
        random.shuffle(indices)
        return indices[:n_days]

    size_idx = distribute(4, 7)
    color_idx = distribute(4, 7)
    ring_idx = distribute(4, 7)
    stab_idx = distribute(2, 15)

    # 各档位的合理采样范围（区别于映射表的分类边界）
    # planet_size: 每档在边界附近取值
    SIZE_RANGES = [
        (300, 359),   # <6h → size 0.6
        (360, 419),   # 6-6.9h → size 0.75
        (420, 479),   # 7-7.9h → size 0.88
        (480, 540),   # >=8h → size 1.0
    ]
    # efficiency: 每档在合理范围内取值，最低不低于 40
    EFF_RANGES = [
        (85, 98),     # 优秀 → Fast
        (75, 84),     # 良好 → Optimal
        (65, 74),     # 一般 → Normal
        (40, 64),     # 偏低 → Slow
    ]
    # deep_pct: 每档在合理范围内取值，总范围 8%-25%
    DEEP_RANGES = [
        (8, 11),      # <12%
        (12, 17),     # 12-17.9%
        (18, 20),     # 18-20%
        (21, 25),     # >20%
    ]
    # stability: Great 81-98, Good 55-80（与正式 grade_label 分档一致）
    STAB_RANGES = [
        (81, 98),
        (55, 80),
    ]

    schedule = []
    for i in range(n_days):
        si = size_idx[i]
        ci = color_idx[i]
        ri = ring_idx[i]
        ti = stab_idx[i]

        schedule.append({
            "sleep_range": SIZE_RANGES[si],
            "eff_range": EFF_RANGES[ci],
            "deep_range": DEEP_RANGES[ri],
            "stab_range": STAB_RANGES[ti],
        })
    return schedule


def main():
    output_dir = os.path.join(os.path.dirname(__file__), "..", "..", "output", "sleep_art_data")
    os.makedirs(output_dir, exist_ok=True)

    n_days = (END_DATE - START_DATE).days + 1
    print(f"生成 {len(UIDS)} 个人格 × {n_days} 天 = {len(UIDS) * n_days} 条记录")
    print(f"输出目录: {os.path.abspath(output_dir)}\n")

    for uid in UIDS:
        random.seed(uid)
        schedule = build_schedule(n_days)
        records = []
        for day_idx in range(n_days):
            d = START_DATE + timedelta(days=day_idx)
            records.append(generate_record(uid, d, schedule[day_idx]))

        filepath = os.path.join(output_dir, f"{uid}_sleep_art.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

        # 验证档位覆盖
        sizes = sorted(set(r["planet"]["planet_size"] for r in records))
        lands = sorted(set(r["planet"]["land_color"] for r in records))
        radii = sorted(set(r["planet"]["ring_radius"] for r in records))
        noises = sorted(set(r["planet"]["planet_noise_density"] for r in records))
        eff_labels = sorted(set(r["metrics"]["efficiency"]["grade_label"] for r in records))
        stab_labels = sorted(set(r["metrics"]["stability"]["grade_label"] for r in records))

        print(f"  {uid[:8]}...  sizes={sizes} colors={len(lands)}种 "
              f"radii={radii} noise={noises} eff={eff_labels} stab={stab_labels}")

    print("\n完成")


if __name__ == "__main__":
    main()
