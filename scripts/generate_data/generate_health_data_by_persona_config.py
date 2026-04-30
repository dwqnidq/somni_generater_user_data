"""根据 config/health_data_personas_config.json 生成各人格的睡眠健康数据。

输出格式与 output/{uid}_health_data.json 保持一致：
  record_date / raw_data / idf_data / create_time / update_time / uid

用法示例：
  python scripts/generate_data/generate_health_data_by_persona_config.py
  python scripts/generate_data/generate_health_data_by_persona_config.py --user-id 69aea6d8af5e6cbf08027966
  python scripts/generate_data/generate_health_data_by_persona_config.py --state good
  python scripts/generate_data/generate_health_data_by_persona_config.py --state mixed --good-ratio 0.6
  python scripts/generate_data/generate_health_data_by_persona_config.py --start-date 2026-03-01 --end-date 2026-03-31
"""

import argparse
import json
import os
import random
import sys
from datetime import date, datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

TIMEZONE_OFFSET = 8  # UTC+8
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "health_data_personas_config.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")


# ──────────────────────────────────────────────
# 时间工具函数
# ──────────────────────────────────────────────

def _parse_hhmm(s: str):
    """'HH:MM' → (h, m)"""
    h, m = map(int, s.split(":"))
    return h, m


def _hhmm_to_minutes(s: str) -> int:
    h, m = _parse_hhmm(s)
    return h * 60 + m


def _pick_time_on_date(base_dt: datetime, time_range: list) -> datetime:
    """在 base_dt 当天，按 time_range [min_str, max_str] 随机采样一个 datetime。
    若 max < min（跨午夜），end 自动加 1 天。"""
    min_h, min_m = _parse_hhmm(time_range[0])
    max_h, max_m = _parse_hhmm(time_range[1])
    start = base_dt.replace(hour=min_h, minute=min_m, second=0, microsecond=0)
    end = base_dt.replace(hour=max_h, minute=max_m, second=0, microsecond=0)
    if end <= start:
        end += timedelta(days=1)
    span = int((end - start).total_seconds() // 60)
    offset = random.randint(0, max(0, span))
    return start + timedelta(minutes=offset)


def _local_to_utc_iso(local_dt: datetime) -> str:
    utc_dt = local_dt - timedelta(hours=TIMEZONE_OFFSET)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _minutes_to_hhmm(minutes: int) -> str:
    m = minutes % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _window_start_end(base_dt: datetime, lo_str: str, hi_str: str) -> tuple[datetime, datetime]:
    """与 _pick_time_on_date 相同语义：在 base_dt 日历日上解析 [lo_str, hi_str]，跨日时 end 顺延一天。"""
    min_h, min_m = _parse_hhmm(lo_str)
    max_h, max_m = _parse_hhmm(hi_str)
    start = base_dt.replace(hour=min_h, minute=min_m, second=0, microsecond=0)
    end = base_dt.replace(hour=max_h, minute=max_m, second=0, microsecond=0)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _sleep_episode_window(base_dt: datetime, lo_str: str, hi_str: str) -> tuple[datetime, datetime]:
    """入睡时刻窗：未跨日的 0:00～12:00 前区间视为 record_date 次日凌晨（与跨午夜锚点、卧床窗同一夜）。"""
    lo_m = _hhmm_to_minutes(lo_str)
    hi_m = _hhmm_to_minutes(hi_str)
    if lo_m <= hi_m and hi_m < 12 * 60:
        return _window_start_end(base_dt + timedelta(days=1), lo_str, hi_str)
    return _window_start_end(base_dt, lo_str, hi_str)


def _pick_random_in_datetime_window(start_dt: datetime, end_dt: datetime) -> datetime:
    span = int((end_dt - start_dt).total_seconds() // 60)
    return start_dt + timedelta(minutes=random.randint(0, max(0, span)))


def _wall_clock_in_hhmm_range(dt: datetime, rng: list[str]) -> bool:
    """仅比较当日钟点（时:分）是否在 [rng[0], rng[1]] 内，支持跨午夜区间（lo > hi）。"""
    m = dt.hour * 60 + dt.minute
    lo, hi = _hhmm_to_minutes(rng[0]), _hhmm_to_minutes(rng[1])
    if lo <= hi:
        return lo <= m <= hi
    return m >= lo or m <= hi


def _intersect_anchor_sleep_windows(
    base_dt: datetime, center: int, jitter: int, sleep_range: list[str]
) -> tuple[datetime, datetime] | None:
    """锚点 [center±jitter] 与 sleep_time_range 在 base_dt 上的时间窗求交；空则返回 None。"""
    aw_lo = _minutes_to_hhmm((center - jitter) % 1440)
    aw_hi = _minutes_to_hhmm((center + jitter) % 1440)
    a0, a1 = _window_start_end(base_dt, aw_lo, aw_hi)
    s0, s1 = _sleep_episode_window(base_dt, sleep_range[0], sleep_range[1])
    ist = max(a0, s0)
    ien = min(a1, s1)
    if ist > ien:
        return None
    return ist, ien


def _pick_sleep_latency_for_bed_range(
    sleep_time_local: datetime, lat_rng: list[int], bed_range: list[str]
) -> int:
    """在 sleep_latency 配置区间内选取使卧床时刻落在 bed_time_range 的值；无解则回退随机。"""
    lo, hi = int(lat_rng[0]), int(lat_rng[1])
    lo, hi = min(lo, hi), max(lo, hi)
    valid = [
        lat for lat in range(lo, hi + 1)
        if _wall_clock_in_hhmm_range(sleep_time_local - timedelta(minutes=lat), bed_range)
    ]
    if valid:
        return random.choice(valid)
    return random.randint(lo, hi)


def _find_feasible_shift_minutes(
    bed_dt: datetime,
    sleep_dt: datetime,
    wake_up_dt: datetime,
    sleep_range: list[str],
    bed_range: list[str],
    wake_up_range: list[str],
    search_radius: int = 1440,
) -> int | None:
    """对 bed/sleep/wake_up 同时平移相同整数分钟，使三者钟点均落入配置区间；优先 |Δ| 最小。"""
    candidates: list[int] = []
    for d in range(-search_radius, search_radius + 1):
        if (
            _wall_clock_in_hhmm_range(bed_dt + timedelta(minutes=d), bed_range)
            and _wall_clock_in_hhmm_range(sleep_dt + timedelta(minutes=d), sleep_range)
            and _wall_clock_in_hhmm_range(wake_up_dt + timedelta(minutes=d), wake_up_range)
        ):
            candidates.append(d)
    if not candidates:
        return None
    best = min(abs(c) for c in candidates)
    narrowed = [c for c in candidates if abs(c) == best]
    return random.choice(narrowed)


# ──────────────────────────────────────────────
# 睡眠阶段生成
# ──────────────────────────────────────────────

def _build_sleep_stage_timeline(deep_min: int, light_min: int, rem_min: int) -> list:
    """生成写实的睡眠阶段分钟级序列（不含前后清醒段）。
    前半夜深睡主导，后半夜 REM 主导，轻睡作过渡。"""
    total = deep_min + light_min + rem_min
    if total <= 0:
        return []

    remaining = {"deep": deep_min, "light": light_min, "rem": rem_min}
    timeline: list = []
    elapsed = 0

    def _take(stage: str, lo: int, hi: int) -> int:
        avail = remaining[stage]
        if avail <= 0:
            return 0
        dur = min(avail, random.randint(max(7, lo), max(7, hi)))
        remaining[stage] -= dur
        return dur

    while sum(remaining.values()) > 0:
        # 轻睡过渡
        dur = _take("light", 12, 40)
        if dur:
            timeline.extend(["light"] * dur)
            elapsed += dur

        # 根据当前进度决定主要阶段
        position = elapsed / max(1, total)
        if position < 0.55 and remaining["deep"] > 0:
            dur = _take("deep", 20, 45)
            if dur:
                timeline.extend(["deep"] * dur)
                elapsed += dur
        elif remaining["rem"] > 0:
            dur = _take("rem", 15, 50)
            if dur:
                timeline.extend(["rem"] * dur)
                elapsed += dur
        elif remaining["deep"] > 0:
            dur = _take("deep", 10, 30)
            if dur:
                timeline.extend(["deep"] * dur)
                elapsed += dur
        else:
            # 只剩轻睡时继续补充
            if remaining["light"] <= 0:
                break

    # 兜底：追加剩余分钟
    for stage in ("deep", "light", "rem"):
        leftover = remaining[stage]
        if leftover > 0:
            timeline.extend([stage] * leftover)

    return timeline


def _compress_timeline(timeline: list) -> list:
    """将连续相同阶段合并，去除 < 7 分钟的非 awake 碎片段（合入相邻段）。"""
    if not timeline:
        return []

    # 合并连续相同
    runs: list = []
    i = 0
    while i < len(timeline):
        st = timeline[i]
        j = i + 1
        while j < len(timeline) and timeline[j] == st:
            j += 1
        runs.append([st, j - i])
        i = j

    # 去除极短非 awake 碎片（< 7 min）
    changed = True
    while changed:
        changed = False
        k = 0
        while k < len(runs):
            st, dur = runs[k]
            if st != "awake" and dur < 7:
                if k > 0:
                    runs[k - 1][1] += dur
                    runs.pop(k)
                    if k < len(runs) and runs[k - 1][0] == runs[k][0]:
                        runs[k - 1][1] += runs[k][1]
                        runs.pop(k)
                elif k + 1 < len(runs):
                    runs[k + 1][1] += dur
                    runs.pop(k)
                else:
                    k += 1
                    continue
                changed = True
            else:
                k += 1

    # 展开为阶段序列
    result = []
    for st, dur in runs:
        result.extend([st] * dur)
    return result


def _sample_ratios_in_range(
    deep_range: list, light_range: list, rem_range: list, awake_range: list
) -> tuple:
    """各阶段比例在各自范围内随机采样，且四者之和等于 100。

    采用约束采样：先随机 d、r，再由余量反推 a 的合法区间并在其中采样，
    保证只要配置范围内存在合法组合就一定能命中，不依赖暴力盲猜次数。
    若确实无解（配置本身不合法）则抛出 ValueError。
    """
    d_lo, d_hi = int(deep_range[0]), int(deep_range[1])
    l_lo, l_hi = int(light_range[0]), int(light_range[1])
    r_lo, r_hi = int(rem_range[0]), int(rem_range[1])
    a_lo, a_hi = int(awake_range[0]), int(awake_range[1])

    # 枚举全部 d 和 r 的顺序打乱后逐一尝试，确保均匀覆盖
    d_vals = list(range(d_lo, d_hi + 1))
    r_vals = list(range(r_lo, r_hi + 1))
    random.shuffle(d_vals)
    random.shuffle(r_vals)

    for d in d_vals:
        for r in r_vals:
            remainder = 100 - d - r
            # a 需同时满足 a ∈ [a_lo, a_hi] 且 (remainder - a) ∈ [l_lo, l_hi]
            a_lo_c = max(a_lo, remainder - l_hi)
            a_hi_c = min(a_hi, remainder - l_lo)
            if a_lo_c <= a_hi_c:
                a = random.randint(a_lo_c, a_hi_c)
                l = remainder - a
                return d, l, r, a

    raise ValueError(
        f"睡眠阶段比例配置无合法解，请检查各范围是否能凑出四者之和=100："
        f"deep={deep_range}, light={light_range}, rem={rem_range}, awake={awake_range}"
    )


def _adjust_int_tuple_to_sum_100(
    vals: list[int], bounds: list[tuple[int, int]]
) -> bool:
    """各值已在对应 bounds 内时，仅通过 ±1 将四者之和调为 100。"""
    for _ in range(400):
        s = vals[0] + vals[1] + vals[2] + vals[3]
        if s == 100:
            return True
        if s < 100:
            best_i = -1
            best_room = -1
            for i, (_lo, hi) in enumerate(bounds):
                room = hi - vals[i]
                if room > best_room:
                    best_room = room
                    best_i = i
            if best_room <= 0:
                return False
            vals[best_i] += 1
        else:
            best_i = -1
            best_room = -1
            for i, (lo, _hi) in enumerate(bounds):
                room = vals[i] - lo
                if room > best_room:
                    best_room = room
                    best_i = i
            if best_room <= 0:
                return False
            vals[best_i] -= 1
    return vals[0] + vals[1] + vals[2] + vals[3] == 100


def _fit_ratios_to_config_bounds_and_sum_100(
    deep: int,
    light: int,
    rem: int,
    awake: int,
    deep_range: list,
    light_range: list,
    rem_range: list,
    awake_range: list,
) -> tuple[int, int, int, int] | None:
    """裁剪到各配置区间后，将四段睡眠比例凑为整数和 100；不可行则返回 None。"""
    bounds = [
        (min(int(deep_range[0]), int(deep_range[1])), max(int(deep_range[0]), int(deep_range[1]))),
        (min(int(light_range[0]), int(light_range[1])), max(int(light_range[0]), int(light_range[1]))),
        (min(int(rem_range[0]), int(rem_range[1])), max(int(rem_range[0]), int(rem_range[1]))),
        (min(int(awake_range[0]), int(awake_range[1])), max(int(awake_range[0]), int(awake_range[1]))),
    ]
    vals = [
        max(bounds[0][0], min(bounds[0][1], int(deep))),
        max(bounds[1][0], min(bounds[1][1], int(light))),
        max(bounds[2][0], min(bounds[2][1], int(rem))),
        max(bounds[3][0], min(bounds[3][1], int(awake))),
    ]
    if _adjust_int_tuple_to_sum_100(vals, bounds):
        return vals[0], vals[1], vals[2], vals[3]
    return None


def _split_awake(total_minutes: int, n: int) -> list:
    """将 total_minutes 拆成 n 段夜间清醒，每段至少 2 分钟。"""
    if n <= 0 or total_minutes < 2:
        return []
    n = min(n, total_minutes // 2)
    if n <= 0:
        return []
    parts = [2] * n
    remaining = total_minutes - 2 * n
    for _ in range(remaining):
        parts[random.randint(0, n - 1)] += 1
    return parts


def _insert_night_awakenings(stage_tl: list, awakening_durations: list) -> list:
    """在睡眠阶段时间线中随机插入夜间清醒段，避免过于靠近两端。"""
    if not awakening_durations or not stage_tl:
        return stage_tl
    n = len(awakening_durations)
    total = len(stage_tl)
    margin = max(1, total // 8)
    pool = list(range(margin, total - margin))
    if len(pool) < n:
        pool = list(range(1, total))
    if len(pool) < n:
        return stage_tl
    positions = sorted(random.sample(pool, n))
    result: list = []
    prev = 0
    for pos, dur in zip(positions, awakening_durations):
        result.extend(stage_tl[prev:pos])
        result.extend(["awake"] * dur)
        prev = pos
    result.extend(stage_tl[prev:])
    return result


def _timeline_to_idf(timeline: list, start_dt: datetime) -> list:
    """分钟级阶段序列 → idf_data 段列表（连续相同段合并）。"""
    if not timeline:
        return []
    segs = []
    i = 0
    n = len(timeline)
    base_min = start_dt.hour * 60 + start_dt.minute

    while i < n:
        st = timeline[i]
        j = i + 1
        while j < n and timeline[j] == st:
            j += 1
        s_min = (base_min + i) % 1440
        e_min = (base_min + j) % 1440
        segs.append({
            "stage": st,
            "start": f"{s_min // 60:02d}:{s_min % 60:02d}",
            "end": f"{e_min // 60:02d}:{e_min % 60:02d}",
        })
        i = j
    return segs


def build_idf_data(bed_time_local: datetime, sleep_latency: int,
                   total_sleep_min: int, deep_min: int, light_min: int,
                   rem_min: int, wake_after_sleep: int,
                   awakening_durations: list | None = None) -> list:
    """构建完整的 idf_data：[awake(潜伏)] + [睡眠阶段+夜间清醒] + [awake(觉后)]"""
    timeline: list = ["awake"] * sleep_latency
    stage_tl = _build_sleep_stage_timeline(deep_min, light_min, rem_min)
    stage_tl = _compress_timeline(stage_tl)
    if awakening_durations:
        stage_tl = _insert_night_awakenings(stage_tl, awakening_durations)
    timeline.extend(stage_tl)
    timeline.extend(["awake"] * max(1, wake_after_sleep))
    return _timeline_to_idf(timeline, bed_time_local)


# ──────────────────────────────────────────────
# 睡眠评分
# ──────────────────────────────────────────────

def compute_sleep_score(efficiency: int, deep_ratio: int,
                        rem_ratio: int, awake_ratio: int) -> int:
    """由睡眠效率、深睡比例、REM 比例、清醒比例计算睡眠评分（0-100）。"""
    score = (
        efficiency
        + (deep_ratio - 15) * 0.3
        + (rem_ratio - 20) * 0.1
        - max(0, awake_ratio - 10) * 0.5
    )
    score += random.randint(-3, 3)
    return max(30, min(100, round(score)))


# ──────────────────────────────────────────────
# 单日数据生成
# ──────────────────────────────────────────────

def _randint_range(rng: list) -> int:
    lo, hi = int(rng[0]), int(rng[1])
    return random.randint(min(lo, hi), max(lo, hi))


def generate_day_record(record_date: date, uid: str, state_cfg: dict,
                        now_utc: datetime, state_label: str = "good",
                        schedule_anchor: dict | None = None,
                        persona_code: str = "M-L-C") -> dict:
    """生成某一天的健康数据记录。

    schedule_anchor 由调用方预先计算：{"sleep_center": int(分钟), "jitter": int(分钟)}
    当存在时，入睡时间在 center ± jitter 内随机，而非从配置全范围采样。
    """
    for _ in range(150):
        rec = _try_generate_day_record_impl(
            record_date, uid, state_cfg, now_utc, state_label, schedule_anchor,
            persona_code,
        )
        if rec is not None:
            return rec
    raise RuntimeError(
        "无法在 150 次尝试内使 bed/sleep/wake_up 经同一平移后同时落入配置区间："
        f"uid={uid} record_date={record_date}"
    )


def _try_generate_day_record_impl(
    record_date: date,
    uid: str,
    state_cfg: dict,
    now_utc: datetime,
    state_label: str,
    schedule_anchor: dict | None,
    persona_code: str = "M-L-C",
) -> dict | None:
    """单次随机生成；若不存在满足三向时间窗的整体平移则返回 None 供外层重试。"""
    # ── 时间采样 ──────────────────────────────────
    base_dt = datetime(record_date.year, record_date.month, record_date.day)
    if schedule_anchor:
        center = schedule_anchor["sleep_center"]
        jitter = schedule_anchor["jitter"]
        inter = _intersect_anchor_sleep_windows(
            base_dt, center, jitter, state_cfg["sleep_time_range"]
        )
        if inter is None:
            sw0, sw1 = _sleep_episode_window(
                base_dt, state_cfg["sleep_time_range"][0], state_cfg["sleep_time_range"][1]
            )
            sleep_time_local = _pick_random_in_datetime_window(sw0, sw1)
        else:
            sleep_time_local = _pick_random_in_datetime_window(inter[0], inter[1])
    else:
        sw0, sw1 = _sleep_episode_window(
            base_dt, state_cfg["sleep_time_range"][0], state_cfg["sleep_time_range"][1]
        )
        sleep_time_local = _pick_random_in_datetime_window(sw0, sw1)
    sleep_latency = _pick_sleep_latency_for_bed_range(
        sleep_time_local, state_cfg["sleep_latency"], state_cfg["bed_time_range"]
    )
    bed_time_local = sleep_time_local - timedelta(minutes=sleep_latency)
    wake_after_sleep = _randint_range(state_cfg["wake_after_sleep"])

    # ── 四段比例采样（各自在范围内且和为 100）────────────────
    deep_ratio, light_ratio, rem_ratio, awake_ratio = _sample_ratios_in_range(
        state_cfg["deep_sleep_ratio"],
        state_cfg["light_sleep_ratio"],
        state_cfg["rem_ratio"],
        state_cfg["awake_ratio"],
    )

    # ── TIB：由 TST 种子和睡眠效率推导，确保 TST 贴合配置范围 ──
    tst_seed = _randint_range(state_cfg["total_sleep_minutes"])
    sleep_efficiency_seed = 100 - awake_ratio
    tib = max(
        tst_seed + sleep_latency + wake_after_sleep,
        round(tst_seed / max(1, sleep_efficiency_seed) * 100),
    )

    # ── 各阶段分钟数依据 TIB 计算 ─────────────────────────
    deep_min = round(tib * deep_ratio / 100)
    light_min = round(tib * light_ratio / 100)
    rem_min = round(tib * rem_ratio / 100)
    total_sleep_min = deep_min + light_min + rem_min

    # 三次 round() 累积误差可能使 total_sleep_min 偏出配置范围 ±1~2 分钟，
    # 通过调整最大段（浅睡）来修正，保持 TIB 总账不变
    tst_lo, tst_hi = int(state_cfg["total_sleep_minutes"][0]), int(state_cfg["total_sleep_minutes"][1])
    if total_sleep_min < tst_lo:
        light_min += tst_lo - total_sleep_min
        total_sleep_min = tst_lo
    elif total_sleep_min > tst_hi:
        light_min -= total_sleep_min - tst_hi
        light_min = max(0, light_min)
        total_sleep_min = deep_min + light_min + rem_min

    # ── 夜间清醒：awake 配额扣除入睡潜伏和觉后清醒后的余量 ────
    n_awakenings = _randint_range(state_cfg["night_awakenings"])
    nighttime_awake = max(0, tib - total_sleep_min - sleep_latency - wake_after_sleep)
    awakening_durations = _split_awake(nighttime_awake, n_awakenings)

    # ── 输出用四段比例：由实际分钟反推，再裁剪到配置区间并凑整为和 100 ──
    awake_total_min = sleep_latency + wake_after_sleep + sum(awakening_durations)
    dr, lr, rr, ar = (
        state_cfg["deep_sleep_ratio"],
        state_cfg["light_sleep_ratio"],
        state_cfg["rem_ratio"],
        state_cfg["awake_ratio"],
    )
    d_e = round(100 * deep_min / max(1, tib))
    l_e = round(100 * light_min / max(1, tib))
    r_e = round(100 * rem_min / max(1, tib))
    a_e = round(100 * awake_total_min / max(1, tib))
    fitted = _fit_ratios_to_config_bounds_and_sum_100(d_e, l_e, r_e, a_e, dr, lr, rr, ar)
    if fitted is not None:
        deep_ratio, light_ratio, rem_ratio, awake_ratio = fitted
    else:
        deep_ratio, light_ratio, rem_ratio, awake_ratio = _sample_ratios_in_range(dr, lr, rr, ar)

    sleep_efficiency = min(100, max(0, round(100 * total_sleep_min / max(1, tib))))

    # ── 时间节点 ──────────────────────────────────────────
    wake_time_local = sleep_time_local + timedelta(
        minutes=total_sleep_min + sum(awakening_durations)
    )
    wake_up_time_local = wake_time_local + timedelta(minutes=wake_after_sleep)

    # 方案 A：整体平移钟点，使 bed/sleep/wake_up 落入配置区间，不改变睡眠结构分钟数
    shift_m = _find_feasible_shift_minutes(
        bed_time_local,
        sleep_time_local,
        wake_up_time_local,
        state_cfg["sleep_time_range"],
        state_cfg["bed_time_range"],
        state_cfg["wake_up_time_range"],
    )
    if shift_m is None:
        return None
    td_shift = timedelta(minutes=shift_m)
    bed_time_local += td_shift
    sleep_time_local += td_shift
    wake_time_local += td_shift
    wake_up_time_local += td_shift

    # ── 其他体征指标 ────────────────────────────────
    avg_heartbeat = _randint_range(state_cfg["average_heartbeat"])
    avg_respiration = _randint_range(state_cfg["average_respiration"])
    apnea_count = _randint_range(state_cfg["apnea_count"])
    turnover_count = _randint_range(state_cfg["turnover_count"])

    # ── UTC 时间 ────────────────────────────────────
    bed_time_utc = _local_to_utc_iso(bed_time_local)
    sleep_time_utc = _local_to_utc_iso(sleep_time_local)
    wake_time_utc = _local_to_utc_iso(wake_time_local)
    wake_up_time_utc = _local_to_utc_iso(wake_up_time_local)

    sleep_score = compute_sleep_score(sleep_efficiency, deep_ratio,
                                      rem_ratio, awake_ratio)

    # ── idf_data（须与平移后的 bed_time_local 一致）────────────────
    idf = build_idf_data(
        bed_time_local, sleep_latency,
        total_sleep_min, deep_min, light_min, rem_min, wake_after_sleep,
        awakening_durations,
    )

    ts = now_utc.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    return {
        "record_date": record_date.strftime("%Y-%m-%d"),
        "data_label": state_label,
        "raw_data": {
            "apnea_count": apnea_count,
            "average_heartbeat": avg_heartbeat,
            "average_respiration": avg_respiration,
            "turnover_count": turnover_count,
            "awake_ratio": awake_ratio,
            "deep_sleep_ratio": deep_ratio,
            "light_sleep_ratio": light_ratio,
            "rem_ratio": rem_ratio,
            "sleep_score": sleep_score,
            "total_sleep_minutes": total_sleep_min,
            "bed_time": bed_time_utc,
            "sleep_time": sleep_time_utc,
            "wake_time": wake_time_utc,
            "wake_up_time": wake_up_time_utc,
            "sleep_latency": sleep_latency,
            "sleep_efficiency": sleep_efficiency,
        },
        "idf_data": idf,
        "create_time": ts,
        "update_time": ts,
        "uid": uid,
    }


# ──────────────────────────────────────────────
# 主生成逻辑
# ──────────────────────────────────────────────

def generate_persona_health_data(
    persona: dict,
    state_mode: str = "mixed",
    good_ratio: float = 0.5,
    start_date_override: date | None = None,
    end_date_override: date | None = None,
    overwrite: bool = False,
) -> str:
    """为单个人格生成全部日期的健康数据，保存到 output/{uid}_health_data.json。
    返回输出文件路径。"""
    uid = persona["user_id"]
    name = persona["name"]
    date_range = persona.get("date_range", {})
    states = persona["sleep_metric_states"]

    start_str = date_range["start"]
    end_str = date_range["end"]
    start_dt = datetime.strptime(start_str, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_str, "%Y-%m-%d").date()

    if start_date_override:
        start_dt = max(start_dt, start_date_override)
    if end_date_override:
        end_dt = min(end_dt, end_date_override)

    if start_dt > end_dt:
        print(f"[{name}] 日期范围无效，跳过")
        return ""

    out_file = os.path.join(OUTPUT_DIR, f"{uid}_health_data.json")
    if not overwrite and os.path.exists(out_file):
        print(f"[{name}] 文件已存在，跳过（使用 --overwrite 强制重新生成）")
        return out_file

    now_utc = datetime.utcnow()
    records = []

    # 预计算每个状态的「惯常入睡中心」：有 schedule_jitter_minutes 时，
    # 在配置范围内随机取一个中心点，该人格本次生成的所有对应状态天数都围绕此中心 ± jitter 采样。
    state_anchors: dict = {}
    for sk, scfg in states.items():
        jitter = scfg.get("schedule_jitter_minutes")
        if jitter is not None:
            lo = _hhmm_to_minutes(scfg["sleep_time_range"][0])
            hi = _hhmm_to_minutes(scfg["sleep_time_range"][1])
            if hi < lo:
                hi += 1440
            center = random.randint(lo, hi) % 1440
            state_anchors[sk] = {"sleep_center": center, "jitter": jitter}

    # 预计算每天的状态（mixed 模式按比例随机分配 good/bad）
    total_days = (end_dt - start_dt).days + 1
    if state_mode == "good":
        day_states = ["good"] * total_days
    elif state_mode == "bad":
        day_states = ["bad"] * total_days
    else:
        # mixed：每 7 天为一个窗口，按 good_ratio 决定坏天数，但每周坏天数不超过 3 天，
        # 并在窗口内随机打散，避免连续堆积。
        day_states = []
        i = 0
        while i < total_days:
            week_size = min(7, total_days - i)
            expected_bad = round((1 - good_ratio) * week_size)
            bad_count = min(3, max(0, expected_bad))
            week_states = ["bad"] * bad_count + ["good"] * (week_size - bad_count)
            random.shuffle(week_states)
            day_states.extend(week_states)
            i += week_size

    code = persona.get("code") or "M-L-C"
    for i, cur in enumerate(_date_range(start_dt, end_dt)):
        state_key = day_states[i]
        state_cfg = states[state_key]
        rec = generate_day_record(
            cur, uid, state_cfg, now_utc,
            state_label=state_key,
            schedule_anchor=state_anchors.get(state_key),
            persona_code=code,
        )
        records.append(rec)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"[{name}] 已生成 {len(records)} 条记录 → {out_file}")
    return out_file


def _date_range(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="根据 health_data_personas_config.json 生成睡眠健康数据"
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help="指定 user_id（不填则生成所有人格）",
    )
    parser.add_argument(
        "--state",
        choices=["good", "bad", "mixed"],
        default="mixed",
        help="数据状态：good（全好）/ bad（全差）/ mixed（混合，默认）",
    )
    parser.add_argument(
        "--good-ratio",
        type=float,
        default=0.5,
        help="mixed 模式下好状态占比（0.0~1.0，默认 0.5）",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="覆盖起始日期（YYYY-MM-DD）",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="覆盖结束日期（YYYY-MM-DD）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="强制覆盖已存在的输出文件",
    )
    parser.add_argument(
        "--config",
        default=CONFIG_PATH,
        help=f"配置文件路径（默认 {CONFIG_PATH}）",
    )
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    personas = config["personas"]
    if args.user_id:
        personas = [p for p in personas if p["user_id"] == args.user_id]
        if not personas:
            print(f"未找到 user_id={args.user_id} 的人格配置")
            sys.exit(1)

    start_date_override = (
        datetime.strptime(args.start_date, "%Y-%m-%d").date()
        if args.start_date else None
    )
    end_date_override = (
        datetime.strptime(args.end_date, "%Y-%m-%d").date()
        if args.end_date else None
    )

    for persona in personas:
        generate_persona_health_data(
            persona=persona,
            state_mode=args.state,
            good_ratio=args.good_ratio,
            start_date_override=start_date_override,
            end_date_override=end_date_override,
            overwrite=args.overwrite,
        )

    print("\n全部完成。")


if __name__ == "__main__":
    main()
