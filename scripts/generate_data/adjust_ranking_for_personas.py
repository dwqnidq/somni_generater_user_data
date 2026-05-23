"""调整 somni_sleep_analysis.json 排名，确保真实用户 + 8 个人格用户每天都在前 10 名。

策略：
  1. 将人格用户和真实用户的 _somni_sleep_analysis.json 合并进主文件
  2. 对每一天，以所有受保护用户中最低的 score 为锚点，计算需要降低多少虚拟用户的分数
  3. 降低虚拟用户分数时，从底层数据正向计算确保公式一致
  4. 真实用户和人格用户的原始数据不做任何修改

用法：
    python scripts/generate_data/adjust_ranking_for_personas.py
    python scripts/generate_data/adjust_ranking_for_personas.py --real-user-uid <uid>
    python scripts/generate_data/adjust_ranking_for_personas.py --real-user-uid <uid> --dry-run
"""

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
MAIN_FILE = os.path.join(OUTPUT_DIR, "somni_sleep_analysis.json")

# 8 个人格用户 UID
PERSONA_UIDS = {
    "69aea593af5e6cbf08027964",  # 完美主义百灵鸟
    "69aea63eaf5e6cbf08027965",  # 敏感的晨间鹿
    "69aea6d8af5e6cbf08027966",  # 效率至上考拉
    "69aea6e3af5e6cbf08027967",  # 阳光漫步者
    "69aea6e8af5e6cbf08027968",  # 深夜灵感守望者
    "69aea6eeaf5e6cbf08027969",  # 深海独奏家
    "69aea6f3af5e6cbf0802796a",  # 创意夜猫子
    "69aea6f8af5e6cbf0802796b",  # 月光冲浪者
}

# 五维权重
WEIGHTS = {
    "deep_sleep": 0.25,
    "sleep_duration": 0.25,
    "abnormal_events": 0.20,
    "sleep_efficiency": 0.15,
    "routine_regularity": 0.15,
}

# 允许排在受保护用户之前的虚拟用户数量
# 8 人格 + 1 真实用户 = 9 人，ALLOWED_ABOVE=1 → 保证前 10
ALLOWED_ABOVE = 1


# ─── 评分公式 ─────────────────────────────────────────────────────────────


def comprehensive_score(dim: dict) -> int:
    """从 dimensions 字典计算综合分。"""
    return round(
        dim["deep_sleep"]["score"] * WEIGHTS["deep_sleep"]
        + dim["sleep_duration"]["score"] * WEIGHTS["sleep_duration"]
        + dim["abnormal_events"]["score"] * WEIGHTS["abnormal_events"]
        + dim["sleep_efficiency"]["score"] * WEIGHTS["sleep_efficiency"]
        + dim["routine_regularity"]["score"] * WEIGHTS["routine_regularity"]
    )


def score_sleep_duration(sleep_sec: int) -> int:
    """睡眠时长得分。7-9h 满分；<4h 得 0；4-7h 线性。"""
    s4, s7, s9 = 4 * 3600, 7 * 3600, 9 * 3600
    if sleep_sec < s4:
        return 0
    if sleep_sec <= s7:
        return max(0, round(100 - (s7 - sleep_sec) / 1800.0 * 10))
    if sleep_sec <= s9:
        return 100
    return max(0, round(100 - (sleep_sec - s9) / 1800.0 * 5))


def score_deep_sleep(deep_sec: int, ratio: float) -> int:
    """深睡充足度得分。"""
    if deep_sec < 1800:
        return 0
    if ratio >= 0.20:
        return 100
    if ratio >= 0.15:
        return 80
    if ratio >= 0.10:
        return 60
    return 40


def score_abnormal_events(sleep_sec: int, abn_duration: int, abn_count: int) -> int:
    """异常事件得分。"""
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


def score_sleep_efficiency(onset_sec: int) -> int:
    """入睡效率得分。"""
    if onset_sec <= 900:
        return 100
    if onset_sec <= 1800:
        return 80
    if onset_sec <= 2700:
        return 60
    if onset_sec <= 3600:
        return 40
    return 0


def score_routine_regularity(fluctuation_min: float) -> int:
    """作息规律度得分。"""
    if fluctuation_min <= 30:
        return 100
    if fluctuation_min <= 120:
        return math.floor(100 * (120 - fluctuation_min) / 90)
    return 0


def verify_record_consistency(record: dict) -> Optional[str]:
    """验证单条记录的 score 与 dimensions 公式计算结果一致。

    返回 None 表示一致，否则返回差异描述。
    """
    dim = record["dimensions"]
    calculated = comprehensive_score(dim)
    stored = record["score"]
    if calculated != stored:
        return f"score={stored} vs calculated={calculated} (diff={stored - calculated})"
    return None


# ─── 数据加载 ─────────────────────────────────────────────────────────────


def load_persona_data() -> Dict[str, List[dict]]:
    """加载 8 个人格用户的 somni_sleep_analysis 数据。"""
    persona_data = {}
    for uid in PERSONA_UIDS:
        path = os.path.join(OUTPUT_DIR, f"{uid}_somni_sleep_analysis.json")
        if not os.path.exists(path):
            print(f"[WARN] 人格用户 {uid} 的文件不存在: {path}")
            continue
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)
        persona_data[uid] = records
        print(f"  人格 {uid[:12]}: {len(records)} 条记录")
    return persona_data


def load_real_user_data(uid: str) -> List[dict]:
    """加载真实用户的 somni_sleep_analysis 数据。"""
    path = os.path.join(OUTPUT_DIR, f"{uid}_somni_sleep_analysis.json")
    if not os.path.exists(path):
        print(f"[ERROR] 真实用户 {uid} 的文件不存在: {path}")
        return []
    with open(path, "r", encoding="utf-8") as f:
        records = json.load(f)
    print(f"  真实用户 {uid[:12]}: {len(records)} 条记录")
    return records


def merge_users_into_main(
    main_data: List[dict],
    persona_data: Dict[str, List[dict]],
    real_user_uid: Optional[str],
    real_user_data: List[dict],
) -> List[dict]:
    """将人格用户和真实用户数据合并进主文件（移除旧数据后重新合并）。"""
    protected_uids = set(PERSONA_UIDS)
    if real_user_uid:
        protected_uids.add(real_user_uid)

    original_count = len(main_data)
    main_data = [r for r in main_data if r["uid"] not in protected_uids]
    removed = original_count - len(main_data)
    if removed > 0:
        print(f"  移除旧数据 {removed} 条")

    added = 0
    for uid, records in persona_data.items():
        main_data.extend(records)
        added += len(records)
    if real_user_data:
        main_data.extend(real_user_data)
        added += len(real_user_data)
    print(f"  合并用户数据 {added} 条")
    return main_data


# ─── 降分逻辑 ─────────────────────────────────────────────────────────────


def lower_virtual_user(record: dict, target_score: int) -> None:
    """降低一个虚拟用户的分数到目标值，从底层数据正向计算确保一致性。"""
    if record["score"] <= target_score:
        return

    dim = record["dimensions"]
    current = comprehensive_score(dim)
    if current <= target_score:
        record["score"] = current
        return

    # 1. 降低 sleep_seconds（影响 sleep_duration 维度）
    while record["sleep_seconds"] > 14400:
        record["sleep_seconds"] -= 1800
        dim["sleep_duration"]["score"] = score_sleep_duration(record["sleep_seconds"])
        dim["sleep_duration"]["value"] = record["sleep_seconds"]
        ratio = record.get("deep_sleep_ratio", 0.15)
        record["deep_sleep_seconds"] = round(record["sleep_seconds"] * ratio)
        if comprehensive_score(dim) <= target_score:
            break

    # 2. 降低 deep_sleep（降 ratio）
    for target_ratio in [0.19, 0.14, 0.09, 0.04]:
        if comprehensive_score(dim) <= target_score:
            break
        record["deep_sleep_ratio"] = target_ratio
        record["deep_sleep_seconds"] = round(record["sleep_seconds"] * target_ratio)
        dim["deep_sleep"]["score"] = score_deep_sleep(record["deep_sleep_seconds"], target_ratio)
        dim["deep_sleep"]["value"] = round(target_ratio * 100)

    # 3. 增加异常事件
    for count in [3, 5, 7, 10]:
        if comprehensive_score(dim) <= target_score:
            break
        abn_dur = round(record["sleep_seconds"] * (0.1 + count * 0.03))
        abn_dur = min(abn_dur, round(record["sleep_seconds"] * 0.7))
        dim["abnormal_events"]["score"] = score_abnormal_events(record["sleep_seconds"], abn_dur, count)
        dim["abnormal_events"]["value"] = count

    # 4. 增加入睡耗时
    for onset_sec in [1800, 2700, 3600, 5400]:
        if comprehensive_score(dim) <= target_score:
            break
        dim["sleep_efficiency"]["score"] = score_sleep_efficiency(onset_sec)
        dim["sleep_efficiency"]["value"] = onset_sec // 60

    # 5. 增加作息波动
    for fluct in [60, 90, 120, 150]:
        if comprehensive_score(dim) <= target_score:
            break
        dim["routine_regularity"]["score"] = score_routine_regularity(fluct)
        if fluct <= 30:
            dim["routine_regularity"]["value"] = "Excellent"
        elif fluct <= 60:
            dim["routine_regularity"]["value"] = "Great"
        elif fluct <= 90:
            dim["routine_regularity"]["value"] = "Good"
        else:
            dim["routine_regularity"]["value"] = "Fair"

    # 最终：从底层数据正向计算所有维度 score 和综合分
    dim["sleep_duration"]["score"] = score_sleep_duration(record["sleep_seconds"])
    dim["sleep_duration"]["value"] = record["sleep_seconds"]
    dim["deep_sleep"]["score"] = score_deep_sleep(
        record["deep_sleep_seconds"], record.get("deep_sleep_ratio", 0)
    )
    dim["deep_sleep"]["value"] = round(record.get("deep_sleep_ratio", 0) * 100)
    record["score"] = comprehensive_score(dim)


# ─── 排名调整 ─────────────────────────────────────────────────────────────


def adjust_ranking(
    data: List[dict],
    real_user_uid: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    """调整排名：确保真实用户和人格用户每天都在前 10。

    策略：
      - 以所有受保护用户（真实用户 + 人格用户）中最低的 score 为锚点
      - 保留前 ALLOWED_ABOVE 名虚拟用户不变
      - 降低其余虚拟用户的分数到锚点 score 以下
    """
    import random
    random.seed(42)

    by_date: Dict[str, List[dict]] = defaultdict(list)
    for r in data:
        by_date[r["stats_date"]].append(r)

    total_lowered = 0

    for date_str in sorted(by_date.keys()):
        records = by_date[date_str]

        # 收集所有受保护用户（真实用户 + 人格用户）
        protected_scores = []
        if real_user_uid:
            real_records = [r for r in records if r["uid"] == real_user_uid]
            if real_records:
                protected_scores.append(real_records[0]["score"])
        persona_records = [r for r in records if r["uid"] in PERSONA_UIDS]
        protected_scores.extend(r["score"] for r in persona_records)

        if not protected_scores:
            continue

        # 以最低的受保护 score 为锚点
        anchor_score = min(protected_scores)

        virtual_records = [r for r in records if r["uid"] not in PERSONA_UIDS and (not real_user_uid or r["uid"] != real_user_uid)]
        virtual_sorted = sorted(virtual_records, key=lambda r: r["score"], reverse=True)

        keep = virtual_sorted[:ALLOWED_ABOVE]
        to_lower = virtual_sorted[ALLOWED_ABOVE:]
        to_lower = [v for v in to_lower if v["score"] >= anchor_score]

        if not to_lower:
            continue

        base_target = anchor_score - 1
        for v in to_lower:
            old_score = v["score"]
            jitter = random.randint(-5, 0)
            target = max(20, base_target + jitter)
            if not dry_run:
                lower_virtual_user(v, target)
            total_lowered += 1
            if dry_run:
                print(f"  [DRY] {date_str} {v['uid'][:12]}: {old_score} -> {target}")

    print(f"\n{'[DRY RUN] ' if dry_run else ''}共降低 {total_lowered} 条虚拟用户记录")


def verify_all(data: List[dict], real_user_uid: Optional[str] = None) -> bool:
    """验证：1) 公式一致性  2) 真实用户和人格用户排名前 10。"""
    by_date: Dict[str, List[dict]] = defaultdict(list)
    for r in data:
        by_date[r["stats_date"]].append(r)

    all_ok = True

    # 公式一致性（跳过人格用户，其 score 由连续插值公式生成，与离散阈值公式不同）
    inconsistency_count = 0
    for r in data:
        if r["uid"] in PERSONA_UIDS:
            continue
        err = verify_record_consistency(r)
        if err:
            inconsistency_count += 1
            if inconsistency_count <= 5:
                print(f"  [INCONSISTENT] {r['stats_date']} {r['uid'][:12]}: {err}")
    if inconsistency_count > 0:
        print(f"  [FAIL] {inconsistency_count} 条记录的 score 与公式计算不一致")
        all_ok = False
    else:
        print("  [OK] 所有记录的 score 与公式计算一致")

    # 受保护用户排名（真实用户 + 人格用户）
    protected_uids = set(PERSONA_UIDS)
    if real_user_uid:
        protected_uids.add(real_user_uid)

    rank_fail_count = 0
    for date_str in sorted(by_date.keys()):
        records = by_date[date_str]
        sorted_records = sorted(records, key=lambda r: r["score"], reverse=True)
        top10_uids = [r["uid"] for r in sorted_records[:10]]
        for uid in protected_uids:
            if uid not in top10_uids:
                rank = next(
                    (i + 1 for i, r in enumerate(sorted_records) if r["uid"] == uid),
                    None,
                )
                if rank:
                    print(f"  [FAIL] {date_str}: 用户 {uid[:12]} 排名 {rank}，不在前 10")
                    rank_fail_count += 1
                    all_ok = False
    if rank_fail_count == 0:
        print(f"  [OK] 所有受保护用户（真实用户 + 人格用户）所有日期都在前 10 名")

    if all_ok:
        print("  [OK] 公式一致性验证通过，受保护用户排名前 10")

    return all_ok


# ─── 入口 ─────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="调整排名让真实用户和人格用户进入前 10")
    parser.add_argument("--real-user-uid", default=None, help="真实用户 UID（从 {uid}_somni_sleep_analysis.json 读取分数）")
    parser.add_argument("--dry-run", action="store_true", help="只打印不实际修改")
    args = parser.parse_args()

    real_user_uid = args.real_user_uid

    print("=== 加载人格用户数据 ===")
    persona_data = load_persona_data()

    real_user_data: List[dict] = []
    if real_user_uid:
        print(f"\n=== 加载真实用户数据 ({real_user_uid[:12]}) ===")
        real_user_data = load_real_user_data(real_user_uid)
        if not real_user_data:
            print("[ERROR] 真实用户数据为空，退出")
            sys.exit(1)

    print("\n=== 加载主文件 ===")
    with open(MAIN_FILE, "r", encoding="utf-8") as f:
        main_data = json.load(f)
    print(f"  主文件: {len(main_data)} 条记录")

    print("\n=== 合并用户数据 ===")
    data = merge_users_into_main(main_data, persona_data, real_user_uid, real_user_data)

    print("\n=== 调整排名 ===")
    adjust_ranking(data, real_user_uid=real_user_uid, dry_run=args.dry_run)

    if not args.dry_run:
        print(f"\n=== 写入文件 ===")
        with open(MAIN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {MAIN_FILE} ({len(data)} 条)")

        print("\n=== 验证 ===")
        verify_all(data, real_user_uid=real_user_uid)


if __name__ == "__main__":
    main()
