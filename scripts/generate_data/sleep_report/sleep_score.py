"""睡眠结构计算、评分规则、时间线操作。"""

from __future__ import annotations

import json
import math
import os
import random
from datetime import datetime, timedelta

from .time_utils import calculate_duration


# ── 人格睡眠阶段比例范围 ──────────────────────────────────────────────────────
PERSONALITY_SLEEP_STAGE_RATIO_RANGES = {
    "M-H-R": {"deep": (13, 18), "light": (50, 57), "rem": (17, 22), "awake": (9, 15)},
    "M-H-C": {"deep": (16, 21), "light": (48, 55), "rem": (20, 24), "awake": (7, 13)},
    "M-L-R": {"deep": (18, 23), "light": (46, 52), "rem": (19, 23), "awake": (4, 9)},
    "M-L-C": {"deep": (21, 25), "light": (43, 49), "rem": (21, 25), "awake": (2, 5)},
    "E-H-R": {"deep": (11, 17), "light": (47, 55), "rem": (18, 22), "awake": (9, 14)},
    "E-H-C": {"deep": (15, 21), "light": (48, 56), "rem": (19, 23), "awake": (7, 13)},
    "E-L-R": {"deep": (17, 22), "light": (47, 53), "rem": (19, 23), "awake": (4, 10)},
    "E-L-C": {"deep": (20, 25), "light": (44, 50), "rem": (21, 25), "awake": (2, 7)},
}

GOOD_SLEEP_PERSONALITIES = frozenset({"M-L-C", "E-L-C", "M-L-R", "M-H-C", "E-H-C"})
POOR_SLEEP_PERSONALITIES = frozenset({"M-H-R", "E-H-R", "E-L-R"})
HIGH_SENS_HIGH_ACTIVE_PERSONALITIES = frozenset({"M-H-R", "E-H-R"})
LOW_SENS_LOW_ACTIVE_PERSONALITIES = frozenset({"M-L-C", "E-L-C"})
LOW_SENS_HIGH_OR_HIGH_SENS_LOW_PERSONALITIES = frozenset(
    {"M-L-R", "E-L-R", "M-H-C", "E-H-C"}
)


# ── 睡眠阶段分钟分配 ─────────────────────────────────────────────────────────

def distribute_sleep_stage_minutes(total_sleep_minutes, deep_ratio, light_ratio, rem_ratio):
    """浅睡/深睡/REM 相对净睡眠的比例（三者之和为100），拆成整数分钟且三者之和等于 total_sleep_minutes。"""
    t = int(total_sleep_minutes)
    if t <= 0:
        return 0, 0, 0
    d = t * int(deep_ratio) // 100
    l = t * int(light_ratio) // 100
    r = t * int(rem_ratio) // 100
    diff = t - d - l - r
    l += diff
    return d, l, r


def _normalize_three_int100(d, l, r):
    """将三个非负整数比例缩放到和严格为 100（最大余数法），供净睡眠 TST 上拆段。"""
    d, l, r = max(0, int(d)), max(0, int(l)), max(0, int(r))
    s = d + l + r
    if s <= 0:
        return 34, 33, 33
    scaled = [100.0 * d / s, 100.0 * l / s, 100.0 * r / s]
    floors = [int(math.floor(x)) for x in scaled]
    rem = 100 - sum(floors)
    if rem > 0:
        fracs = sorted([(scaled[i] - floors[i], i) for i in range(3)], key=lambda x: -x[0])
        for k in range(rem):
            floors[fracs[k % 3][1]] += 1
    elif rem < 0:
        fracs = sorted([(scaled[i] - floors[i], i) for i in range(3)], key=lambda x: x[0])
        for k in range(-rem):
            for _, i in fracs:
                if floors[i] > 0:
                    floors[i] -= 1
                    break
    return int(floors[0]), int(floors[1]), int(floors[2])


# ── IDF 相关 ─────────────────────────────────────────────────────────────────

def idf_all_awake_minutes(sleep_data):
    """idf_data 中所有 awake 段的分钟数之和（本地 HH:MM，支持跨日）。"""
    idf = sleep_data.get("idf_data") or []
    raw = sleep_data.get("raw_data") or {}
    anchor = sleep_data.get("record_date") or raw.get("record_date")
    total = 0
    for seg in idf:
        if (seg.get("stage") or "") != "awake":
            continue
        st, en = seg.get("start"), seg.get("end")
        if not st or not en:
            continue
        total += calculate_duration(st, en, anchor)
    return int(total)


def _idf_timeline_from_stages(stage_rows):
    """将 idf 段展开为按分钟的阶段列表（与段顺序一致）。"""
    tl = []
    for seg in stage_rows or []:
        sh, sm = map(int, seg["start"].split(":"))
        eh, em = map(int, seg["end"].split(":"))
        d = (eh * 60 + em) - (sh * 60 + sm)
        if d <= 0:
            d += 1440
        stg = seg.get("stage") or ""
        tl.extend([stg] * int(d))
    return tl


def _idf_stages_from_timeline(tl, anchor_dt):
    """分钟级阶段列表还原为 idf 段（连续同类合并）。"""
    if not tl:
        return []
    out = []
    seg_start = 0
    cur_stage = tl[0]
    for idx in range(1, len(tl) + 1):
        is_break = idx == len(tl) or tl[idx] != cur_stage
        if not is_break:
            continue
        st = anchor_dt + timedelta(minutes=seg_start)
        et = anchor_dt + timedelta(minutes=idx)
        out.append(
            {
                "stage": cur_stage,
                "start": st.strftime("%H:%M"),
                "end": et.strftime("%H:%M"),
            }
        )
        if idx < len(tl):
            seg_start = idx
            cur_stage = tl[idx]
    return out


def _runs_non_awake_in_window(tl, lo, hi):
    """[lo, hi) 内非 awake 的连续同质段 (start, end, stage)。"""
    runs = []
    i = lo
    while i < hi:
        if tl[i] == "awake":
            i += 1
            continue
        stg = tl[i]
        s = i
        while i < hi and tl[i] == stg:
            i += 1
        runs.append((s, i - 1, stg))
    return runs


def _runs_light_in_window(tl, lo, hi):
    """[lo, hi) 内浅睡连续段。"""
    runs = []
    i = lo
    while i < hi:
        if tl[i] != "light":
            i += 1
            continue
        s = i
        while i < hi and tl[i] == "light":
            i += 1
        runs.append((s, i - 1))
    return runs


def _fragment_idf_timeline_high_sensitivity(tl, sleep_off, wake_off, max_run=None, max_iter=300):
    """
    高敏感：通过「同质段内部与外侧异质分钟」对调，打断过长的单段深/浅/REM，
    使结构更碎；不改动各阶段总分钟数。max_run 控制单段上限（避免仍很长）。
    """
    if not tl or wake_off <= sleep_off:
        return tl
    if max_run is None:
        max_run = random.randint(22, 28)
    lo, hi = int(sleep_off), int(wake_off)
    for _ in range(max_iter):
        runs = _runs_non_awake_in_window(tl, lo, hi)
        bad = [r for r in runs if r[1] - r[0] + 1 > max_run]
        if not bad:
            break
        s, e, S = random.choice(bad)
        length = e - s + 1
        mid = s + length // 2
        donors = [
            p
            for p in range(lo, hi)
            if (p < s or p > e) and tl[p] != "awake" and tl[p] != S
        ]
        if not donors:
            break
        p = random.choice(donors)
        tl[mid], tl[p] = tl[p], tl[mid]
    return tl


def _smooth_light_runs_low_sensitivity_timeline(
    tl, sleep_off, wake_off, max_spread=16, max_iter=200
):
    """
    低敏感：缩小各浅睡连续段之间的时长差距（把偏长段边缘的浅睡与邻接深/REM 对调），
    在保持浅睡总分钟数不变的前提下，让浅睡段时长更均匀、过渡不那么突兀。
    """
    if not tl or wake_off <= sleep_off:
        return tl
    lo, hi = int(sleep_off), int(wake_off)
    for _ in range(max_iter):
        light_runs = _runs_light_in_window(tl, lo, hi)
        if len(light_runs) < 2:
            break
        lens = sorted(
            [(b - a + 1, a, b) for a, b in light_runs],
            key=lambda x: -x[0],
        )
        long_len, long_s, long_e = lens[0]
        short_len, short_s, short_e = lens[-1]
        if long_len - short_len <= max_spread:
            break
        swapped = False
        for u in (long_e, long_s):
            if not (lo <= u < hi) or tl[u] != "light":
                continue
            for v in (short_e + 1, short_s - 1):
                if not (lo <= v < hi):
                    continue
                if tl[v] not in ("deep", "rem"):
                    continue
                if u == v:
                    continue
                tl[u], tl[v] = tl[v], tl[u]
                swapped = True
                break
            if swapped:
                break
        if not swapped:
            for u in (long_e, long_s):
                if not (lo <= u < hi) or tl[u] != "light":
                    continue
                pool = [
                    v
                    for v in range(lo, hi)
                    if tl[v] in ("deep", "rem")
                    and not (short_s <= v <= short_e)
                    and not (long_s <= v <= long_e)
                    and abs(v - u) >= 8
                ]
                if not pool:
                    continue
                v = random.choice(pool)
                tl[u], tl[v] = tl[v], tl[u]
                swapped = True
                break
        if not swapped:
            break
    return tl


def _enforce_light_front_lt_back_timeline(tl, sleep_off, wake_off):
    """
    在分钟时间轴 [sleep_off, wake_off) 内，保证前半夜浅睡总分钟 < 后半夜浅睡总分钟；
    用前半夜浅睡与后半夜深/REM 对调实现（与 _enforce_light_back_heavier 同逻辑），
    不改变 deep/light/rem 总分钟数。
    """
    if not tl or wake_off <= sleep_off:
        return tl
    lo, hi = int(sleep_off), int(wake_off)
    wake_m = min(hi, len(tl))
    sleep_m = min(lo, len(tl))
    if wake_m <= sleep_m or sleep_m >= len(tl):
        return tl
    mid = sleep_m + (wake_m - sleep_m) // 2
    front_idx = [i for i in range(sleep_m, mid) if tl[i] == "light"]
    back_light = sum(1 for i in range(mid, wake_m) if tl[i] == "light")
    front_light = len(front_idx)
    need = front_light - back_light + 1
    if need <= 0:
        return tl
    back_non_light = [i for i in range(mid, wake_m) if tl[i] in ("deep", "rem")]
    if not back_non_light:
        return tl
    swaps = min(need, len(front_idx), len(back_non_light))
    front_pick = sorted(front_idx, reverse=True)[:swaps]
    back_pick = sorted(back_non_light)[:swaps]
    for fi, bi in zip(front_pick, back_pick):
        tl[fi], tl[bi] = tl[bi], tl[fi]
    return tl


def _redistribute_excess_light_low_sensitivity_timeline(
    tl, sleep_off, wake_off, max_light_run=60
):
    """
    低敏感：连续浅睡超过 max_light_run（默认 60）的部分不再保留为浅睡，
    改为深睡或 REM（净睡眠期内总分钟数不变，仅浅睡减少、深/REM 增加）。
    前半夜偏多标为深睡，后半夜偏多标为 REM，略随机。
    """
    if not tl or wake_off <= sleep_off:
        return tl
    lo, hi = int(sleep_off), int(wake_off)
    mid = lo + (hi - lo) // 2
    for a, b in _runs_light_in_window(tl, lo, hi):
        ln = b - a + 1
        if ln <= max_light_run:
            continue
        for idx in range(a + max_light_run, b + 1):
            if idx < mid:
                tl[idx] = "deep" if random.random() < 0.66 else "rem"
            else:
                tl[idx] = "rem" if random.random() < 0.58 else "deep"
    return tl


def _low_s_light_redistribute_and_balance_timeline(tl, sleep_off, wake_off):
    """低敏感：超长浅睡改标为深/REM 后，立刻做前半夜浅睡 < 后半夜浅睡。"""
    tl = _redistribute_excess_light_low_sensitivity_timeline(tl, sleep_off, wake_off)
    tl = _enforce_light_front_lt_back_timeline(tl, sleep_off, wake_off)
    return tl


def _tst_phase_minutes_in_timeline_window(tl, lo, hi):
    """时间轴 [lo, hi) 内 deep/light/rem 分钟计数（不含 awake）。"""
    md = ml = mr = 0
    for i in range(int(lo), int(hi)):
        if i < 0 or i >= len(tl):
            continue
        s = tl[i]
        if s == "deep":
            md += 1
        elif s == "light":
            ml += 1
        elif s == "rem":
            mr += 1
    return md, ml, mr


# ── 睡眠结构分钟与占比 ───────────────────────────────────────────────────────

def sleep_report_structure_minutes_and_percents(sleep_data):
    """
    睡眠报告 sleep_structure 用分钟与两套占比：

    - 分钟：深/浅/REM 由 total_sleep_minutes + raw 三占比拆分；清醒为 idf_data 全部 awake 段之和。
    - 饼图 percent（pie_aw…pie_r）：由四段分钟归一，**四者之和恒为 100**，便于同屏环形/条形与分钟条一致。
    - 健康口径（health_aw…health_r）：与 raw_data 对齐 —— 深/浅/REM 为占净睡(TST)%；清醒为 awake_ratio（占 TIB%），
      缺失时用 清醒分钟/上床→起床 估算。供与 health 对账、阈值判定（如 pick_main_title）、文案中的「临床%」使用。

    返回 (m_aw, m_d, m_l, m_r, pie_aw, pie_d, pie_l, pie_r, health_aw, health_d, health_l, health_r)。
    """
    raw = sleep_data.get("raw_data") or {}
    record_date = (sleep_data or {}).get("record_date") or None
    tst = max(0, int(raw.get("total_sleep_minutes") or 0))
    d0 = int(raw.get("deep_sleep_ratio") or 0)
    l0 = int(raw.get("light_sleep_ratio") or 0)
    r0 = int(raw.get("rem_ratio") or 0)
    dp, lp, rp = _normalize_three_int100(d0, l0, r0)
    m_d, m_l, m_r = distribute_sleep_stage_minutes(tst, dp, lp, rp)
    m_aw = idf_all_awake_minutes(sleep_data)

    pie_aw, pie_d, pie_l, pie_r = _int100_from_four_floats(
        [float(m_aw), float(m_d), float(m_l), float(m_r)]
    )

    health_d, health_l, health_r = int(dp), int(lp), int(rp)
    try:
        health_aw = max(0, min(100, int(raw.get("awake_ratio"))))
    except (TypeError, ValueError):
        health_aw = -1
    if health_aw < 0:
        tib = calculate_duration(
            raw.get("bed_time", ""),
            raw.get("wake_up_time", ""),
            record_date,
        )
        if tib and tib > 0:
            health_aw = max(0, min(100, int(round(100.0 * float(m_aw) / float(tib)))))
        else:
            tot = float(tst + m_aw)
            health_aw = (
                max(0, min(100, int(round(100.0 * float(m_aw) / tot))))
                if tot > 0
                else 0
            )

    return m_aw, m_d, m_l, m_r, pie_aw, pie_d, pie_l, pie_r, health_aw, health_d, health_l, health_r


# ── SPT 百分比工具 ───────────────────────────────────────────────────────────

def _split_four_spt_percents_to_int100(night_waso_minutes, deep_mins, light_mins, rem_mins, spt_minutes):
    """清醒(WASO)/深睡/浅睡/REM 占 SPT（入睡→起床）的百分比，四者之和严格为 100（整数）。"""
    spt = max(1, int(spt_minutes))
    parts = [
        100.0 * float(night_waso_minutes) / spt,
        100.0 * float(deep_mins) / spt,
        100.0 * float(light_mins) / spt,
        100.0 * float(rem_mins) / spt,
    ]
    floors = [int(math.floor(p)) for p in parts]
    rem = 100 - sum(floors)
    if rem > 0:
        fracs = sorted([(parts[i] - floors[i], i) for i in range(4)], key=lambda x: -x[0])
        for k in range(rem):
            floors[fracs[k % 4][1]] += 1
    elif rem < 0:
        fracs = sorted([(parts[i] - floors[i], i) for i in range(4)], key=lambda x: x[0])
        for k in range(-rem):
            for _, i in fracs:
                if floors[i] > 0:
                    floors[i] -= 1
                    break
    return tuple(int(x) for x in floors)


def _int100_from_four_floats(parts):
    """四个非负浮点缩放到和为 100 后，用最大余数法得到和严格为 100 的四个整数百分比。"""
    parts = [max(0.0, float(x)) for x in parts]
    s = sum(parts)
    if s <= 0:
        return (2, 34, 32, 32)
    scaled = [100.0 * p / s for p in parts]
    floors = [int(math.floor(x)) for x in scaled]
    rem = 100 - sum(floors)
    if rem > 0:
        fracs = sorted([(scaled[i] - floors[i], i) for i in range(4)], key=lambda x: -x[0])
        for k in range(rem):
            floors[fracs[k % 4][1]] += 1
    elif rem < 0:
        fracs = sorted([(scaled[i] - floors[i], i) for i in range(4)], key=lambda x: x[0])
        for k in range(-rem):
            for _, i in fracs:
                if floors[i] > 0:
                    floors[i] -= 1
                    break
    return tuple(int(x) for x in floors)


def normalize_four_spt_stage_percents(awake_p, deep_p, light_p, rem_p):
    """清醒/深睡/浅睡/REM 占 SPT 的整数百分比，规范为严格和为 100（与人格配置语义一致）。"""
    try:
        aw = int(round(float(awake_p)))
        d = int(round(float(deep_p)))
        l = int(round(float(light_p)))
        r = int(round(float(rem_p)))
    except (TypeError, ValueError):
        return _int100_from_four_floats([0.0, 0.0, 0.0, 0.0])
    aw, d, l, r = max(0, aw), max(0, d), max(0, l), max(0, r)
    if aw + d + l + r == 100:
        return aw, d, l, r
    return _int100_from_four_floats([float(aw), float(d), float(l), float(r)])


def spt_minutes_from_raw_sleep_window(raw_data, record_date_str=None):
    """SPT（入睡→起床）分钟数，与 generate_sleep_data 中四阶段占比分母一致。"""
    spt = calculate_duration(
        (raw_data or {}).get("sleep_time", ""),
        (raw_data or {}).get("wake_time", ""),
        record_date_str,
    )
    if spt >= 1:
        return int(spt)
    tst = int((raw_data or {}).get("total_sleep_minutes", 0) or 0)
    try:
        ar = float((raw_data or {}).get("awake_ratio", 0) or 0) / 100.0
    except (TypeError, ValueError):
        ar = 0.0
    if ar >= 0.999:
        return max(1, tst)
    return max(1, int(round(float(tst) / max(1e-6, (1.0 - ar)))))


def four_stage_minutes_on_spt(raw_data, record_date_str=None):
    """
    按 SPT 与四阶段整数占比（和为 100）拆出清醒/深睡/浅睡/REM 分钟，与 raw_data 写入逻辑一致。
    返回 (m_aw, m_d, m_l, m_r, spt, aw%, d%, l%, r%)。
    """
    rd = raw_data or {}
    spt = spt_minutes_from_raw_sleep_window(rd, record_date_str)
    aw, d, l, r = normalize_four_spt_stage_percents(
        rd.get("awake_ratio", 0),
        rd.get("deep_sleep_ratio", 0),
        rd.get("light_sleep_ratio", 0),
        rd.get("rem_ratio", 0),
    )
    alloc = _spt_minutes_from_int_percents(spt, aw, d, l, r)
    if not alloc:
        alloc = _spt_minutes_from_int_percents(spt, *_int100_from_four_floats([float(aw), float(d), float(l), float(r)]))
    m_aw, m_d, m_l, m_r = alloc
    return m_aw, m_d, m_l, m_r, spt, aw, d, l, r


def _spt_minutes_from_int_percents(spt, p_aw, p_d, p_l, p_r):
    """SPT 总分钟 spt；四段整数占比和为 100；返回 (m_aw, m_d, m_l, m_r) 整数分钟且和为 spt。"""
    spt = int(max(1, spt))
    ps = [int(p_aw), int(p_d), int(p_l), int(p_r)]
    if sum(ps) != 100:
        return None
    floats = [spt * p / 100.0 for p in ps]
    floors = [int(math.floor(x)) for x in floats]
    rem = spt - sum(floors)
    if rem > 0:
        fracs = sorted([(floats[i] - floors[i], i) for i in range(4)], key=lambda x: -x[0])
        for k in range(rem):
            floors[fracs[k % 4][1]] += 1
    elif rem < 0:
        fracs = sorted([(floats[i] - floors[i], i) for i in range(4)], key=lambda x: x[0])
        for k in range(-rem):
            for _, i in fracs:
                if floors[i] > 0:
                    floors[i] -= 1
                    break
    return tuple(int(x) for x in floors)


# ── 环境舒适度子分 ───────────────────────────────────────────────────────────

def load_environment_rows_for_record_date(user_id, record_date, output_dir=None):
    """读取 output 下 environment 文件中某一 record_date 的条目。"""
    output_dir = output_dir or os.getenv("OUTPUT_DIR", "output")
    path = os.path.join(output_dir, f"{user_id}_environment_data.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [
        row
        for row in data
        if isinstance(row, dict)
        and row.get("uid") == user_id
        and row.get("record_date") == record_date
    ]


def _environment_comfort_subscore(env_rows):
    """环境舒适度子分 0–100：无数据时中性分；有数据则按温湿度/噪声粗略扣分。"""
    if not env_rows:
        return 72.0
    temps = []
    noises = []
    for r in env_rows:
        t = r.get("temperature")
        if isinstance(t, (int, float)):
            temps.append(float(t))
        n = r.get("noise")
        if isinstance(n, (int, float)):
            noises.append(float(n))
    pen = 0.0
    if temps:
        mt = sum(temps) / len(temps)
        if mt < 17.0 or mt > 28.0:
            pen += 28.0
        elif mt < 18.0 or mt > 26.0:
            pen += 14.0
    if noises:
        mn = sum(noises) / len(noises)
        if mn >= 65.0:
            pen += 26.0
        elif mn >= 55.0:
            pen += 14.0
        elif mn >= 48.0:
            pen += 6.0
    return max(22.0, min(100.0, 100.0 - pen))


# ── 评分规则子分 ─────────────────────────────────────────────────────────────

def _rule_subscore_deep(d):
    d = float(d or 0)
    if d >= 20:
        return 100.0
    if d >= 15:
        return 82.0
    return max(28.0, 55.0 - (15.0 - d) * 4.5)


def _rule_subscore_light(l):
    l = float(l or 0)
    if 50.0 <= l <= 55.0:
        return 100.0
    if 50.0 < l <= 60.0:
        return 90.0
    if 60.0 < l <= 65.0:
        return 76.0
    if l < 50.0:
        return 58.0
    return 68.0


def _rule_subscore_rem(r):
    r = float(r or 0)
    if 25.0 <= r < 28.0:
        return 100.0
    if r >= 28.0:
        return 76.0
    if r >= 20.0:
        return 82.0
    return max(30.0, 55.0 - (20.0 - r) * 4.0)


def _rule_subscore_awake(a):
    a = float(a or 0)
    if a <= 5.0:
        return 100.0
    if a <= 10.0:
        return 80.0
    return max(25.0, 72.0 - (a - 10.0) * 5.5)


def _rule_subscore_tst(mins):
    """
    总睡眠时长评分（0–100）：
    - 8h–9h（480–540 min）：最优，100 分
    - 7h–8h（420–480 min）：稍微扣分，线性从 100 降至 72
    - <7h（<420 min）：短睡重罚，线性从 72 急降（约每少睡 1 分钟扣 0.9 分），下限 18
    - >9h（>540 min）：稍微扣分，线性从 100 缓降，下限 55
    """
    m = float(int(mins or 0))
    if 480.0 <= m <= 540.0:
        return 100.0
    if 420.0 <= m < 480.0:
        return 72.0 + (m - 420.0) * (28.0 / 60.0)
    if m < 420.0:
        return max(18.0, 72.0 - (420.0 - m) * (72.0 / 80.0))
    return max(55.0, 100.0 - (m - 540.0) * (45.0 / 120.0))


def _rule_subscore_apnea(n):
    n = int(n or 0)
    if n < 5:
        return 100.0
    return 32.0


def _rule_subscore_respiration(rr):
    r = float(rr or 0)
    if 12.0 <= r <= 18.0:
        return 100.0
    if r > 18.0:
        return max(25.0, 88.0 - (r - 18.0) * 8.0)
    return max(25.0, 88.0 - (12.0 - r) * 9.0)


def _rule_subscore_hr(hr):
    h = float(hr or 0)
    if h > 80.0:
        return 30.0
    if h < 45.0:
        return 45.0
    if 50.0 <= h <= 65.0:
        return 100.0
    if 45.0 <= h < 50.0:
        return 70.0
    if 65.0 < h <= 70.0:
        return 86.0
    if 70.0 < h <= 80.0:
        return 66.0
    return 78.0


def _rule_subscore_efficiency(e):
    x = float(e or 0)
    if x > 90.0:
        return 100.0
    if x >= 85.0:
        return 84.0
    if x >= 80.0:
        return 62.0
    if x >= 70.0:
        return 38.0
    return 20.0


def _rule_subscore_latency(lat):
    """
    入睡潜伏期评分（0–100）：
    - ≤20 min：满分 100
    - 20–25 min：稍微扣分，线性 100 → 85
    - 25–30 min：扣分稍多，线性 85 → 65
    - >30 min：扣分再多一些，线性 65 急降，下限 20
    """
    m = float(lat or 0)
    if m <= 20.0:
        return 100.0
    if m <= 25.0:
        return 100.0 - (m - 20.0) * (15.0 / 5.0)
    if m <= 30.0:
        return 85.0 - (m - 25.0) * (20.0 / 5.0)
    return max(20.0, 65.0 - (m - 30.0) * (45.0 / 30.0))


# ── 综合评分 ─────────────────────────────────────────────────────────────────

def calculate_sleep_report_structure_score(
    total_sleep_minutes, deep_percent, light_percent, rem_percent
):
    """
    睡眠报告综合分 0–100：仅由净睡眠时长（分钟）与深/浅/REM 占净睡比例四项
    子分算术平均（与 _rule_subscore_tst / deep / light / rem 规则一致）。
    """
    parts = [
        _rule_subscore_tst(float(total_sleep_minutes or 0)),
        _rule_subscore_deep(float(deep_percent or 0)),
        _rule_subscore_light(float(light_percent or 0)),
        _rule_subscore_rem(float(rem_percent or 0)),
    ]
    out = int(round(sum(parts) / len(parts)))
    return max(0, min(100, out))


def sleep_report_score_from_sleep_data(sleep_data):
    """从单条 sleep 记录 raw_data 提取净睡时长与三阶段占比，得到报告用综合分。"""
    rd = (sleep_data or {}).get("raw_data") or {}
    tst = max(0, int(rd.get("total_sleep_minutes") or 0))
    dp, lp, rp = _normalize_three_int100(
        int(rd.get("deep_sleep_ratio") or 0),
        int(rd.get("light_sleep_ratio") or 0),
        int(rd.get("rem_ratio") or 0),
    )
    return calculate_sleep_report_structure_score(tst, dp, lp, rp)


def calculate_rule_based_sleep_score(sleep_data, environment_rows=None):
    """
    规则化睡眠分 0–100：综合深睡/浅睡/REM 占比、净睡眠时长、入睡潜伏期、呼吸暂停、
    平均呼吸率、平均心率、睡眠效率及（可选）环境采样子分，取十项算术平均。
    清醒占比不参与分数计算。
    """
    rd = (sleep_data or {}).get("raw_data") or {}
    parts = [
        _rule_subscore_deep(rd.get("deep_sleep_ratio", 0)),
        _rule_subscore_light(rd.get("light_sleep_ratio", 0)),
        _rule_subscore_rem(rd.get("rem_ratio", 0)),
        _rule_subscore_tst(rd.get("total_sleep_minutes", 0)),
        _rule_subscore_latency(rd.get("sleep_latency", 0)),
        _rule_subscore_apnea(rd.get("apnea_count", 0)),
        _rule_subscore_respiration(rd.get("average_respiration", 0)),
        _rule_subscore_hr(rd.get("average_heartbeat", 0)),
        _rule_subscore_efficiency(rd.get("sleep_efficiency", 0)),
        _environment_comfort_subscore(environment_rows or []),
    ]
    out = int(round(sum(parts) / len(parts)))
    return max(0, min(100, out))


def recalculate_rule_based_sleep_scores_in_health(user_id, output_dir=None):
    """在已生成 environment 后，按规则分与环境数据写回 health_data 的 sleep_score。"""
    output_dir = output_dir or os.getenv("OUTPUT_DIR", "output")
    health_path = os.path.join(output_dir, f"{user_id}_health_data.json")
    if not os.path.isfile(health_path):
        return 0
    try:
        with open(health_path, "r", encoding="utf-8") as f:
            health = json.load(f)
    except Exception:
        return 0
    if not isinstance(health, list):
        return 0
    n = 0
    for rec in health:
        if not isinstance(rec, dict):
            continue
        rd = rec.get("record_date")
        uid = rec.get("user_id")
        if not rd or not uid:
            continue
        env = load_environment_rows_for_record_date(uid, rd, output_dir=output_dir)
        sc = calculate_rule_based_sleep_score(rec, environment_rows=env)
        rec.setdefault("raw_data", {})
        rec["raw_data"]["sleep_score"] = sc
        n += 1
    try:
        with open(health_path, "w", encoding="utf-8") as f:
            json.dump(health, f, ensure_ascii=False, indent=2)
    except Exception:
        return n
    return n


# ── 比例采样 ─────────────────────────────────────────────────────────────────

def _sample_stage_ratio(cfg_lo, cfg_hi, p_lo, p_hi):
    lo = max(int(cfg_lo), int(p_lo))
    hi = min(int(cfg_hi), int(p_hi))
    if lo > hi:
        lo, hi = int(p_lo), int(p_hi)
    return random.randint(lo, hi)


def _intersect_cfg_pb(user, key, pb_lo, pb_hi):
    """用户 config 中 key 的区间与人格表 [pb_lo,pb_hi] 求交；交为空时退化为人格表区间。"""
    c = user.get(key) or {}
    try:
        u_lo = int((c.get("min") or [pb_lo])[0])
        u_hi = int((c.get("max") or [pb_hi])[0])
    except (TypeError, ValueError, IndexError):
        u_lo, u_hi = int(pb_lo), int(pb_hi)
    if u_lo > u_hi:
        u_lo, u_hi = u_hi, u_lo
    lo = max(u_lo, int(pb_lo))
    hi = min(u_hi, int(pb_hi))
    if lo > hi:
        return int(pb_lo), int(pb_hi)
    return lo, hi


def _sample_four_spt_percents_bounded(
    a_lo, a_hi, d_lo, d_hi, l_lo, l_hi, r_lo, r_hi, min_awake=1, max_draws=3000
):
    """
    在闭区间内随机 (aw,d,l,r) 整数百分比，满足 aw+d+l+r=100、aw>=min_awake。
    各段均在给定上下界内；若整体不可行返回 None。
    """
    a_lo = max(int(min_awake), int(a_lo))
    a_hi = max(a_lo, int(a_hi))
    d_lo, d_hi = int(d_lo), int(d_hi)
    l_lo, l_hi = int(l_lo), int(l_hi)
    r_lo, r_hi = int(r_lo), int(r_hi)
    aw_min_fe = max(a_lo, 100 - d_hi - l_hi - r_hi)
    aw_max_fe = min(a_hi, 100 - d_lo - l_lo - r_lo)
    if aw_min_fe > aw_max_fe:
        return None
    for _ in range(max_draws):
        aw = random.randint(aw_min_fe, aw_max_fe)
        r_sum = 100 - aw
        d_min = max(d_lo, r_sum - l_hi - r_hi)
        d_max = min(d_hi, r_sum - l_lo - r_lo)
        if d_min > d_max:
            continue
        d = random.randint(d_min, d_max)
        r2 = r_sum - d
        l_min = max(l_lo, r2 - r_hi)
        l_max = min(l_hi, r2 - r_lo)
        if l_min > l_max:
            continue
        l = random.randint(l_min, l_max)
        r = r2 - l
        if r_lo <= r <= r_hi:
            return aw, d, l, r
    return None
