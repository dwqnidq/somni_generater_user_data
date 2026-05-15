"""异常日历、TIB 阶段最终化。"""

from __future__ import annotations

import math
import random
from datetime import timedelta

from .sleep_score import (
    _normalize_three_int100,
    _sample_four_spt_percents_bounded,
    _intersect_cfg_pb,
    _sample_stage_ratio,
    normalize_four_spt_stage_percents,
    distribute_sleep_stage_minutes,
    PERSONALITY_SLEEP_STAGE_RATIO_RANGES,
    GOOD_SLEEP_PERSONALITIES,
    POOR_SLEEP_PERSONALITIES,
    HIGH_SENS_HIGH_ACTIVE_PERSONALITIES,
    LOW_SENS_LOW_ACTIVE_PERSONALITIES,
    LOW_SENS_HIGH_OR_HIGH_SENS_LOW_PERSONALITIES,
)


def _sleep_calendar_bad_day_fraction(personality_type):
    """离群「坏睡日」占日历总天数的比例（按敏感×活跃分档）。高敏感+高活跃为 0（只排好日）。"""
    pt = personality_type or "M-L-C"
    if pt in HIGH_SENS_HIGH_ACTIVE_PERSONALITIES:
        return 0.0
    if pt in LOW_SENS_LOW_ACTIVE_PERSONALITIES:
        return 0.30
    if pt in LOW_SENS_HIGH_OR_HIGH_SENS_LOW_PERSONALITIES:
        return 0.20
    return 0.20


def _max_non_adjacent_good_slots_on_calendar(eligible_sorted_indices):
    """
    在连续自然日上，eligible 下标集合中最多能选多少个「好睡日」且两两不相邻。
    每个最长连续 eligible 段长度为 L 时贡献 ceil(L/2)。
    """
    if not eligible_sorted_indices:
        return 0
    total = 0
    run_start = prev = eligible_sorted_indices[0]
    for x in eligible_sorted_indices[1:]:
        if x == prev + 1:
            prev = x
        else:
            total += (prev - run_start + 2) // 2
            run_start = prev = x
    total += (prev - run_start + 2) // 2
    return total


def _pick_non_adjacent_indices_from_pool(pool, k_target, max_attempts=320):
    """
    从 pool（日期下标列表）中选取至多 k_target 个，任意两个下标不相邻（自然日）。
    多次随机贪心，尽量达到 k_target；若结构上界不足则返回能取到的最大规模之一。
    """
    if k_target <= 0 or not pool:
        return []
    best = []
    for _ in range(max_attempts):
        order = list(pool)
        random.shuffle(order)
        picked = []
        picked_set = set()
        for idx in order:
            if len(picked) >= k_target:
                break
            if (idx - 1) in picked_set or (idx + 1) in picked_set:
                continue
            picked.append(idx)
            picked_set.add(idx)
        if len(picked) > len(best):
            best = picked
        if len(best) >= k_target:
            break
    return best[:k_target]


def pick_non_consecutive_day_indices(n_days, k):
    """
    在 [0, n_days-1] 中无放回选取 k 个下标，且任意两天下标不相邻（用于「差睡日」不连续）。
    可行上界为 ceil(n_days/2)；若 k 过大会先截断。
    """
    if k <= 0 or n_days <= 0:
        return []
    max_k = (n_days + 1) // 2
    k = min(k, max_k)
    if k <= 0:
        return []
    choice = sorted(random.sample(range(n_days - k + 1), k))
    return [choice[i] + i for i in range(k)]


def build_sleep_outlier_mode_by_date(start_date, end_date, personality_type):
    """
    按人格分档预先排期睡眠离群日，返回 { 'YYYY-MM-DD': None | 'bad' | 'good' }。
    规则：每人格只混入「好」或「坏」一类离群日，不同时排 bad 与 good。

    - 高敏感+高活跃（M-H-R / E-H-R）：不排 bad；约 30% 天为 good（互不相邻）。
    - 低敏感+低活跃（M-L-C / E-L-C）：约 30% 天为 bad（互不相邻）；不排 good。
    - 低敏感+高活跃 或 高敏感+低活跃（M-L-R、E-L-R、M-H-C、E-H-C）：约 20% 天为 bad；
      不排 good。
    - 未知人格编码：约 20% bad，不排 good。
    """
    if start_date is None or end_date is None or end_date < start_date:
        return {}
    n_days = (end_date - start_date).days + 1
    dates = [start_date + timedelta(days=i) for i in range(n_days)]
    result = {d.strftime("%Y-%m-%d"): None for d in dates}

    bad_frac = _sleep_calendar_bad_day_fraction(personality_type)
    k_bad = max(0, int(round(n_days * bad_frac)))
    if k_bad > 0:
        for i in pick_non_consecutive_day_indices(n_days, k_bad):
            result[dates[i].strftime("%Y-%m-%d")] = "bad"

    # 仅高敏感+高活跃：无 bad 日，在全部日历日上取约 30% good，且好睡日不相邻
    if personality_type in HIGH_SENS_HIGH_ACTIVE_PERSONALITIES:
        non_bad_indices = list(range(n_days))
        k_good = max(0, int(round(n_days * 0.30)))
        cap = _max_non_adjacent_good_slots_on_calendar(non_bad_indices)
        k_good = min(k_good, cap)
        if k_good > 0 and non_bad_indices:
            random.shuffle(non_bad_indices)
            chosen = _pick_non_adjacent_indices_from_pool(non_bad_indices, k_good)
            for i in chosen:
                result[dates[i].strftime("%Y-%m-%d")] = "good"

    return result


def strip_sleep_calendar_flags_from_health_records(health_data_list):
    """落盘前从 raw_data 移除好睡日/差睡日标记（仅生成过程使用，不写入最终 JSON）。"""
    for row in health_data_list or []:
        rd = row.get("raw_data")
        if isinstance(rd, dict):
            rd.pop("good_sleep_day", None)
            rd.pop("bad_sleep_day", None)


def apply_sleep_outlier_mode(
    personality_type,
    deep_ratio,
    light_ratio,
    rem_ratio,
    sleep_latency,
    apnea_count,
    leave_bed_count,
    leave_bed_minutes,
    sleep_outlier_mode,
    mutate_stage_ratios=True,
):
    """
    按排期注入离群夜指标（替代原先按天随机 maybe_inject）：
    - bad：所有人格均适用 — 深睡/REM 下降、浅睡上升、入睡更慢、呼吸暂停（至少 5 次）与离床增多；
      好睡人格下降幅度较大，差睡人格下降幅度较小（本已较差，降幅受限）。
    - good：差睡人格 — 与 bad 大致相反的温和改善，仍受各字段合理上下限约束。

    mutate_stage_ratios=False 时仅调整潜伏期/呼吸暂停/离床，不改 d/l/r 占比（供 TIB 四段联合抽样路径使用）。
    """
    if sleep_outlier_mode == "bad":
        if mutate_stage_ratios:
            if personality_type in GOOD_SLEEP_PERSONALITIES:
                deep_drop = random.randint(3, 8)
                rem_drop = random.randint(2, 6)
            else:
                deep_drop = random.randint(1, 4)
                rem_drop = random.randint(1, 3)
            deep_ratio = max(5, deep_ratio - deep_drop)
            rem_ratio = max(6, rem_ratio - rem_drop)
            light_ratio = min(86, light_ratio + deep_drop + rem_drop)
            total = deep_ratio + light_ratio + rem_ratio
            if total != 100:
                light_ratio += 100 - total
            light_ratio = max(0, min(100, light_ratio))
        sleep_latency = int(sleep_latency + random.randint(6, 20))
        apnea_count = int(apnea_count + random.randint(1, 4))
        apnea_count = max(5, apnea_count)
        leave_bed_count = int(leave_bed_count + random.randint(1, 2))
        leave_bed_minutes = int(leave_bed_minutes + random.randint(6, 20))
        return (
            deep_ratio,
            light_ratio,
            rem_ratio,
            sleep_latency,
            apnea_count,
            leave_bed_count,
            leave_bed_minutes,
            True,
        )

    if sleep_outlier_mode == "good" and personality_type in POOR_SLEEP_PERSONALITIES:
        if mutate_stage_ratios:
            deep_gain = random.randint(3, 8)
            rem_gain = random.randint(2, 6)
            deep_ratio = min(30, deep_ratio + deep_gain)
            rem_ratio = min(30, rem_ratio + rem_gain)
            light_ratio = max(38, light_ratio - deep_gain - rem_gain)
            total = deep_ratio + light_ratio + rem_ratio
            if total != 100:
                light_ratio += 100 - total
            light_ratio = max(0, min(100, light_ratio))
        sleep_latency = max(2, int(sleep_latency - random.randint(5, 18)))
        apnea_count = max(0, int(apnea_count - random.randint(1, 4)))
        leave_bed_count = max(0, int(leave_bed_count - random.randint(0, 2)))
        leave_bed_minutes = max(0, int(leave_bed_minutes - random.randint(5, 25)))
        return (
            deep_ratio,
            light_ratio,
            rem_ratio,
            sleep_latency,
            apnea_count,
            leave_bed_count,
            leave_bed_minutes,
            True,
        )

    return (
        deep_ratio,
        light_ratio,
        rem_ratio,
        sleep_latency,
        apnea_count,
        leave_bed_count,
        leave_bed_minutes,
        False,
    )


def _deduct_one_from_dlr_closest_to_lower_bound(d, l, r, d_lo, l_lo, r_lo):
    """awake 占比 +1 时，从深/浅/REM 中扣 1 个百分点：选 (值-下限) 最小者。"""
    candidates = [
        ("d", int(d), int(d_lo)),
        ("l", int(l), int(l_lo)),
        ("r", int(r), int(r_lo)),
    ]
    best = None
    best_key = None
    for key, val, lo in candidates:
        if val <= lo:
            continue
        margin = val - lo
        if best is None or margin < best:
            best = margin
            best_key = key
    if best_key is None:
        return d, l, r, False
    d, l, r = int(d), int(l), int(r)
    if best_key == "d":
        d -= 1
    elif best_key == "l":
        l -= 1
    else:
        r -= 1
    return d, l, r, True


def _tib_awake_minutes_from_percent(tib, aw_pct):
    tib = max(1, int(tib))
    aw_pct = int(max(0, min(100, aw_pct)))
    return int(round(tib * aw_pct / 100.0))


def _add_one_to_dlr_closest_to_upper_bound(d, l, r, d_hi, l_hi, r_hi):
    """压缩 awake 占比时，向深/浅/REM 回补 1 个百分点：选 (上限-值) 最大者。"""
    picks = []
    for key, val, hi in (("d", int(d), int(d_hi)), ("l", int(l), int(l_hi)), ("r", int(r), int(r_hi))):
        if val < hi:
            picks.append((hi - val, key))
    if not picks:
        return int(d), int(l), int(r), False
    picks.sort(reverse=True)
    _, key = picks[0]
    d, l, r = int(d), int(l), int(r)
    if key == "d":
        d += 1
    elif key == "l":
        l += 1
    else:
        r += 1
    return d, l, r, True


def _finalize_tib_stage_minutes_for_idf(
    tib,
    sleep_latency,
    post_wake_minutes,
    aw_rt,
    d_rt,
    l_rt,
    r_rt,
    a_lo,
    a_hi,
    d_lo,
    d_hi,
    l_lo,
    l_hi,
    r_lo,
    r_hi,
    awake_count,
    night_waso_cfg_lo=None,
    night_waso_cfg_hi=None,
):
    """
    按「卧床 bed→wake_up」TIB 与四段整数占比（和为 100）生成分钟数，并推导 idf 中段夜间清醒总分钟。

    - 四段占比均为人格表 ∩ 用户 config 后的闭区间抽样（awake 用 awakeSleep ∩ 人格 awake）。
    - 清醒分钟 = round(TIB * awake% / 100)；TST = TIB - 清醒；深/浅/REM 分钟在 TST 内按三占比分配。
    - 入睡潜伏期 + 觉后清醒（wake→wake_up）为「边缘清醒」；剩余清醒预算为夜间中段清醒分钟。
    - 无夜间醒来次数：不在 idf 中插入中段清醒，总清醒分钟压到边缘清醒之和（在 awake 占比允许范围内微调）。
    - 有夜间醒来次数：中段清醒段数 = 次数（不含首段潜伏期清醒与末段觉后清醒）；夜间清醒分钟需 >= max(3, 次数)
      且不足时 awake 每次 +1 并从最接近下限的深/浅/REM 占比扣 1。
    - night_waso_cfg_lo/hi：用户配置「夜间清醒总分钟」（入睡后中段），若给出且 na>0，则在 [lo,hi] 内随机目标并对齐
      总清醒分钟（在 a_lo..a_hi 与 TIB 约束下取最接近的可行 awake%）。
    """
    aw = int(aw_rt)
    d, l, r = int(d_rt), int(l_rt), int(r_rt)
    a_lo, a_hi = int(a_lo), int(a_hi)
    edge = int(sleep_latency) + int(max(0, post_wake_minutes))
    na = max(0, int(awake_count or 0))
    tib = max(1, int(tib))

    def _recompute_minutes():
        m_aw = _tib_awake_minutes_from_percent(tib, aw)
        tst = max(0, tib - m_aw)
        dp, lp, rp = _normalize_three_int100(d, l, r)
        m_d, m_l, m_r = distribute_sleep_stage_minutes(tst, dp, lp, rp)
        return int(m_aw), int(m_d), int(m_l), int(m_r)

    m_aw, m_d, m_l, m_r = _recompute_minutes()

    while m_aw < int(sleep_latency) and aw < a_hi:
        aw += 1
        d, l, r, ok = _deduct_one_from_dlr_closest_to_lower_bound(d, l, r, d_lo, l_lo, r_lo)
        if not ok:
            break
        m_aw, m_d, m_l, m_r = _recompute_minutes()

    rem_night = int(m_aw) - edge
    need_rem = max(3, na) if na > 0 else 0

    def _bump_awake_one():
        nonlocal aw, d, l, r
        if aw >= a_hi:
            return False
        aw += 1
        d, l, r, ok = _deduct_one_from_dlr_closest_to_lower_bound(d, l, r, d_lo, l_lo, r_lo)
        return ok

    if na == 0:
        night_waso = 0
        m_aw = int(max(0, min(int(edge), int(tib))))
        tst = max(0, int(tib) - int(m_aw))
        dp, lp, rp = _normalize_three_int100(d, l, r)
        m_d, m_l, m_r = distribute_sleep_stage_minutes(tst, dp, lp, rp)
        aw_rt = int(round(100.0 * float(m_aw) / float(tib))) if tib > 0 else 0
        aw_rt = max(a_lo, min(a_hi, aw_rt))
        tst_sum = int(m_d + m_l + m_r)
        if tst_sum > 0:
            d_rt, l_rt, r_rt = _normalize_three_int100(
                int(round(100.0 * float(m_d) / float(tst_sum))),
                int(round(100.0 * float(m_l) / float(tst_sum))),
                int(round(100.0 * float(m_r) / float(tst_sum))),
            )
        else:
            d_rt, l_rt, r_rt = dp, lp, rp
        return m_aw, m_d, m_l, m_r, night_waso, aw_rt, d_rt, l_rt, r_rt

    while rem_night < need_rem and aw < a_hi:
        if not _bump_awake_one():
            break
        m_aw, m_d, m_l, m_r = _recompute_minutes()
        rem_night = int(m_aw) - edge

    while rem_night == 0 and na > 0 and aw < a_hi:
        if not _bump_awake_one():
            break
        m_aw, m_d, m_l, m_r = _recompute_minutes()
        rem_night = int(m_aw) - edge

    while 0 < rem_night < need_rem and aw < a_hi:
        if not _bump_awake_one():
            break
        m_aw, m_d, m_l, m_r = _recompute_minutes()
        rem_night = int(m_aw) - edge

    night_waso = rem_night if (na > 0 and rem_night >= need_rem) else 0
    aw_rt, d_rt, l_rt, r_rt = aw, d, l, r

    if (
        na > 0
        and night_waso_cfg_lo is not None
        and night_waso_cfg_hi is not None
        and int(night_waso_cfg_lo) <= int(night_waso_cfg_hi)
    ):
        w_lo = max(int(need_rem), int(night_waso_cfg_lo))
        w_hi = int(max(w_lo, int(night_waso_cfg_hi)))
        max_mid = max(int(need_rem), int(tib) - int(edge) - 1)
        w_hi_eff = min(w_hi, max_mid)
        w_lo_eff = min(w_lo, w_hi_eff) if max_mid < w_lo else w_lo
        w_lo_eff = max(int(need_rem), int(w_lo_eff))
        w_hi_eff = max(w_lo_eff, int(w_hi_eff))
        tgt = (
            random.randint(int(w_lo_eff), int(w_hi_eff))
            if w_lo_eff <= w_hi_eff
            else int(w_hi_eff)
        )
        desired_m_aw = int(edge) + int(tgt)
        desired_m_aw = min(desired_m_aw, int(tib) - 1)
        desired_m_aw = max(desired_m_aw, int(edge) + int(need_rem))
        min_aw_pct = int(
            math.ceil(100.0 * float(desired_m_aw) / float(max(1, int(tib))))
        )
        eff_a_hi = min(97, max(int(a_hi), int(min_aw_pct)))
        best_aw = int(aw)
        best_score = abs(int(m_aw) - int(desired_m_aw))
        for cand_aw in range(int(a_lo), int(eff_a_hi) + 1):
            cand_m = int(_tib_awake_minutes_from_percent(int(tib), int(cand_aw)))
            if cand_m - int(edge) < int(need_rem):
                continue
            sc = abs(int(cand_m) - int(desired_m_aw))
            if sc < best_score:
                best_score = sc
                best_aw = int(cand_aw)
        aw = int(best_aw)
        m_aw = int(_tib_awake_minutes_from_percent(int(tib), aw))
        tst = max(0, int(tib) - int(m_aw))
        dp, lp, rp = _normalize_three_int100(int(d), int(l), int(r))
        m_d, m_l, m_r = distribute_sleep_stage_minutes(tst, dp, lp, rp)
        rem_night = int(m_aw) - int(edge)
        night_waso = rem_night if rem_night >= int(need_rem) else 0
        tst_sum = max(1, int(m_d + m_l + m_r))
        d_rt, l_rt, r_rt = _normalize_three_int100(
            int(round(100.0 * float(m_d) / float(tst_sum))),
            int(round(100.0 * float(m_l) / float(tst_sum))),
            int(round(100.0 * float(m_r) / float(tst_sum))),
        )
        aw_rt = max(int(a_lo), min(int(eff_a_hi), int(aw)))

    return m_aw, m_d, m_l, m_r, night_waso, aw_rt, d_rt, l_rt, r_rt
