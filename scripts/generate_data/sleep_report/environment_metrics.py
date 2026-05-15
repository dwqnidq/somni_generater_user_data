"""环境噪声采样与再平衡。"""

from __future__ import annotations

import math
import os
import random
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from personality_profile import get_noise_range
from .shared import _parse_utc_iso_to_local_dt


def _environment_centers_from_local_clock(dt_local):
    """
    《固定时段环境变化.md》本地时钟分段：返回 (温度中心℃, 湿度中心%, 光照中心lx, 噪声中心dB)。
    采样点仍须落在入睡～起床窗内；仅按该时刻墙钟映射到对应时段特征。
    """
    h = dt_local.hour + dt_local.minute / 60.0 + dt_local.second / 3600.0
    # 21:00–23:00 入睡准备
    if 21.0 <= h < 23.0:
        return (24.2, 48.0, 180.0, 52.0)
    # 23:00–02:00 入睡与深睡建立
    if h >= 23.0 or h < 2.0:
        return (20.0, 54.0, 2.5, 36.0)
    # 02:00–05:00 夜间稳定
    if 2.0 <= h < 5.0:
        return (18.6, 60.0, 0.5, 26.0)
    # 05:00–07:00 觉醒启动
    if 5.0 <= h < 7.0:
        return (19.5, 54.0, 55.0, 42.0)
    # 07:00–09:00 起床过渡（室内透帘晨光，实测约 100–400 lx）
    if 7.0 <= h < 9.0:
        return (22.5, 46.0, 250.0, 62.0)
    # 其余白天时段（若偶发落入窗内）
    return (23.5, 50.0, 200.0, 45.0)


def _idf_awake_windows_minutes(idf_data):
    """
    从 idf_data 中提取所有 awake 阶段的 (start_min, end_min) 列表（分钟数，从0点起，支持跨午夜）。
    """
    windows = []
    for seg in (idf_data or []):
        if seg.get("stage") != "awake":
            continue
        try:
            sh, sm = map(int, seg["start"].split(":"))
            eh, em = map(int, seg["end"].split(":"))
            windows.append((sh * 60 + sm, eh * 60 + em))
        except Exception:
            pass
    return windows


def _raw_awake_windows_minutes(raw_data):
    """
    从 raw_data 提取卧床清醒与觉后清醒窗口（分钟数，从0点起，支持跨午夜）。
    用于补齐 idf_data 未覆盖的 awake 时段（如 bed_time->sleep_time）。
    """
    out = []
    raw_data = raw_data or {}
    bed_local = _parse_utc_iso_to_local_dt(raw_data.get("bed_time", ""))
    sleep_local = _parse_utc_iso_to_local_dt(raw_data.get("sleep_time", ""))
    wake_local = _parse_utc_iso_to_local_dt(raw_data.get("wake_time", ""))
    wake_up_local = _parse_utc_iso_to_local_dt(raw_data.get("wake_up_time", ""))

    if bed_local and sleep_local and sleep_local > bed_local:
        out.append((bed_local.hour * 60 + bed_local.minute, sleep_local.hour * 60 + sleep_local.minute))
    if wake_local and wake_up_local and wake_up_local > wake_local:
        out.append((wake_local.hour * 60 + wake_local.minute, wake_up_local.hour * 60 + wake_up_local.minute))
    return out


def _is_in_awake_window(dt_local, awake_windows):
    """判断本地时刻是否落在任一清醒阶段窗口内。"""
    m = dt_local.hour * 60 + dt_local.minute
    for s, e in awake_windows:
        if s <= e:
            if s <= m <= e:
                return True
        else:
            if m >= s or m <= e:
                return True
    return False


def _sample_environment_metrics_sequence(
    collected_local_dts,
    user_config,
    onset_env_idx,
    noise_spike_idxs,
    awake_windows=None,
):
    """
    与采样时刻对齐的温度/湿度/光照/噪声：按《固定时段环境变化.md》墙钟分段为基准，
    辅以短时抖动与温湿弱耦合；再沿睡眠窗时间轴施加平滑的「先缓降后缓升」温湿包络（近似昼夜室内变化）；
    入睡困难索引附近升温略降湿；噪声尖峰保留。
    illuminance 规则：睡眠中 0-3 lux，清醒阶段 15% 概率出现 20-30 lux，其余仍为 0-3 lux。
    """
    n = len(collected_local_dts)
    if n <= 0:
        return []

    if user_config:
        tr = user_config.get("sleepTemperature", {"min": [18], "max": [22]})
        hr = user_config.get("sleepHumidity", {"min": [50], "max": [60]})
        ir = user_config.get("sleepIlluminance", {"min": [0], "max": [10]})
        t_lo, t_hi = int(tr["min"][0]), int(tr["max"][0])
        h_lo, h_hi = int(hr["min"][0]), int(hr["max"][0])
        i_lo, i_hi = int(ir["min"][0]), int(ir["max"][0])
        ptype = user_config.get("personalInformation", {}).get("type", "M-L-C")
    else:
        t_lo, t_hi = 18, 26
        h_lo, h_hi = 40, 65
        i_lo, i_hi = 0, 20
        ptype = "M-L-C"

    t_mid = (t_lo + t_hi) / 2.0
    sorted_idx = sorted(range(n), key=lambda i: collected_local_dts[i])

    temps_f = [0.0] * n
    hum_f = [0.0] * n
    illum_out = [0] * n
    noise_f = [0.0] * n

    for k, i in enumerate(sorted_idx):
        dt = collected_local_dts[i]
        tc, hc, lc, nc = _environment_centers_from_local_clock(dt)
        temps_f[i] = tc + random.uniform(-1.1, 1.1)
        hum_f[i] = hc + random.uniform(-2.0, 2.0) - 0.35 * (temps_f[i] - tc)
        noise_f[i] = nc + random.uniform(-2.2, 2.2)
        # illuminance：睡眠中保持极暗（0-3 lux），清醒阶段 15% 概率出现短暂微亮（20-30 lux）
        if awake_windows and _is_in_awake_window(dt, awake_windows) and random.random() < 0.15:
            lux = float(random.randint(20, 30))
        else:
            lux = float(random.randint(0, 3))
        illum_out[i] = int(round(lux))

    for j in range(1, n):
        prev_i = sorted_idx[j - 1]
        cur_i = sorted_idx[j]
        alpha = 0.42
        temps_f[cur_i] = alpha * temps_f[cur_i] + (1 - alpha) * temps_f[prev_i] + random.uniform(-0.35, 0.35)
        hum_f[cur_i] = alpha * hum_f[cur_i] + (1 - alpha) * hum_f[prev_i] - 0.22 * (temps_f[cur_i] - temps_f[prev_i])
        noise_f[cur_i] = 0.55 * noise_f[cur_i] + 0.45 * noise_f[prev_i] + random.uniform(-1.2, 1.2)

    # 沿本地时间先后：前半窗缓慢降温、后半窗缓慢回升（昼夜室内温湿观感）；与墙钟分段叠加
    if n >= 2:
        amp_t = random.uniform(1.1, 2.3)
        amp_t_rise = random.uniform(0.75, 1.55)
        amp_h = random.uniform(2.0, 5.0)
        amp_h_rise = random.uniform(0.8, 2.2)
        for j, i in enumerate(sorted_idx):
            u = j / float(n - 1)
            if u <= 0.5:
                leg = 2.0 * u
                w_down = math.sin((math.pi / 2.0) * leg) ** 2
                temps_f[i] += -amp_t * w_down
                hum_f[i] += amp_h * w_down
            else:
                leg = 2.0 * (u - 0.5)
                w_up = math.sin((math.pi / 2.0) * leg) ** 2
                temps_f[i] += -amp_t * (1.0 - w_up) + amp_t_rise * w_up
                hum_f[i] += amp_h * (1.0 - w_up) - amp_h_rise * w_up

    if onset_env_idx is not None and 0 <= onset_env_idx < n:
        peak = random.uniform(27.5, 31.0)
        for i in range(n):
            dist = abs(i - onset_env_idx)
            blend = max(0.0, 1.0 - min(dist / 3.5, 1.0))
            temps_f[i] = temps_f[i] * (1.0 - blend) + peak * blend
            hum_f[i] -= blend * random.uniform(3.0, 10.0)

    nr = get_noise_range(ptype)
    n_lo, n_hi = int(nr["min"]), int(nr["max"])
    for i in range(n):
        noise_f[i] = max(float(n_lo) - 2.0, min(float(n_hi) + 16.0, noise_f[i]))

    for si in noise_spike_idxs:
        if not (0 <= si < n):
            continue
        peak_n = float(random.randint(64, 84))
        for j in range(n):
            dist = abs(j - si)
            att = max(0.0, 1.0 - min(dist / 2.5, 1.0))
            add = (peak_n - noise_f[j]) * att
            if add > 0:
                noise_f[j] += add * (1.0 if j == si else 0.72)

    out = []
    for i in range(n):
        t_raw = temps_f[i]
        h_raw = hum_f[i]
        lux_raw = float(illum_out[i])
        # 睡眠环境：最大不超过 30 lux
        lux_c = max(0.0, min(30.0, lux_raw))
        t_c = max(float(t_lo) - 0.5, min(float(t_hi) + 4.0, t_raw))
        h_c = max(float(h_lo) - 2.0, min(float(h_hi) + 6.0, h_raw))
        out.append(
            (
                int(round(max(15.0, min(34.0, t_c)))),
                int(round(max(25.0, min(78.0, h_c)))),
                int(round(lux_c)),
                int(round(max(18.0, min(92.0, noise_f[i])))),
            )
        )
    return out


def _build_noise_category_plan(total_days):
    """
    构建按天噪音类别计划（近似配比）：
    - best: 10%  (daily avg < 35)
    - good: 50%  (35-50)
    - noisy: 30% (50-65]
    - overload: 10% (>65)
    """
    if total_days <= 0:
        return []

    ratio_items = [
        ("best", 0.10),
        ("good", 0.50),
        ("noisy", 0.30),
        ("overload", 0.10),
    ]
    raw = [(name, total_days * ratio) for name, ratio in ratio_items]
    base_counts = {name: int(math.floor(v)) for name, v in raw}
    used = sum(base_counts.values())
    remain = total_days - used

    if remain > 0:
        frac_sorted = sorted(raw, key=lambda x: (x[1] - math.floor(x[1])), reverse=True)
        idx = 0
        while remain > 0 and frac_sorted:
            name = frac_sorted[idx % len(frac_sorted)][0]
            base_counts[name] += 1
            idx += 1
            remain -= 1

    plan = []
    for name, _ in ratio_items:
        plan.extend([name] * max(0, int(base_counts.get(name, 0))))
    random.shuffle(plan)
    return plan[:total_days]


def _noise_avg_target_range_for_category(category):
    """返回噪音类别对应的「日均值目标区间」(min_avg, max_avg)。"""
    if category == "best":
        return (20, 34)
    if category == "good":
        return (35, 50)
    if category == "noisy":
        return (51, 65)
    if category == "overload":
        return (66, 82)
    return (35, 50)


def _apply_daily_noise_average_target(metrics_seq, category):
    """
    将当日环境序列的噪音均值拉到目标区间内，保持其他字段不变。
    """
    if not metrics_seq:
        return metrics_seq

    t_min, t_max = _noise_avg_target_range_for_category(category)
    n = len(metrics_seq)
    noises = [float(m[3]) for m in metrics_seq]
    cur_avg = sum(noises) / max(1, n)
    target_avg = random.uniform(float(t_min), float(t_max))
    shift = target_avg - cur_avg

    adjusted = []
    for i, item in enumerate(metrics_seq):
        t, h, lux, noise = item
        new_noise = int(round(max(18.0, min(92.0, float(noise) + shift + random.uniform(-1.0, 1.0)))))
        adjusted.append((t, h, lux, new_noise))

    def _avg(seq):
        return sum(x[3] for x in seq) / max(1, len(seq))

    # 兜底微调：离散取整后若均值越界，逐步回拉到区间边界内
    cur = _avg(adjusted)
    if cur < t_min:
        need = int(math.ceil((t_min - cur) * n))
        i = 0
        while need > 0 and i < n * 6:
            idx = i % n
            t, h, lux, nv = adjusted[idx]
            if nv < 92:
                adjusted[idx] = (t, h, lux, nv + 1)
                need -= 1
            i += 1
    elif cur > t_max:
        need = int(math.ceil((cur - t_max) * n))
        i = 0
        while need > 0 and i < n * 6:
            idx = i % n
            t, h, lux, nv = adjusted[idx]
            if nv > 18:
                adjusted[idx] = (t, h, lux, nv - 1)
                need -= 1
            i += 1
    return adjusted


def _noise_category_from_avg(avg_noise):
    """按日均噪音值推断类别。"""
    try:
        v = float(avg_noise)
    except (TypeError, ValueError):
        return "good"
    if v < 35:
        return "best"
    if v <= 50:
        return "good"
    if v <= 65:
        return "noisy"
    return "overload"


def _rebalance_environment_noise_daily_after_feedback(environment_rows):
    """
    事件回填后，锁定事件命中的噪音点，仅调整非事件点，使日均值尽量回到既定类别区间。
    """
    if not environment_rows:
        return

    by_date = {}
    for row in environment_rows:
        if not isinstance(row, dict):
            continue
        rd = row.get("record_date")
        if not rd:
            continue
        by_date.setdefault(rd, []).append(row)

    for _rd, rows in by_date.items():
        if not rows:
            continue

        categories = [str(r.get("noise_daily_category") or "").strip() for r in rows]
        categories = [c for c in categories if c]
        if categories:
            day_category = max(set(categories), key=categories.count)
        else:
            cur_avg = sum(float(r.get("noise", 0) or 0) for r in rows) / max(1, len(rows))
            day_category = _noise_category_from_avg(cur_avg)

        t_min, t_max = _noise_avg_target_range_for_category(day_category)
        movable = [r for r in rows if r.get("noise_event_affected") is not True]
        if not movable:
            continue

        def _row_noise(row):
            try:
                return int(round(float(row.get("noise", 0) or 0)))
            except (TypeError, ValueError):
                return 35

        total_n = len(rows)
        cur_sum = sum(_row_noise(r) for r in rows)
        cur_avg = cur_sum / max(1, total_n)
        if t_min <= cur_avg <= t_max:
            continue

        target_avg = float(t_min if cur_avg < t_min else t_max)
        need = int(round(target_avg * total_n - cur_sum))
        if need == 0:
            continue

        step = 1 if need > 0 else -1
        remain = abs(need)
        safety = 0
        while remain > 0 and safety < len(movable) * 150:
            idx = safety % len(movable)
            row = movable[idx]
            nv = _row_noise(row)
            if step > 0 and nv < 92:
                row["noise"] = nv + 1
                remain -= 1
            elif step < 0 and nv > 18:
                row["noise"] = nv - 1
                remain -= 1
            safety += 1
