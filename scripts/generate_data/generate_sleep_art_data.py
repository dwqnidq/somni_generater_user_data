"""
根据各用户健康数据（_health_data.json），生成每日睡眠艺术可视化数据。
- 从 raw_data 提取睡眠指标；planet、metrics 均由同一份 raw 确定性推导。
- 输出至 output/{uid}_sleep_art_data.json

用法：
  python scripts/generate_data/generate_sleep_art_data.py
  python scripts/generate_data/generate_sleep_art_data.py --user-id 69aea6f3af5e6cbf0802796a
  python scripts/generate_data/generate_sleep_art_data.py --user-id 69aea6f3af5e6cbf0802796a --no-overwrite
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

# ── 路径配置 ──────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.join(ROOT, "output")
CONFIG_PATH = os.path.join(ROOT, "config", "health_data_personas_config.json")

# ── 用户人格映射 ──────────────────────────────────────────────────────────────
PERSONAS = [
    {"uid": "69aea593af5e6cbf08027964", "name": "完美主义百灵鸟"},
    {"uid": "69aea63eaf5e6cbf08027965", "name": "敏感晨间鹿"},
    {"uid": "69aea6d8af5e6cbf08027966", "name": "效率考拉"},
    {"uid": "69aea6e3af5e6cbf08027967", "name": "阳光漫步者"},
    {"uid": "69aea6e8af5e6cbf08027968", "name": "创意夜猫子"},
    {"uid": "69aea6eeaf5e6cbf08027969", "name": "敏感都市夜行者"},
    {"uid": "69aea6f3af5e6cbf0802796a", "name": "夜间猎手"},
    {"uid": "69aea6f8af5e6cbf0802796b", "name": "随性漫游者"},
]

# ── 固定文案 ──────────────────────────────────────────────────────────────────
TITLE = "梦境星空"
DESCRIPTION = "您的专属睡眠具象艺术"


def _i(raw: dict, key: str, default: int = 0) -> int:
    v = raw.get(key, default)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _hours_total_sleep(raw: dict) -> float:
    m = _i(raw, "total_sleep_minutes", 0)
    return m / 60.0


def planet_size_from_raw(raw: dict) -> float:
    h = _hours_total_sleep(raw)
    if h < 6:
        return 0.6
    if h < 7:
        return 0.8
    if h < 8:
        return 1.0
    return 1.2


def planet_colors_from_efficiency(eff: int) -> tuple[str, str, str]:
    if eff >= 85:
        return ("#8ec5f5", "#284e71", "#a5c8e9")
    if eff >= 75:
        return ("#38e2e5", "#286c71", "#acebec")
    if eff >= 65:
        return ("#d6ce9f", "#6f6544", "#dedbc4")
    return ("#dea282", "#945838", "#dec0af")


def ring_radius_from_deep_ratio(deep_pct: int) -> float:
    if deep_pct < 12:
        return 1.4
    if deep_pct <= 17:
        return 1.6
    if deep_pct <= 20:
        return 1.8
    return 2.0


def efficiency_grade_label(eff: int) -> str:
    if eff >= 85:
        return "Fast"
    if eff >= 75:
        return "Optimal"
    if eff >= 65:
        return "Normal"
    return "Slow"


def stability_score_from_raw(raw: dict) -> int:
    """由 raw 各字段加权合成 30–100（整数），同 raw 恒相同。"""
    eff = _i(raw, "sleep_efficiency", 0)
    awake = _i(raw, "awake_ratio", 0)
    turnover = _i(raw, "turnover_count", 0)
    apnea = _i(raw, "apnea_count", 0)
    latency = _i(raw, "sleep_latency", 0)
    deep = _i(raw, "deep_sleep_ratio", 0)
    hb = _i(raw, "average_heartbeat", 0)
    resp = _i(raw, "average_respiration", 0)

    frag = max(0.0, 100.0 - min(45.0, awake * 1.5))
    move = max(0.0, 100.0 - min(45.0, turnover * 0.45))
    apn = max(0.0, 100.0 - min(35.0, apnea * 4.5))
    lat_pen = max(0.0, 100.0 - min(25.0, latency * 0.35))

    hr_calm = 75.0
    if hb > 0:
        hr_calm = max(0.0, 100.0 - min(40.0, abs(hb - 63) * 1.8))
    rr_calm = 75.0
    if resp > 0:
        rr_calm = max(0.0, 100.0 - min(40.0, abs(resp - 15) * 4.0))

    depth = max(0.0, min(100.0, deep * 3.5))

    blended = (
        0.22 * eff
        + 0.18 * frag
        + 0.16 * move
        + 0.14 * apn
        + 0.10 * lat_pen
        + 0.08 * hr_calm
        + 0.08 * rr_calm
        + 0.04 * depth
    )
    return max(30, min(100, int(round(blended))))


def stability_grade_label(score: int) -> str:
    if score <= 40:
        return "Poor"
    if score <= 60:
        return "Fair"
    if score <= 80:
        return "Good"
    return "Great"


def planet_noise_density_from_stability(score: int) -> float:
    if score <= 40:
        return 0.85
    if score <= 60:
        return 0.65
    if score <= 80:
        return 0.45
    return 0.25


def build_planet(raw: dict, stability_score: int) -> dict:
    eff = _i(raw, "sleep_efficiency", 0)
    land, water, ring_c = planet_colors_from_efficiency(eff)
    deep_pct = _i(raw, "deep_sleep_ratio", 0)
    return {
        "planet_size": planet_size_from_raw(raw),
        "land_color": land,
        "water_color": water,
        "ring_color": ring_c,
        "ring_radius": ring_radius_from_deep_ratio(deep_pct),
        "planet_noise_density": planet_noise_density_from_stability(stability_score),
    }


def build_metrics(raw: dict) -> dict:
    total_min = _i(raw, "total_sleep_minutes", 0)
    deep_ratio_pct = _i(raw, "deep_sleep_ratio", 0)
    efficiency = _i(raw, "sleep_efficiency", 0)

    deep_min = int(total_min * deep_ratio_pct / 100)
    deep_pct = int(deep_ratio_pct)

    stab = stability_score_from_raw(raw)

    return {
        "deep_min": deep_min,
        "deep_pct": deep_pct,
        "efficiency": {
            "score": efficiency,
            "grade_label": efficiency_grade_label(efficiency),
        },
        "stability": {
            "score": stab,
            "grade_label": stability_grade_label(stab),
        },
    }


def _illustration_seed(uid: str) -> str:
    """基于 uid 生成固定的 picsum 图片种子前缀（20位十六进制）"""
    return hashlib.md5(uid.encode()).hexdigest()[:20]


def build_illustrations(uid: str) -> list[dict]:
    """生成两张插图（picsum，种子确定性绑定 uid）"""
    base_seed = _illustration_seed(uid)
    return [
        {"url": f"https://picsum.photos/seed/{base_seed}a/720/1280", "sort_order": 0},
        {"url": f"https://picsum.photos/seed/{base_seed}b/720/1280", "sort_order": 1},
    ]


def _persona_uid(persona: dict) -> str:
    return str(persona.get("user_id") or persona.get("uid") or "")


def resolve_personas_for_cli(*, user_id: str | None, config_path: str) -> list[dict]:
    """未指定 user_id 时用内置 PERSONAS；指定时从 config.personas 中解析一条。"""
    if not user_id:
        return list(PERSONAS)
    if not os.path.isfile(config_path):
        print(f"未找到配置文件: {config_path}")
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    personas = cfg.get("personas") or []
    out = [p for p in personas if str(p.get("user_id", "")) == user_id]
    if not out:
        print(f"未在 config.personas 中找到 user_id={user_id}")
        sys.exit(1)
    return out


def generate_sleep_art_for_persona(
    persona: dict,
    output_dir: str | None = None,
) -> list[dict]:
    base = output_dir if output_dir is not None else OUTPUT_DIR
    uid = _persona_uid(persona)
    if not uid:
        return []
    data_path = os.path.join(base, f"{uid}_health_data.json")

    if not os.path.exists(data_path):
        print(f"  [跳过] 未找到文件: {data_path}")
        return []

    with open(data_path, encoding="utf-8") as f:
        health_records: list[dict] = json.load(f)

    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now_utc.microsecond // 1000:03d}Z"

    illustrations = build_illustrations(uid)
    results: list[dict] = []

    for rec in health_records:
        record_date = rec.get("record_date", "")
        raw = rec.get("raw_data", {})
        if not raw:
            continue

        metrics = build_metrics(raw)
        stab = int(metrics["stability"]["score"])
        planet = build_planet(raw, stab)

        results.append({
            "uid": uid,
            "record_date": record_date,
            "metrics": metrics,
            "title": TITLE,
            "description": DESCRIPTION,
            "planet": planet,
            "illustrations": illustrations,
            "create_time": now_iso,
            "update_time": now_iso,
        })

    return results


def write_sleep_art_data_for_persona(
    persona: dict,
    output_dir: str,
    *,
    overwrite: bool = False,
) -> None:
    """写入 {uid}_sleep_art_data.json；无 health 文件或无可写记录时仅打印说明。"""
    uid = _persona_uid(persona)
    name = str(persona.get("name") or uid)
    if not uid:
        return
    out_path = os.path.join(output_dir, f"{uid}_sleep_art_data.json")
    if (not overwrite) and os.path.isfile(out_path):
        print(f"  [跳过] {name} ({uid}) sleep_art 已存在（--overwrite 可覆盖）")
        return

    records = generate_sleep_art_for_persona(persona, output_dir=output_dir)
    if not records:
        print(f"  [跳过] {name} ({uid}) 无 sleep_art 记录（需有效 health_data）")
        return

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"  [{name}] {uid} → {len(records)} 条 → {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="由 health_data 生成 sleep_art_data JSON")
    parser.add_argument(
        "--user-id",
        default=None,
        metavar="UID",
        help="仅生成该 user_id（须存在于 config/health_data_personas_config.json 的 personas）",
    )
    parser.add_argument(
        "--config",
        default=CONFIG_PATH,
        help="人格配置路径（与 --user-id 联用）",
    )
    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        help="输出目录，默认仓库 output/",
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="若 {uid}_sleep_art_data.json 已存在则跳过",
    )
    args = parser.parse_args()

    personas = resolve_personas_for_cli(user_id=args.user_id, config_path=args.config)
    overwrite = not args.no_overwrite
    out_dir = args.output_dir

    print("生成睡眠艺术可视化数据…")
    if args.user_id:
        print(f"  （仅 user_id={args.user_id}）")

    for persona in personas:
        write_sleep_art_data_for_persona(persona, out_dir, overwrite=overwrite)

    print(f"\n完成！共处理 {len(personas)} 个用户")


if __name__ == "__main__":
    main()
