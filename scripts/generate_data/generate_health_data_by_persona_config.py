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

from persona_generation_config import load_personas_config, merge_generation

TIMEZONE_OFFSET = 8  # UTC+8
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "health_data_personas_config.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")

# ──────────────────────────────────────────────
# 入睡潜伏期增强（按作息类型 M/E 抽样追加 extra latency）
# ──────────────────────────────────────────────

# 同一 (label) 下的多条规则按顺序对剩余样本不重叠抽取，比例基于该 label 的全体样本数。
EXTRA_SLEEP_LATENCY_RULES: dict = {
    "M": {  # 晨型
        "good": [
            {"ratio": 0, "extra_range": (60, 90)},
            {"ratio": 0, "extra_range": (20, 60)},
        ],
        "bad": [
            {"ratio": 0, "extra_range": (90, 120)},
        ],
    },
    "E": {  # 夜型
        "good": [
            {"ratio": 0, "extra_range": (90, 120)},
            {"ratio": 0, "extra_range": (40, 80)},
        ],
        "bad": [
            {"ratio": 0, "extra_range": (120, 180)},
        ],
    },
}

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


def _compute_wake_up_window(bed_time_local: datetime, wake_up_range: list[str]) -> tuple[datetime, datetime]:
    """推算起床时刻的绝对 datetime 窗口：以卧床时刻 +8h 为锚点找对应日历日。"""
    approx_wake_day = (bed_time_local + timedelta(hours=8)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    w0, w1 = _window_start_end(approx_wake_day, wake_up_range[0], wake_up_range[1])
    if w1 < bed_time_local + timedelta(hours=4):
        w0 += timedelta(days=1)
        w1 += timedelta(days=1)
    return w0, w1


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


def _redistribute_short_sleep_segments(idf_data: list, min_dur: int = 10) -> list:
    """将 < min_dur 分钟的非 awake 碎片阶段的分钟数补给同阶段的其他段，
    并平移中间段的时间点，保持时间线连续且各阶段总分钟数不变。"""

    def _parse(time_str: str) -> int:
        h, m = time_str.split(":")
        return int(h) * 60 + int(m)

    def _fmt(minutes: int) -> str:
        minutes = minutes % 1440
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    changed = True
    while changed:
        changed = False
        for i, seg in enumerate(idf_data):
            if seg["stage"] == "awake":
                continue
            dur = _parse(seg["end"]) - _parse(seg["start"])
            if dur < 0:
                dur += 1440
            if dur >= min_dur:
                continue

            # 只找前面的同阶段段（目标在后面会导致时间线断裂）
            target = None
            for j in range(i - 1, -1, -1):
                if idf_data[j]["stage"] == seg["stage"]:
                    target = j
                    break
            if target is None:
                continue

            delta = dur
            idf_data[target]["end"] = _fmt((_parse(idf_data[target]["end"]) + delta) % 1440)

            # 平移目标和短片段之间的所有段
            for k in range(target + 1, i):
                idf_data[k]["start"] = _fmt((_parse(idf_data[k]["start"]) + delta) % 1440)
                idf_data[k]["end"] = _fmt((_parse(idf_data[k]["end"]) + delta) % 1440)

            idf_data.pop(i)
            changed = True
            break

    return idf_data


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
    idf = _timeline_to_idf(timeline, bed_time_local)
    return _redistribute_short_sleep_segments(idf)


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

def _generate_apnea_count(cfg_range: list) -> int:
    """生成呼吸暂停次数：绝大多数记录在 0~4 之间，仅极少数使用配置原范围。"""
    lo, hi = int(cfg_range[0]), int(cfg_range[1])
    lo, hi = min(lo, hi), max(lo, hi)
    # 85% 的概率限制在 [0, 4]，15% 使用配置原范围
    if random.random() < 0.85:
        cap_hi = min(4, hi)
        return random.randint(min(lo, cap_hi), cap_hi)
    return random.randint(lo, hi)


def _randint_range(rng: list) -> int:
    lo, hi = int(rng[0]), int(rng[1])
    return random.randint(min(lo, hi), max(lo, hi))


def _feasible_awake_ratio_interval(
    awake_range: list,
    tib_need_lo: int,
    tib_need_hi: int,
    sleep_latency: int,
    wake_after_sleep: int,
    tst_cfg_lo: int,
    tst_cfg_hi: int,
) -> tuple[int, int] | None:
    """在已知 TIB 允许区间与潜伏/觉后清醒时，筛出能使 tst 合法采样的 awake_ratio 闭区间子集。

    避免「总睡下限偏高 + 清醒比例上沿偏高 + TIB 上沿有限」导致 tst_lo_final > tst_hi_final。
    """
    a_lo, a_hi = int(awake_range[0]), int(awake_range[1])
    a_lo, a_hi = min(a_lo, a_hi), max(a_lo, a_hi)
    feas: list[int] = []
    for a in range(a_lo, a_hi + 1):
        eff = max(1, 100 - a)
        tst_hi_final = min(
            tst_cfg_hi,
            tib_need_hi - sleep_latency - wake_after_sleep,
            round(tib_need_hi * eff / 100),
        )
        tst_lo_final = max(
            tst_cfg_lo,
            min(
                tib_need_lo - sleep_latency - wake_after_sleep,
                round(tib_need_lo * eff / 100),
            ),
        )
        if tst_lo_final <= tst_hi_final:
            feas.append(a)
    if not feas:
        return None
    return (min(feas), max(feas))


def generate_day_record(record_date: date, uid: str, state_cfg: dict,
                        now_utc: datetime, state_label: str = "good",
                        schedule_anchor: dict | None = None,
                        persona_code: str = "M-L-C") -> dict:
    """生成某一天的健康数据记录。

    schedule_anchor 由调用方预先计算：{"sleep_center": int(分钟), "jitter": int(分钟)}
    当存在时，入睡时间在 center ± jitter 内随机，而非从配置全范围采样。
    """
    for _ in range(100):
        rec = _try_generate_day_record_impl(
            record_date, uid, state_cfg, now_utc, state_label, schedule_anchor,
            persona_code,
        )
        if rec is not None:
            return rec
    raise RuntimeError(
        f"配置参数无解：uid={uid} record_date={record_date} state={state_label}。"
        "请检查 bed/sleep/wake_up_time_range 与 total_sleep_minutes/sleep_latency/wake_after_sleep 是否兼容。"
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
    """正向采样：bed → sleep → tib，使 wake_up 直接命中配置窗，无需后置平移。

    关键恒等式：wake_up_time = bed_time + tib
    （latency、stages、夜醒、觉后清醒的分配只影响内部结构，不改变终点钟点）
    因此约束 tib 落入 [ww0-bed, ww1-bed] 即可保证 wake_up ∈ wake_up_time_range。
    返回 None 仅在配置参数组合本身无解时出现（极少），由外层少量重试覆盖。
    """
    base_dt = datetime(record_date.year, record_date.month, record_date.day)

    # ── 入睡时刻（含锚点逻辑）──
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

    # ── 入睡潜伏：使 bed_time 落在 bed_time_range 内 ──
    sleep_latency = _pick_sleep_latency_for_bed_range(
        sleep_time_local, state_cfg["sleep_latency"], state_cfg["bed_time_range"]
    )
    bed_time_local = sleep_time_local - timedelta(minutes=sleep_latency)
    wake_after_sleep = _randint_range(state_cfg["wake_after_sleep"])

    # wake_up_time = bed_time + tib，故 tib ∈ [ww0 - bed, ww1 - bed]
    ww0, ww1 = _compute_wake_up_window(bed_time_local, state_cfg["wake_up_time_range"])
    tib_need_lo = int((ww0 - bed_time_local).total_seconds() // 60)
    tib_need_hi = int((ww1 - bed_time_local).total_seconds() // 60)

    tst_cfg_lo = int(state_cfg["total_sleep_minutes"][0])
    tst_cfg_hi = int(state_cfg["total_sleep_minutes"][1])
    aw_feas = _feasible_awake_ratio_interval(
        state_cfg["awake_ratio"],
        tib_need_lo,
        tib_need_hi,
        sleep_latency,
        wake_after_sleep,
        tst_cfg_lo,
        tst_cfg_hi,
    )
    if aw_feas is None:
        return None

    # ── 四段比例采样（各自在范围内且和为 100）；awake 先按 TIB×总睡约束收紧 ──
    deep_ratio, light_ratio, rem_ratio, awake_ratio = _sample_ratios_in_range(
        state_cfg["deep_sleep_ratio"],
        state_cfg["light_sleep_ratio"],
        state_cfg["rem_ratio"],
        list(aw_feas),
    )

    # ── 约束 tst_seed 使最终 tib 落入允许区间 ──
    # tib = max(tst + lat + wake_after, round(tst / eff * 100))
    # 上界：两条路径都必须 <= tib_need_hi
    # 下界：取两条路径下界的最小值（只需一条满足即可）
    eff = max(1, 100 - int(awake_ratio))

    tst_hi_final = min(
        tst_cfg_hi,
        tib_need_hi - sleep_latency - wake_after_sleep,
        round(tib_need_hi * eff / 100),
    )
    tst_lo_final = max(
        tst_cfg_lo,
        min(
            tib_need_lo - sleep_latency - wake_after_sleep,
            round(tib_need_lo * eff / 100),
        ),
    )
    if tst_lo_final > tst_hi_final:
        return None

    tst_seed = random.randint(tst_lo_final, tst_hi_final)
    sleep_efficiency_seed = eff
    tib = max(
        tst_seed + sleep_latency + wake_after_sleep,
        round(tst_seed / max(1, sleep_efficiency_seed) * 100),
    )

    # 微调：tib 因 round() 偶尔越上界时，通过缩减 tst_seed 修正
    if tib > tib_need_hi:
        excess = tib - tib_need_hi
        if tst_seed - excess >= tst_cfg_lo:
            tst_seed -= excess
            tib = tib_need_hi
        else:
            return None

    # ── 各阶段分钟数依据 TIB 计算 ──
    deep_min = round(tib * deep_ratio / 100)
    light_min = round(tib * light_ratio / 100)
    rem_min = round(tib * rem_ratio / 100)
    total_sleep_min = deep_min + light_min + rem_min

    # round() 累积误差修正（调浅睡）
    if total_sleep_min < tst_cfg_lo:
        light_min += tst_cfg_lo - total_sleep_min
        total_sleep_min = tst_cfg_lo
    elif total_sleep_min > tst_cfg_hi:
        light_min -= total_sleep_min - tst_cfg_hi
        light_min = max(0, light_min)
        total_sleep_min = deep_min + light_min + rem_min

    # ── 夜间清醒 ──
    n_awakenings = _randint_range(state_cfg["night_awakenings"])
    nighttime_awake = max(0, tib - total_sleep_min - sleep_latency - wake_after_sleep)
    awakening_durations = _split_awake(nighttime_awake, n_awakenings)

    # ── 时间节点（由 bed_time + tib 直接推算）──
    wake_up_time_local = bed_time_local + timedelta(minutes=tib)
    wake_time_local = wake_up_time_local - timedelta(minutes=wake_after_sleep)

    # ── 输出用四段比例：由实际分钟反推，裁剪到配置区间并凑整为和 100 ──
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

    # ── 其他体征指标 ──
    avg_heartbeat = _randint_range(state_cfg["average_heartbeat"])
    avg_respiration = _randint_range(state_cfg["average_respiration"])
    apnea_count = _generate_apnea_count(state_cfg["apnea_count"])
    turnover_count = _randint_range(state_cfg["turnover_count"])

    # ── UTC 时间 ──
    bed_time_utc = _local_to_utc_iso(bed_time_local)
    sleep_time_utc = _local_to_utc_iso(sleep_time_local)
    wake_time_utc = _local_to_utc_iso(wake_time_local)
    wake_up_time_utc = _local_to_utc_iso(wake_up_time_local)

    sleep_score = compute_sleep_score(sleep_efficiency, deep_ratio, rem_ratio, awake_ratio)

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





def _augment_record_with_extra_sleep_latency(rec: dict, extra_minutes: int) -> None:
    """为单条记录追加 extra_minutes 分钟的入睡潜伏期：
    - sleep_time 推后 take（=min(extra, 可削减的睡眠段长度)）；bed_time / wake_time / wake_up_time 不变
    - idf_data：起始 awake 段延长 take，从睡眠+夜间清醒段尾部等量裁剪 take 分钟
      （idf 总长 = TIB 不变，末尾觉后 awake 段与 wake_up_time 钟点保持不变）
    - raw_data.total_sleep_minutes / sleep_efficiency 由最终 timeline 重新计算，确保与 idf 严格一致
    - raw_data.sleep_latency 字段保持不变（用户主观感受值，不重新计算）
    """
    raw = rec["raw_data"]
    bed_dt = datetime.strptime(raw["bed_time"], "%Y-%m-%dT%H:%M:%SZ")
    sleep_dt = datetime.strptime(raw["sleep_time"], "%Y-%m-%dT%H:%M:%SZ")
    wake_up_dt = datetime.strptime(raw["wake_up_time"], "%Y-%m-%dT%H:%M:%SZ")
    tib = max(1, int((wake_up_dt - bed_dt).total_seconds() // 60))

    idf = rec.get("idf_data") or []
    if not idf:
        return

    timeline: list = []
    for seg in idf:
        dur = (_hhmm_to_minutes(seg["end"]) - _hhmm_to_minutes(seg["start"])) % 1440
        if dur == 0:
            dur = 1440
        timeline.extend([seg["stage"]] * dur)

    insert_at = 0
    while insert_at < len(timeline) and timeline[insert_at] == "awake":
        insert_at += 1
    if insert_at >= len(timeline):
        return

    tail_awake_len = 0
    for stage in reversed(timeline):
        if stage == "awake":
            tail_awake_len += 1
        else:
            break
    sleep_end = len(timeline) - tail_awake_len

    take = min(extra_minutes, sleep_end - insert_at)
    if take <= 0:
        return

    new_timeline = (
        timeline[:insert_at]
        + ["awake"] * take
        + timeline[insert_at:sleep_end - take]
        + timeline[sleep_end:]
    )

    bed_local_dt = bed_dt + timedelta(hours=TIMEZONE_OFFSET)
    rec["idf_data"] = _redistribute_short_sleep_segments(_timeline_to_idf(new_timeline, bed_local_dt))

    new_tst = sum(1 for st in new_timeline if st != "awake")
    last_non_awake_end_idx = max(
        (i for i, st in enumerate(new_timeline) if st != "awake"),
        default=-1,
    ) + 1
    raw["sleep_time"] = (sleep_dt + timedelta(minutes=take)).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw["wake_time"] = (bed_dt + timedelta(minutes=last_non_awake_end_idx)).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw["total_sleep_minutes"] = new_tst
    raw["sleep_efficiency"] = min(100, max(0, round(100 * new_tst / tib)))


def _pick_evenly_spaced_indices(
    positions: list[int], n_target: int, occupied: set[int] | None = None
) -> list[int]:
    """在已按日期序排列的位置数组 positions 中均匀分桶抽取 n_target 个：
    每桶内优先选与"上一个被选位置 / occupied 已占位置"间隔 ≥ 2 的候选，
    支持跨 label 共享 occupied 排除以达到全局"尽量不相邻"。"""
    n = len(positions)
    if n_target <= 0 or n == 0:
        return []
    if n_target >= n:
        return list(positions)
    occ = occupied or set()
    picked: list[int] = []
    last_pos = -10
    for k in range(n_target):
        lo = k * n // n_target
        hi = (k + 1) * n // n_target
        bucket = list(range(lo, hi))
        far = [
            i for i in bucket
            if positions[i] - last_pos >= 2
            and all(abs(positions[i] - p) >= 2 for p in occ)
        ]
        if not far:
            far = [i for i in bucket if positions[i] - last_pos >= 2]
        chosen_idx = random.choice(far if far else bucket)
        picked.append(positions[chosen_idx])
        last_pos = positions[chosen_idx]
    return picked


def _apply_extra_sleep_latency_for_records(records: list, persona_code: str) -> None:
    """按人格作息类型（code 首位 M/E）从 good/bad 子集中不重叠抽取若干档位，
    分别为各档位内的样本追加随机 extra latency。

    抽样策略：
    - 同一 label 的多个规则共用一份分桶选出的"被增强"位置集合，再随机切片分配给各规则
    - 跨 label 之间共享 occupied 集合：先处理目标量小的 label（如 bad），
      再处理 label（如 good）时避免新选位置与已占位置在日期上相邻
    """
    persona_type = (persona_code or "")[:1]
    rules_by_label = EXTRA_SLEEP_LATENCY_RULES.get(persona_type)
    if not rules_by_label:
        return

    label_plans: list[tuple[list[dict], list[int], list[int], int]] = []
    for label, rule_list in rules_by_label.items():
        sub_indices = [i for i, r in enumerate(records) if r.get("data_label") == label]
        n_total = len(sub_indices)
        if n_total == 0:
            continue
        targets_per_rule = [int(round(n_total * rule["ratio"])) for rule in rule_list]
        n_target_total = min(sum(targets_per_rule), n_total)
        if n_target_total <= 0:
            continue
        label_plans.append((rule_list, targets_per_rule, sub_indices, n_target_total))

    label_plans.sort(key=lambda x: x[3])

    occupied: set[int] = set()
    for rule_list, targets_per_rule, sub_indices, n_target_total in label_plans:
        picked = _pick_evenly_spaced_indices(sub_indices, n_target_total, occupied)
        occupied.update(picked)
        random.shuffle(picked)
        cursor = 0
        for rule, n_t in zip(rule_list, targets_per_rule):
            n_t = min(n_t, len(picked) - cursor)
            if n_t <= 0:
                continue
            lo, hi = rule["extra_range"]
            for idx in picked[cursor:cursor + n_t]:
                _augment_record_with_extra_sleep_latency(records[idx], random.randint(lo, hi))
            cursor += n_t


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
    config_start = datetime.strptime(start_str, "%Y-%m-%d").date()
    config_end = datetime.strptime(end_str, "%Y-%m-%d").date()
    from date_range_helpers import apply_date_range_overrides  # noqa: WPS433

    merged = apply_date_range_overrides(
        config_start, config_end, start_date_override, end_date_override
    )
    if not merged:
        print(f"[{name}] 日期范围无效，跳过")
        return ""
    start_dt, end_dt = merged

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
        # mixed：每 7 天为一个窗口，按 good_ratio 决定坏天数，并在窗口内随机打散。
        gen = merge_generation(load_personas_config(), persona)
        mix = gen.get("day_state_mix") or {}
        max_bad_per_week = int(mix.get("max_bad_days_per_week", 3))
        day_states = []
        i = 0
        while i < total_days:
            week_size = min(7, total_days - i)
            expected_bad = round((1 - good_ratio) * week_size)
            bad_count = min(max_bad_per_week, max(0, expected_bad))
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

    _apply_extra_sleep_latency_for_records(records, code)

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
