"""调整 output/somni_sleep_analysis.json（仅虚拟用户）：八人格每日前 10，只改虚拟分数。

策略：
  1. 大池文件只存虚拟用户；八人格仅从 output/{uid}_somni_sleep_analysis.json 只读参与排名
  2. 每日以八人格（+ 可选真实用户）最低分为锚点，压低虚拟用户 score
  3. 虚拟用户降分后从底层字段重算五维，保证 score 与公式一致
  4. 禁止修改人格 JSON；写回大池时不写入人格 uid 记录

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

from sleep_map_persona_merge import (  # noqa: E402
    filter_virtual_pool_records,
    persona_uid_set,
    resolve_current_persona_uid,
)

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

def _allowed_virtual_above_anchor(real_user_uid: Optional[str]) -> int:
    """允许分数高于「受保护用户最低分」的虚拟用户数。

    仅八人格时须为 0，否则最多 1 个虚拟会挤掉人格前十。
    八人格 + 真实用户时可为 1（共 9 受保护，留 1 席虚拟高于锚点）。
    """
    return 1 if real_user_uid else 0


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


def build_working_dataset_for_ranking(
    virtual_records: List[dict],
    persona_data: Dict[str, List[dict]],
    real_user_uid: Optional[str],
    real_user_data: List[dict],
) -> List[dict]:
    """内存合并：虚拟池 + 人格/真实用户文件（仅用于算榜与校验，不写回人格）。"""
    working = list(virtual_records)
    added = 0
    for records in persona_data.values():
        working.extend(records)
        added += len(records)
    if real_user_data:
        working.extend(real_user_data)
        added += len(real_user_data)
    print(f"  内存合并人格/真实用户 {added} 条（大池仅保留虚拟 {len(virtual_records)} 条）")
    return working


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
    protected_persona_uids: Optional[set[str]] = None,
    dry_run: bool = False,
) -> None:
    """调整排名：确保受保护人格每天在池内前 10，仅改写虚拟用户记录。"""
    if protected_persona_uids is None:
        protected_persona_uids = set(PERSONA_UIDS)
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
        persona_records = [r for r in records if r["uid"] in protected_persona_uids]
        protected_scores.extend(r["score"] for r in persona_records)

        if not protected_scores:
            continue

        # 以最低的受保护 score 为锚点
        anchor_score = min(protected_scores)

        virtual_records = [
            r for r in records
            if r["uid"] not in protected_persona_uids
            and (not real_user_uid or r["uid"] != real_user_uid)
        ]
        virtual_sorted = sorted(virtual_records, key=lambda r: r["score"], reverse=True)
        keep_n = _allowed_virtual_above_anchor(real_user_uid)
        keep = virtual_sorted[:keep_n]
        keep_uids = {v["uid"] for v in keep}
        to_lower = [
            v for v in virtual_records
            if v["uid"] not in keep_uids and v["score"] >= anchor_score
        ]

        if not to_lower:
            continue

        base_target = anchor_score - 1
        for v in to_lower:
            old_score = v["score"]
            jitter = random.randint(-5, 0)
            target = max(20, base_target + jitter)
            if v["uid"] in protected_persona_uids or (
                real_user_uid and v["uid"] == real_user_uid
            ):
                raise RuntimeError(f"禁止修改受保护用户记录: {v['uid']}")
            if not dry_run:
                lower_virtual_user(v, target)
            total_lowered += 1
            if dry_run:
                print(f"  [DRY] {date_str} {v['uid'][:12]}: {old_score} -> {target}")

    print(f"\n{'[DRY RUN] ' if dry_run else ''}共降低 {total_lowered} 条虚拟用户记录")


def verify_virtual_formula(virtual_records: List[dict]) -> bool:
    """仅校验虚拟用户：score 与五维加权一致。"""
    inconsistency_count = 0
    for r in virtual_records:
        err = verify_record_consistency(r)
        if err:
            inconsistency_count += 1
            if inconsistency_count <= 5:
                print(f"  [INCONSISTENT] {r['stats_date']} {r['uid'][:12]}: {err}")
    if inconsistency_count > 0:
        print(f"  [FAIL] 虚拟用户 {inconsistency_count} 条 score 与公式不一致")
        return False
    print("  [OK] 虚拟用户 score 与公式计算一致")
    return True


def verify_protected_top10(
    working_data: List[dict],
    real_user_uid: Optional[str] = None,
    protected_persona_uids: Optional[set[str]] = None,
) -> bool:
    """在虚拟+人格合并数据上校验受保护 uid 每日前 10。"""
    if protected_persona_uids is None:
        protected_persona_uids = set(PERSONA_UIDS)
    protected_uids = set(protected_persona_uids)
    if real_user_uid:
        protected_uids.add(real_user_uid)

    by_date: Dict[str, List[dict]] = defaultdict(list)
    for r in working_data:
        by_date[r["stats_date"]].append(r)

    rank_fail_count = 0
    for date_str in sorted(by_date.keys()):
        sorted_records = sorted(by_date[date_str], key=lambda r: r["score"], reverse=True)
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
    if rank_fail_count > 0:
        return False
    print(f"  [OK] 受保护人格/用户所有日期都在前 10 名（共 {len(protected_uids)} 个 uid）")
    return True


# ─── 入口 ─────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="调整排行池：八人格每日前10，仅压低虚拟用户（不修改人格 JSON）",
    )
    parser.add_argument("--real-user-uid", default=None, help="额外保护的真实用户 UID")
    parser.add_argument(
        "--current-only",
        action="store_true",
        help="仅保护 config.json 当前人格（默认保护八人格全部）",
    )
    parser.add_argument("--current-persona-uid", default="", help="与 --current-only 联用指定 uid")
    parser.add_argument("--dry-run", action="store_true", help="只打印不实际修改")
    args = parser.parse_args()

    real_user_uid = args.real_user_uid
    if args.current_only:
        uid, label = resolve_current_persona_uid(uid=args.current_persona_uid)
        protected_persona_uids = {uid}
        print(f"=== 受保护人格：仅当前 {label} ({uid[:12]}) ===")
    else:
        protected_persona_uids = set(PERSONA_UIDS)
        print("=== 受保护人格：八人格全部（不修改人格 output 文件）===")

    print("=== 加载人格用户数据 ===")
    persona_data = load_persona_data()

    real_user_data: List[dict] = []
    if real_user_uid:
        print(f"\n=== 加载真实用户数据 ({real_user_uid[:12]}) ===")
        real_user_data = load_real_user_data(real_user_uid)
        if not real_user_data:
            print("[ERROR] 真实用户数据为空，退出")
            sys.exit(1)

    print("\n=== 加载主文件（仅虚拟） ===")
    with open(MAIN_FILE, "r", encoding="utf-8") as f:
        main_data = json.load(f)
    persona_uids_all = persona_uid_set()
    virtual_only = filter_virtual_pool_records(main_data, persona_uids_all)
    stripped = len(main_data) - len(virtual_only)
    if stripped > 0:
        print(f"  剔除大池中人格旧记录 {stripped} 条 → 虚拟 {len(virtual_only)} 条")
    else:
        print(f"  主文件: {len(virtual_only)} 条虚拟记录")

    print("\n=== 内存合并（算榜用） ===")
    working = build_working_dataset_for_ranking(
        virtual_only, persona_data, real_user_uid, real_user_data
    )

    print("\n=== 调整排名 ===")
    adjust_ranking(
        working,
        real_user_uid=real_user_uid,
        protected_persona_uids=protected_persona_uids,
        dry_run=args.dry_run,
    )

    if not args.dry_run:
        print(f"\n=== 写入文件（仅虚拟） ===")
        with open(MAIN_FILE, "w", encoding="utf-8") as f:
            json.dump(virtual_only, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {MAIN_FILE} ({len(virtual_only)} 条，不含人格)")

        print("\n=== 验证 ===")
        formula_ok = verify_virtual_formula(virtual_only)
        rank_ok = verify_protected_top10(
            working,
            real_user_uid=real_user_uid,
            protected_persona_uids=protected_persona_uids,
        )
        if not (formula_ok and rank_ok):
            sys.exit(1)


if __name__ == "__main__":
    main()
