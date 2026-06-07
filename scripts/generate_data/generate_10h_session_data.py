"""生成一段 10 小时睡眠会话的体征数据与环境数据（每 5 秒一个采样点）。

复用 main.py 体征/环境生成中的核心原语（generate_health_data 里的昼夜节律偏移、
睡前过渡偏移、环境温/湿/光/噪采样器），但不依赖具体 health 记录，而是按下列
5 个阶段合成睡眠分期：

  睡前(玩手机) 65min  → 清醒，心率偏高、波动大、体动多、光偏亮
  放松(闭眼)   10min  → 清醒缓降，副交感激活、体动减少、光调暗
  入睡         15min  → 浅睡，心率/呼吸继续下降
  守护         480min → 睡眠周期（约 90min/周期，浅/深/REM 交替；前段深睡多、后段 REM 多）
  唤醒         30min  → 由睡眠逐步转清醒，心率/呼吸回升、体动增多、光渐亮

合计 600min = 10h，按 5s 间隔共 7200 个采样点。

输出（全部为整数；ts 为 epoch 毫秒）：
  output/session_10h_vitals.json
    {"ts":..,"heart_rate":..,"respiration_rate":..,"body_motion":bool,"presence":bool,"heart_rate_random":..}
  output/session_10h_environment.json
    {"ts":..,"temperature":..,"humidity":..,"illuminance":..,"noise":..}
  output/session_10h_viewer.html  （Chart.js 折线图，分阶段着色，直观查看曲线）

用法：
  python scripts/generate_data/generate_10h_session_data.py
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from datetime import date, datetime, time as dtime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_ROOT)

import generate_health_data as gh  # noqa: E402
from utils import atomic_write_json  # noqa: E402

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")

INTERVAL_SEC = 5
SAMPLES_PER_MIN = 60 // INTERVAL_SEC  # 12
SESSION_START_CLOCK = dtime(23, 0)  # 环境/节律按 23:00 起的真实夜晚塑形

# 阶段（分钟）：合计 600min = 10h；差额已并入「睡前」
PRESLEEP_MIN = 65
RELAX_MIN = 10
ONSET_MIN = 15
GUARDIAN_MIN = 480
WAKE_MIN = 30
TOTAL_MIN = PRESLEEP_MIN + RELAX_MIN + ONSET_MIN + GUARDIAN_MIN + WAKE_MIN
TOTAL_SAMPLES = TOTAL_MIN * SAMPLES_PER_MIN

# 阶段区间（起始分钟, 时长分钟），供 frac 与图表着色使用
PHASE_RANGES = [
    ("presleep", 0, PRESLEEP_MIN),
    ("relax", PRESLEEP_MIN, RELAX_MIN),
    ("onset", PRESLEEP_MIN + RELAX_MIN, ONSET_MIN),
    ("guardian", PRESLEEP_MIN + RELAX_MIN + ONSET_MIN, GUARDIAN_MIN),
    ("wake", PRESLEEP_MIN + RELAX_MIN + ONSET_MIN + GUARDIAN_MIN, WAKE_MIN),
]
_PHASE_LOOKUP = {name: (start, length) for name, start, length in PHASE_RANGES}

# 分期对心率/呼吸的加性调节。
# 亚健康睡眠：深睡/浅睡的下降量减弱（夜间心率、呼吸降不下来，睡得不沉）
HR_STAGE_ADJ = {"deep": -12, "light": -5, "rem": 8, "awake": 18}
RR_STAGE_ADJ = {"deep": -1.0, "light": -0.2, "rem": 2.2, "awake": 2.6}

# 合成对象的基线与边界（_bounds_from_raw 同一公式）。
# 亚健康睡眠：静息心率/呼吸偏高（健康睡眠静息 HR 约 55-65、RR 约 13-16）
AVG_HR, AVG_RR = 74, 18
HR_LO, HR_HI = max(48, AVG_HR - 22), min(118, AVG_HR + 22)  # 52, 96
RR_LO, RR_HI = max(10, AVG_RR - 4), min(26, AVG_RR + 5)      # 14, 23
BASE_HR = max(50, min(105, AVG_HR))
BASE_RR = max(11, min(24, AVG_RR))

# heart_rate_random：未平滑瞬时原始读数的合理范围
HR_RANDOM_LO, HR_RANDOM_HI = 45, 110

# 守护阶段翻身/微觉醒事件次数范围（亚健康：夜间微觉醒增多）
TURNOVER_MIN_COUNT, TURNOVER_MAX_COUNT = 16, 26

# 睡着（入睡阶段起点）对应的采样索引
ONSET_SAMPLE_IDX = (PRESLEEP_MIN + RELAX_MIN) * SAMPLES_PER_MIN

# 一次性噪音事件：睡着后 1 小时内，noise 升到该 dB 区间的短促尖峰
NOISE_EVENT_WINDOW_MIN = 60
NOISE_EVENT_PEAK_DB = (70, 85)

# 噩梦应激事件：睡着后 3 小时内（偏置到 2.5h 之后，越靠后越好）
NIGHTMARE_WINDOW_MIN = 180
NIGHTMARE_LATE_FROM_MIN = 150
NIGHTMARE_HR_AMP = (26.0, 40.0)
NIGHTMARE_RR_AMP = (5.0, 9.0)
HR_EVENT_MAX = 130
RR_EVENT_MAX = 30


def _clamp(lo: float, hi: float, v: float) -> float:
    return max(lo, min(hi, v))


def _guardian_minute_stages(total_min: int) -> list[str]:
    """按 ~90min 睡眠周期生成守护阶段逐分钟分期：前段深睡多、后段 REM 多。"""
    stages: list[str] = []
    cycle_no = 0
    while len(stages) < total_min:
        deep = max(8, 45 - 10 * cycle_no)
        rem = min(35, 5 + 8 * cycle_no)
        light_total = max(10, 90 - deep - rem)
        light_a = light_total // 2
        light_b = light_total - light_a
        stages += ["light"] * light_a + ["deep"] * deep + ["light"] * light_b + ["rem"] * rem
        cycle_no += 1
    return stages[:total_min]


def _minute_plan() -> list[tuple[str, str]]:
    """逐分钟 (phase, stage)，长度 = TOTAL_MIN。"""
    plan: list[tuple[str, str]] = []
    plan += [("presleep", "awake")] * PRESLEEP_MIN
    plan += [("relax", "awake")] * RELAX_MIN
    plan += [("onset", "light")] * ONSET_MIN
    plan += [("guardian", s) for s in _guardian_minute_stages(GUARDIAN_MIN)]
    wake_light = min(10, WAKE_MIN)
    plan += [("wake", "light")] * wake_light
    plan += [("wake", "awake")] * (WAKE_MIN - wake_light)
    return plan


def _sample_plan() -> list[tuple[str, str, float]]:
    """逐采样点 (phase, stage, frac_in_phase)，长度 = TOTAL_SAMPLES。"""
    minutes = _minute_plan()
    out: list[tuple[str, str, float]] = []
    for i in range(TOTAL_SAMPLES):
        m_float = i / float(SAMPLES_PER_MIN)
        phase, stage = minutes[int(m_float)]
        start, length = _PHASE_LOOKUP[phase]
        frac = _clamp(0.0, 1.0, (m_float - start) / length)
        out.append((phase, stage, frac))
    return out


def _stage_noise(stage: str) -> tuple[int, float]:
    """分期相关的瞬时噪声。亚健康睡眠：呼吸基础变异加大、深睡也不够规律。"""
    hr_noise = random.randint(-2, 2)
    rr_noise = random.uniform(-1.3, 1.3)
    if stage == "rem":
        hr_noise += int(round(random.uniform(-8, 10)))
        rr_noise += random.uniform(-3.2, 3.8)
    elif stage == "deep":
        hr_noise = int(round(random.uniform(-1.5, 1.5)))
        rr_noise *= 0.7
    elif stage == "light":
        hr_noise = int(round(random.uniform(-2, 2)))
    elif stage == "awake":
        hr_noise += int(round(random.uniform(-3, 5)))
    return hr_noise, rr_noise


def _ema(prev: float | None, target: float, alpha: float, max_delta: float,
         lo: float, hi: float) -> float:
    """目标值 + EMA 平滑 + 每步限幅，保证 5s 间隔下相邻点连续无跳变。"""
    if prev is None:
        return _clamp(lo, hi, float(target))
    blended = prev + alpha * (float(target) - prev)
    delta = _clamp(-max_delta, max_delta, blended - prev)
    return _clamp(lo, hi, prev + delta)


def _motion_prob(phase: str, stage: str, frac: float) -> float:
    if phase == "presleep":
        return 0.65 - 0.30 * frac
    if phase == "relax":
        return 0.20
    if phase == "onset":
        return 0.08
    if phase == "guardian":
        return {"deep": 0.012, "light": 0.06, "rem": 0.045}.get(stage, 0.05)
    return 0.10 + 0.45 * frac  # wake


def _hr_random(row_hr: float) -> int:
    return int(round(_clamp(HR_RANDOM_LO, HR_RANDOM_HI, row_hr + random.uniform(-20, 8))))


def _turnover_events(n: int) -> tuple[list[float], set[int]]:
    """守护阶段偶发翻身：短暂的体动 + 心率小幅抬升。"""
    hr_boost = [0.0] * n
    force: set[int] = set()
    g0 = (PRESLEEP_MIN + RELAX_MIN + ONSET_MIN) * SAMPLES_PER_MIN
    g1 = g0 + GUARDIAN_MIN * SAMPLES_PER_MIN
    for _ in range(random.randint(TURNOVER_MIN_COUNT, TURNOVER_MAX_COUNT)):
        c = random.randint(g0, g1 - 6)
        dur = random.randint(2, 5)
        amp = random.uniform(6.0, 13.0)
        for j in range(c, min(g1, c + dur)):
            force.add(j)
            hr_boost[j] += amp
        for j in range(c + dur, min(g1, c + dur + 8)):
            hr_boost[j] += amp * max(0.0, 1.0 - (j - (c + dur)) / 8.0)
    return hr_boost, force


def _nightmare_event(n: int) -> tuple[list[float], list[float], set[int], int]:
    """睡着后 3 小时内（偏置较晚）一次噩梦应激：心率/呼吸『上升→维持→缓降』叠加包络。"""
    hr_ev = [0.0] * n
    rr_ev = [0.0] * n
    motion: set[int] = set()
    lo = ONSET_SAMPLE_IDX + NIGHTMARE_LATE_FROM_MIN * SAMPLES_PER_MIN
    hi = ONSET_SAMPLE_IDX + (NIGHTMARE_WINDOW_MIN - 2) * SAMPLES_PER_MIN
    lo, hi = min(lo, n - 60), min(hi, n - 40)
    if hi <= lo:
        return hr_ev, rr_ev, motion, -1
    c = random.randint(lo, hi)
    rise, hold, decay = random.randint(6, 12), random.randint(4, 8), random.randint(18, 30)
    amp_hr = random.uniform(*NIGHTMARE_HR_AMP)
    amp_rr = random.uniform(*NIGHTMARE_RR_AMP)
    st, pk2, ed = c - rise, c + hold, c + hold + decay
    for i in range(st, ed + 1):
        if i < 0 or i >= n:
            continue
        if i < c:
            f = (i - st) / max(1, rise)
        elif i <= pk2:
            f = 1.0
        else:
            f = 1.0 - (i - pk2) / max(1, decay)
        f = _clamp(0.0, 1.0, f)
        hr_ev[i] = amp_hr * f
        rr_ev[i] = amp_rr * f
        if f > 0.6:
            motion.add(i)
    return hr_ev, rr_ev, motion, c


def _noise_event(noise: list[int]) -> dict:
    """睡着后 1 小时内一次一次性噪音：noise 升到峰值 dB 的短促尖峰（带上升/回落 taper）。"""
    n = len(noise)
    lo = ONSET_SAMPLE_IDX + SAMPLES_PER_MIN
    hi = min(n - 1, ONSET_SAMPLE_IDX + NOISE_EVENT_WINDOW_MIN * SAMPLES_PER_MIN)
    if hi <= lo:
        return {"center_idx": -1}
    c = random.randint(lo, hi)
    peak = random.randint(*NOISE_EVENT_PEAK_DB)
    half = random.randint(3, 7)
    for j in range(c - half, c + half + 1):
        if 0 <= j < n:
            att = max(0.0, 1.0 - abs(j - c) / (half + 1))
            bump = (peak - noise[j]) * att
            if bump > 0:
                noise[j] = int(round(noise[j] + bump))
    return {"center_idx": c, "peak_db": peak}


def _compute_vitals(samples, local_dts, bed, onset, wake, ts_list) -> tuple[list[dict], dict]:
    n = len(samples)
    hr_boost, force_motion = _turnover_events(n)
    hr_ev, rr_ev, nm_motion, nm_idx = _nightmare_event(n)
    rows: list[dict] = []
    prev_hr: float | None = None
    prev_rr: float | None = None
    prev_stage: str | None = None
    for i, (phase, stage, frac) in enumerate(samples):
        loc = local_dts[i]
        t_rel = gh._circadian_progress_t_rel(loc, onset, wake, bed)
        circ = gh._circadian_vitals_offsets(t_rel)
        pre = gh._presleep_transition_offsets(loc, bed, onset)
        hr_noise, rr_noise = _stage_noise(stage)
        target_hr = _clamp(
            HR_LO, HR_HI,
            BASE_HR + HR_STAGE_ADJ.get(stage, -6) + hr_noise + circ["hr"] + pre["hr"] + hr_boost[i],
        )
        target_rr = _clamp(
            RR_LO, RR_HI,
            round(BASE_RR + RR_STAGE_ADJ.get(stage, -0.25) + rr_noise + circ["rr"] + pre["rr"]),
        )
        row_hr = _ema(prev_hr, target_hr, 0.22 if stage == prev_stage else 0.14, 3.0, HR_LO, HR_HI)
        row_rr = _ema(prev_rr, target_rr, 0.28 if stage == prev_stage else 0.20, 1.0, RR_LO, RR_HI)
        prev_hr, prev_rr, prev_stage = row_hr, row_rr, stage
        # 噩梦应激叠加在正常睡眠节律之上，封顶生理上限（不回灌 EMA，事件结束后自然回到节律）
        out_hr = _clamp(HR_LO, HR_EVENT_MAX, row_hr + hr_ev[i])
        out_rr = _clamp(RR_LO, RR_EVENT_MAX, row_rr + rr_ev[i])
        moving = (random.random() < _motion_prob(phase, stage, frac)) or (i in force_motion) or (i in nm_motion)
        rows.append(
            {
                "ts": ts_list[i],
                "heart_rate": int(round(out_hr)),
                "respiration_rate": int(round(out_rr)),
                "body_motion": bool(moving),
                "presence": True,
                "heart_rate_random": _hr_random(out_hr),
            }
        )
    return rows, {"nightmare_idx": nm_idx}


def _illuminance_envelope(samples) -> list[int]:
    """按阶段塑形光照：睡前亮(玩手机)→放松调暗→睡眠近全黑→唤醒渐亮；EMA 平滑后取整。"""
    raw: list[float] = []
    for phase, _stage, frac in samples:
        if phase == "presleep":
            v = 40 + random.uniform(-14, 16)
        elif phase == "relax":
            v = 15 - 11 * frac + random.uniform(-2, 2)
        elif phase == "onset":
            v = random.uniform(0, 3)
        elif phase == "guardian":
            v = random.uniform(0, 2)
        else:  # wake：渐亮（拉窗帘/天亮）
            v = 3 + 52 * frac + random.uniform(-3, 5)
        raw.append(max(0.0, v))
    out: list[int] = []
    prev = raw[0]
    for v in raw:
        prev = prev + 0.25 * (v - prev)
        out.append(int(round(max(0.0, prev))))
    return out


def _smooth_int_series(values: list[int], alpha: float, deadband: float = 0.6) -> list[int]:
    """EMA 低通 + 滞回量化：缓慢跟随趋势，整数仅在跨过阈值时单向阶跃，消除边界来回跳。"""
    if not values:
        return []
    out: list[int] = []
    prev = float(values[0])
    cur = int(round(prev))
    for v in values:
        prev = prev + alpha * (float(v) - prev)
        while prev >= cur + deadband:
            cur += 1
        while prev <= cur - deadband:
            cur -= 1
        out.append(cur)
    return out


def _compute_environment(samples, local_dts, ts_list) -> tuple[list[dict], dict]:
    user_cfg = {
        "personalInformation": {"type": "M-L-C"},
        "sleepTemperature": {"min": [20], "max": [25]},
        "sleepHumidity": {"min": [45], "max": [60]},
        "sleepIlluminance": {"min": [0], "max": [6]},
    }
    seq = gh._sample_environment_metrics_sequence(local_dts, user_cfg, None, set(), awake_windows=None)
    illum = _illuminance_envelope(samples)
    # 低通平滑温/湿度，避免 5s 间隔下整数四舍五入造成的机械抖动
    temp_s = _smooth_int_series([int(t) for t, _h, _l, _n in seq], 0.06)
    hum_s = _smooth_int_series([int(h) for _t, h, _l, _n in seq], 0.06)
    noise_seq = [int(noise) for _t, _h, _l, noise in seq]
    noise_info = _noise_event(noise_seq)
    rows: list[dict] = []
    for i in range(len(seq)):
        rows.append(
            {
                "ts": ts_list[i],
                "temperature": temp_s[i],
                "humidity": hum_s[i],
                "illuminance": int(illum[i]),
                "noise": noise_seq[i],
            }
        )
    return rows, noise_info


def _write_viewer(vitals: list[dict], environment: list[dict], path: str) -> None:
    pre, rel, on = PRESLEEP_MIN, PRESLEEP_MIN + RELAX_MIN, PRESLEEP_MIN + RELAX_MIN + ONSET_MIN
    guard_end = on + GUARDIAN_MIN
    data = {
        "interval_ms": INTERVAL_SEC * 1000,
        "step_min": INTERVAL_SEC / 60.0,
        "heart_rate": [r["heart_rate"] for r in vitals],
        "heart_rate_random": [r["heart_rate_random"] for r in vitals],
        "respiration_rate": [r["respiration_rate"] for r in vitals],
        "body_motion": [1 if r["body_motion"] else 0 for r in vitals],
        "temperature": [r["temperature"] for r in environment],
        "humidity": [r["humidity"] for r in environment],
        "illuminance": [r["illuminance"] for r in environment],
        "noise": [r["noise"] for r in environment],
        "phases": [
            {"name": "睡前(玩手机)", "start": 0, "end": pre, "color": "rgba(255,193,7,0.12)"},
            {"name": "放松(闭眼)", "start": pre, "end": rel, "color": "rgba(156,39,176,0.12)"},
            {"name": "入睡", "start": rel, "end": on, "color": "rgba(3,169,244,0.12)"},
            {"name": "守护", "start": on, "end": guard_end, "color": "rgba(63,81,181,0.10)"},
            {"name": "唤醒", "start": guard_end, "end": TOTAL_MIN, "color": "rgba(255,87,34,0.12)"},
        ],
    }
    html = _VIEWER_TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


_VIEWER_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>10 小时睡眠会话 · 体征与环境曲线</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
  body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; margin: 24px; background:#fafafa; color:#222; }
  h1 { font-size: 20px; margin: 0 0 6px; }
  .sub { color:#666; font-size: 13px; margin-bottom: 12px; }
  .legend { margin: 6px 0 18px; }
  .legend span { display:inline-block; padding:2px 10px; margin:0 8px 6px 0; border-radius:4px; font-size:12px; color:#111; }
  .chart-box { background:#fff; border:1px solid #eee; border-radius:8px; padding:12px 16px 16px; margin-bottom:18px; box-shadow:0 1px 3px rgba(0,0,0,.05); }
  .chart-box h2 { font-size:15px; margin:0 0 8px; }
  .chart-wrap { position:relative; width:100%; height:240px; }
</style>
</head>
<body>
<h1>10 小时睡眠会话 · 体征与环境曲线</h1>
<div class="sub">采样间隔 5 秒，共 <span id="cnt"></span> 个点（10 小时）；横轴为时长（分钟）。</div>
<div class="legend" id="legend"></div>

<div class="chart-box"><h2>心率 heart_rate / heart_rate_random（bpm）</h2><div class="chart-wrap"><canvas id="c_hr"></canvas></div></div>
<div class="chart-box"><h2>呼吸 respiration_rate（次/分）</h2><div class="chart-wrap"><canvas id="c_rr"></canvas></div></div>
<div class="chart-box"><h2>体动 body_motion（0/1）</h2><div class="chart-wrap"><canvas id="c_mot"></canvas></div></div>
<div class="chart-box"><h2>温度 temperature（℃）/ 湿度 humidity（%）</h2><div class="chart-wrap"><canvas id="c_th"></canvas></div></div>
<div class="chart-box"><h2>光照 illuminance（lux）</h2><div class="chart-wrap"><canvas id="c_lux"></canvas></div></div>
<div class="chart-box"><h2>噪声 noise（dB）</h2><div class="chart-wrap"><canvas id="c_noise"></canvas></div></div>

<script>
const D = __DATA__;
const STEP = D.step_min;
document.getElementById('cnt').textContent = D.heart_rate.length;

const phasePlugin = {
  id:'phases',
  beforeDraw(chart){
    const {ctx, chartArea, scales:{x}} = chart;
    if(!x || !chartArea) return;
    D.phases.forEach(ph=>{
      const a=x.getPixelForValue(ph.start), b=x.getPixelForValue(ph.end);
      ctx.save(); ctx.fillStyle=ph.color;
      ctx.fillRect(a, chartArea.top, b-a, chartArea.bottom-chartArea.top); ctx.restore();
    });
  }
};

const legend=document.getElementById('legend');
D.phases.forEach(ph=>{ const s=document.createElement('span'); s.textContent=ph.name+' ('+ph.start+'~'+ph.end+'分)'; s.style.background=ph.color.replace(/0\\.1[02]/,'0.55'); legend.appendChild(s); });

// 抽稀绘制：每 15s 一点（step=3），兼顾流畅与细节
const S=3;
function pts(arr){ const o=[]; for(let i=0;i<arr.length;i+=S){ o.push({x:+(i*STEP).toFixed(2), y:arr[i]}); } return o; }

function makeChart(id, datasets, opts){
  opts=opts||{};
  const scales={ x:{type:'linear', min:0, max:600, title:{display:true, text:'时长（分钟）'}}, y:opts.y||{} };
  if(opts.y1) scales.y1=opts.y1;
  new Chart(document.getElementById(id), {
    type:'line',
    data:{ datasets },
    options:{
      responsive:true, maintainAspectRatio:false, animation:false,
      parsing:false, normalized:true, spanGaps:true,
      scales, elements:{ point:{radius:0}, line:{borderWidth:1.1} },
      plugins:{ legend:{ display:true } }
    },
    plugins:[phasePlugin]
  });
}

makeChart('c_hr', [
  {label:'heart_rate', data:pts(D.heart_rate), borderColor:'#e53935', tension:.2},
  {label:'heart_rate_random', data:pts(D.heart_rate_random), borderColor:'#fb8c00', borderWidth:.6, tension:0}
]);
makeChart('c_rr', [
  {label:'respiration_rate', data:pts(D.respiration_rate), borderColor:'#43a047', tension:.2}
]);
makeChart('c_mot', [
  {label:'body_motion', data:pts(D.body_motion), borderColor:'#6d4c41', stepped:true}
], { y:{min:-0.1, max:1.1, ticks:{stepSize:1}} });
makeChart('c_th', [
  {label:'temperature(℃)', data:pts(D.temperature), borderColor:'#fb8c00', tension:.3},
  {label:'humidity(%)', data:pts(D.humidity), borderColor:'#1e88e5', yAxisID:'y1', tension:.3}
], { y:{position:'left', title:{display:true,text:'℃'}}, y1:{position:'right', title:{display:true,text:'%'}, grid:{drawOnChartArea:false}} });
makeChart('c_lux', [
  {label:'illuminance', data:pts(D.illuminance), borderColor:'#fbc02d', backgroundColor:'rgba(251,192,45,.2)', fill:true, tension:.3}
]);
makeChart('c_noise', [
  {label:'noise', data:pts(D.noise), borderColor:'#8e24aa', tension:.2}
]);
</script>
</body>
</html>
"""


def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    base_dt = datetime.combine(date.today(), SESSION_START_CLOCK)
    local_dts = [base_dt + timedelta(seconds=INTERVAL_SEC * i) for i in range(TOTAL_SAMPLES)]
    now_ms = int(time.time() * 1000)
    ts_list = [now_ms + INTERVAL_SEC * 1000 * i for i in range(TOTAL_SAMPLES)]

    bed = base_dt
    onset = base_dt + timedelta(minutes=PRESLEEP_MIN + RELAX_MIN)
    wake = base_dt + timedelta(minutes=TOTAL_MIN)

    samples = _sample_plan()
    vitals, vit_info = _compute_vitals(samples, local_dts, bed, onset, wake, ts_list)
    environment, env_info = _compute_environment(samples, local_dts, ts_list)

    vitals_path = os.path.join(OUTPUT_DIR, "session_10h_vitals.json")
    env_path = os.path.join(OUTPUT_DIR, "session_10h_environment.json")
    viewer_path = os.path.join(OUTPUT_DIR, "session_10h_viewer.html")
    atomic_write_json(vitals_path, vitals)
    atomic_write_json(env_path, environment)
    _write_viewer(vitals, environment, viewer_path)

    onset_min = PRESLEEP_MIN + RELAX_MIN
    step_min = INTERVAL_SEC / 60.0
    print(f"体征数据 {len(vitals)} 条 → {vitals_path}")
    print(f"环境数据 {len(environment)} 条 → {env_path}")
    print(f"图表页面 → {viewer_path}")
    nz_idx = env_info.get("center_idx", -1)
    if nz_idx >= 0:
        mn = nz_idx * step_min
        print(f"噪音事件：第 {mn:.1f} 分（睡后 {mn - onset_min:.1f} 分），峰值 {env_info['peak_db']} dB")
    nm_idx = vit_info.get("nightmare_idx", -1)
    if nm_idx >= 0:
        mn = nm_idx * step_min
        print(f"噩梦应激：第 {mn:.1f} 分（睡后 {mn - onset_min:.1f} 分），心率/呼吸抬升")


if __name__ == "__main__":
    main()
