"""根据 health_data_personas_config.json 与 output 下 health/env/vitals 生成睡眠事件。

对每条 health 记录调用 generate_sleep_events，并传入：
  - personality_type = 人格 code（如 M-H-R）
  - sleep_time_cfg_override：来自该日 data_label 对应 sleep_metric_states 的 sleep_time_range
  - generation_options：合并后的 generation（environment / vitals / event_triggers）

不依赖 config.json 中 user_profiles 的 sleepTime；仍会读 output 下体征与环境 JSON（与 generate_sleep_events 一致）。

用法：
  python scripts/generate_data/generate_sleep_events_by_persona.py
  python scripts/generate_data/generate_sleep_events_by_persona.py --user-id 69aea6e3af5e6cbf08027967 --overwrite
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_ROOT)

import generate_health_data as gh
from persona_generation_config import merge_generation
from utils import atomic_write_json

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "health_data_personas_config.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")


def _sleep_time_cfg_from_persona(persona: dict, data_label: str | None) -> dict:
    states = persona.get("sleep_metric_states") or {}
    key = (data_label or "good").lower()
    if key not in states:
        key = "good" if "good" in states else next(iter(states))
    st = states.get(key) or {}
    rng = st.get("sleep_time_range")
    if not rng or len(rng) < 2:
        return {"min": ["23:00"], "max": ["23:59"]}
    return {"min": [str(rng[0])], "max": [str(rng[1])]}


def _generation_options_payload(gen: dict, persona: dict) -> dict:
    return {
        "environment": gen.get("environment") or {},
        "vitals": gen.get("vitals") or {},
        "event_triggers": gen.get("event_triggers") or {},
        "event_probabilities": persona.get("sleep_event_probabilities") or {},
    }


SLEEP_EVENT_LANGUAGE = "zh"
# 与 generate_health_data.eligible_sleeping_windows 一致：严格大于该值优先视为入睡困难
SLEEP_ONSET_DIFFICULTY_LATENCY_MIN = 30
# 编码第 2 维 H/L = 活跃度（见 docs/reference/睡眠人格编码说明.md）
ONSET_ACTIVITY_HIGH_MIN_NIGHTS = 15
ONSET_ACTIVITY_LOW_MIN_NIGHTS = 7
ONSET_ACTIVITY_LOW_MAX_NIGHTS = 10


def _ensure_event_language(event: dict) -> None:
    event["language"] = SLEEP_EVENT_LANGUAGE


def _time_to_minutes(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _minutes_to_hhmm(mins: int) -> str:
    v = mins % 1440
    return f"{v // 60:02d}:{v % 60:02d}"


# 异常事件 — idf_data 阶段约束（见 docs/睡眠事件与idf_data阶段约束.md）
_ABNORMAL_EVENT_STAGE_ALLOWED = {
    "入睡困难": {"awake"},  # 仅首段清醒，见 _pick_minute_in_first_onset_awake
    "噩梦应激": {"rem"},
    "心率上升": {"rem"},
    "异常体动": {"light", "deep", "rem"},
    "噪声事件": {"light", "rem"},
    "家电持续声": {"light", "deep", "rem"},
    "环境持续声": {"light", "deep", "rem"},
    "邻里持续声": {"light", "deep", "rem"},
    "自然持续声": {"light", "deep", "rem"},
    "突发撞击声": {"light", "rem"},
    "突发交通声": {"light", "rem"},
    "人声/门铃声": {"light", "rem"},
    "自然突发声": {"light", "rem"},
    "物品突发声": {"light", "rem"},
}

# 正常事件补位用（本文件仅调整异常事件时勿改）
_EVENT_STAGE_ALLOWED = {
    **_ABNORMAL_EVENT_STAGE_ALLOWED,
    "打鼾": {"light", "deep", "rem"},
    "梦话": {"light", "rem"},
    "AI主动干预": {"light", "awake"},
}


def _idf_seg_minute_window(seg: dict) -> tuple[int, int] | None:
    sm = _time_to_minutes(str(seg.get("start") or seg.get("start_time") or ""))
    em = _time_to_minutes(str(seg.get("end") or seg.get("end_time") or ""))
    if sm is None or em is None:
        return None
    if em < sm:
        em += 1440
    return sm, em


def _pick_minute_in_first_onset_awake(idf_data: list, rng: random.Random) -> int | None:
    """入睡困难：仅 idf_data[0] 且 stage=awake，锚点偏向首段前 35% 或前 8 分钟。"""
    if not idf_data or (idf_data[0] or {}).get("stage") != "awake":
        return None
    win = _idf_seg_minute_window(idf_data[0])
    if not win:
        return None
    sm, em = win
    span = max(1, em - sm)
    early_limit = sm + min(int(span * 0.35), 8, span - 1)
    pick_hi = max(sm, early_limit)
    return rng.randint(sm, pick_hi) % 1440


def _pick_minute_in_stage_windows(
    idf_data: list,
    allowed_stages: set[str],
    rng: random.Random,
    base_min: int | None = None,
    search_range_min: int = 320,
    segments: list | None = None,
) -> int | None:
    """从 idf_data（或指定 segments）中找一个落在 allowed_stages 内的随机分钟。"""
    segs = segments if segments is not None else idf_data
    if not segs:
        return None
    windows: list[tuple[int, int]] = []
    for seg in segs:
        stage = (seg or {}).get("stage")
        if stage not in allowed_stages:
            continue
        win = _idf_seg_minute_window(seg)
        if win:
            windows.append(win)
    if not windows:
        return None
    if base_min is not None:
        near = []
        for sm, em in windows:
            dist = min(abs(base_min - sm), abs(base_min - em))
            if dist <= search_range_min:
                near.append((sm, em))
        if near:
            windows = near
    sm, em = rng.choice(windows)
    return rng.randint(sm, em) % 1440


def _utc_iso_to_local_hm(utc_iso_z: str, tz_offset_hours: int = 8) -> str | None:
    try:
        utc_dt = datetime.strptime(utc_iso_z, "%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None
    local_dt = utc_dt + timedelta(hours=tz_offset_hours)
    return local_dt.strftime("%H:%M")


def _build_rows_by_date(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if not isinstance(r, dict):
            continue
        rd = r.get("record_date")
        if rd:
            out[str(rd)].append(r)
    return out


def _snoring_level_text(db: int) -> str:
    return "中度" if db >= 40 else "轻度"


def _attach_snoring_payload(event: dict, env_day: list[dict], rng: random.Random) -> None:
    if str(event.get("event_type") or "") != "打鼾":
        return
    # 先取事件附近环境噪声作为关联基线，再约束到 40~60
    hm = str(event.get("event_timestamp") or "")
    m = _time_to_minutes(hm)
    base = None
    if m is not None:
        local_noise = []
        for row in env_day:
            z = row.get("collected_at")
            if not z:
                continue
            try:
                local_dt = datetime.strptime(z, "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=8)
            except Exception:
                continue
            mm = local_dt.hour * 60 + local_dt.minute
            if abs((mm - m + 720) % 1440 - 720) <= 2:
                nz = row.get("noise")
                if nz is not None:
                    local_noise.append(float(nz))
        if local_noise:
            base = int(round(sum(local_noise) / len(local_noise)))
    if base is None:
        base = rng.randint(28, 46)

    snore_db = max(40, min(60, base + rng.randint(0, 10)))
    lv = _snoring_level_text(snore_db)
    detail = dict(event.get("detail") or {})
    detail["snoring_value_db"] = snore_db
    detail["snoring_level"] = lv
    detail["result_summary"] = f"判定为{lv}打鼾（{snore_db}dB）"
    event["detail"] = detail


def _default_duration_sec(
    event_type: str,
    code: str,
    rng: random.Random,
    duration_cfg: dict | None = None,
) -> int:
    if duration_cfg:
        rng_val = duration_cfg.get(code)
        if rng_val and len(rng_val) >= 2:
            return rng.randint(int(rng_val[0]), int(rng_val[1]))
    if event_type == "打鼾" or code == "snoring":
        return rng.randint(7, 8)
    if event_type in {"噩梦应激", "异常体动"} or code in {"nightmare", "movement", "abnormal_movement"}:
        return rng.randint(30, 150)
    return rng.randint(5, 45)


def _abnormal_prob_map(persona: dict) -> dict[str, float]:
    probs = (persona.get("sleep_event_probabilities") or {}).get("abnormal") or {}
    out: dict[str, float] = {}
    for k, v in probs.items():
        if isinstance(v, (int, float)):
            out[str(k)] = float(v)
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                if isinstance(v2, (int, float)):
                    out[str(k2)] = float(v2)
    return out


def _normal_prob_map(persona: dict) -> dict[str, float]:
    probs = (persona.get("sleep_event_probabilities") or {}).get("normal") or {}
    out: dict[str, float] = {}
    for k, v in probs.items():
        if isinstance(v, (int, float)):
            out[str(k)] = float(v)
    return out


def _normal_code_map() -> dict[str, str]:
    return {
        "打鼾": "snoring",
        "梦话": "sleep_talking",
        "咳嗽": "cough",
        "睡眠姿势切换": "posture_switch",
        "肢体动作": "body_movement",
        "自然微动": "micro_movement",
        "单次体动": "single_movement",
        "呼吸声": "breathing",
        "吞咽": "swallowing",
    }


def _resolve_high_prob_event(hp_entry: dict, rng: random.Random) -> tuple[str, str]:
    """将 high_probability_events 条目解析为 (event_type, code)。
    噪声类随机选取一个子类型。"""
    label = hp_entry.get("label", "")
    category = hp_entry.get("category")
    if category == "持续性噪声":
        sub_types = ["家电持续声", "环境持续声", "邻里持续声", "自然持续声"]
        codes = ["appliance_continuous", "environment_continuous",
                 "neighbor_continuous", "nature_continuous"]
        idx = rng.randint(0, len(sub_types) - 1)
        return sub_types[idx], codes[idx]
    elif category == "一次性噪声":
        sub_types = ["突发撞击声", "突发交通声", "人声/门铃声", "自然突发声", "物品突发声"]
        codes = ["sudden_impact", "sudden_traffic", "voice_doorbell",
                 "nature_sudden", "object_sudden"]
        idx = rng.randint(0, len(sub_types) - 1)
        return sub_types[idx], codes[idx]
    else:
        label_to_code = {
            "入睡困难": "sleeping",
            "噩梦应激": "nightmare",
            "心率上升": "heart_rate_increase",
            "异常体动": "movement",
        }
        return label, label_to_code.get(label, "")


def _should_high_prob_occur(probability: float, rng: random.Random) -> bool:
    """正态分布采样决定高概率事件是否发生。mean=probability, std=0.05。"""
    sampled = rng.gauss(probability, 0.05)
    return rng.random() < max(0.0, min(1.0, sampled))


def _make_abnormal_event(
    uid: str, record_date: str, event_hhmm: str, event_type: str, code: str,
) -> dict:
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    detail_map = {
        "入睡困难": ("检测到入睡困难情况", "触发睡眠状态分析", "判定为入睡干扰问题"),
        "噩梦应激": ("检测到噩梦相关生理反应", "触发情绪状态分析", "判定为情绪干扰影响"),
        "心率上升": ("检测到心率异常上升", "触发心脏状态分析", "判定为心率干扰影响"),
        "异常体动": ("检测到异常体动模式", "触发体动模式分析", "判定为体动干扰问题"),
        "家电持续声": ("检测到家电持续噪声", "触发噪音监测分析", "判定为持续性环境声干扰"),
        "环境持续声": ("检测到环境持续噪声", "触发噪音监测分析", "判定为持续性环境声干扰"),
        "邻里持续声": ("检测到邻里持续噪声", "触发噪音监测分析", "判定为持续性环境声干扰"),
        "自然持续声": ("检测到自然持续噪声", "触发噪音监测分析", "判定为持续性环境声干扰"),
        "突发撞击声": ("检测到突发撞击声", "触发噪音监测分析", "判定为突发性环境声干扰"),
        "突发交通声": ("检测到突发交通声", "触发噪音监测分析", "判定为突发性环境声干扰"),
        "人声/门铃声": ("检测到人声或门铃声", "触发噪音监测分析", "判定为突发性环境声干扰"),
        "自然突发声": ("检测到自然突发声", "触发噪音监测分析", "判定为突发性环境声干扰"),
        "物品突发声": ("检测到物品突发声", "触发噪音监测分析", "判定为突发性环境声干扰"),
    }
    tc, ac, rs = detail_map.get(
        event_type, ("检测到异常事件", "触发事件分析", "判定为异常事件"),
    )
    return {
        "uid": uid, "record_date": record_date,
        "event_timestamp": event_hhmm, "event_type": event_type,
        "type": "abnormal", "code": code,
        "detail": {"trigger_cause": tc, "action_taken": ac, "result_summary": rs},
        "related_event_id": "", "sort_order": 0,
        "create_time": ts, "update_time": ts,
    }


def _snoring_config_for_persona(persona: dict, gen: dict) -> dict:
    """按晨/夜型读取打鼾配置。"""
    sc = gen.get("snoring_config") or {}
    code = persona.get("code", "M-L-C")
    is_evening = code.startswith("E")
    base = sc.get("evening" if is_evening else "morning") or {}
    return {
        "occurrence_ratio": float(base.get("occurrence_ratio", 0.35 if is_evening else 0.25)),
        "segment_count_range": base.get("segment_count_range", [2, 4] if is_evening else [1, 2]),
        "total_minutes_range": base.get("total_minutes_range", [40, 55] if is_evening else [30, 40]),
        "event_duration_sec": sc.get("event_duration_sec", [7, 8]),
    }


def _generate_snoring_segment_events(
    uid: str,
    record_date: str,
    segment_start_min: int,
    segment_duration_sec: int,
    event_duration_range: list,
    rng: random.Random,
) -> list[dict]:
    """在一段连续时间内生成紧密相邻的打鼾事件，noise_db 呈低→高→低曲线。"""
    events = []
    seg_dur_lo = int(event_duration_range[0])
    seg_dur_hi = int(event_duration_range[1])
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    # 按分钟粒度生成：每分钟生成多条事件，同一分钟内 noise_db 相同
    total_min = max(1, segment_duration_sec // 60)
    base_db = 38
    amplitude = 22

    current_sec = 0
    for min_offset in range(total_min):
        # noise_db 抛物线：按分钟位置计算，低→高→低
        if total_min > 1:
            pos = min_offset / (total_min - 1)  # 0.0 ~ 1.0
        else:
            pos = 0.5
        noise_db = int(round(base_db + amplitude * (1 - (2 * pos - 1) ** 2)))
        noise_db += rng.randint(-2, 2)  # 小幅随机抖动
        noise_db = max(35, min(62, noise_db))

        event_min = (segment_start_min + min_offset) % 1440
        hhmm = _minutes_to_hhmm(event_min)

        # 该分钟内的事件数：60秒 / 单次时长
        sec_in_min = 60 if min_offset < total_min - 1 else max(1, segment_duration_sec - current_sec)
        n_events = max(1, sec_in_min // ((seg_dur_lo + seg_dur_hi) // 2))

        for _ in range(n_events):
            dur = rng.randint(seg_dur_lo, seg_dur_hi)
            events.append({
                "uid": uid,
                "record_date": record_date,
                "event_timestamp": hhmm,
                "event_type": "打鼾",
                "type": "normal",
                "code": "snoring",
                "detail": {
                    "trigger_cause": "检测到用户打鼾",
                    "action_taken": "触发身体指标分析",
                    "result_summary": "判定为唤醒干扰",
                },
                "related_event_id": "",
                "sort_order": 0,
                "create_time": ts,
                "update_time": ts,
                "duration_sec": dur,
                "noise_db": noise_db,
            })
            current_sec += dur

    return events


def _resp_high_5min(vitals_day: list[dict], threshold: int = 15) -> bool:
    if not vitals_day:
        return False
    rows: list[tuple[datetime, float]] = []
    for r in vitals_day:
        rr = ((r.get("metrics") or {}).get("respiration_rate"))
        z = r.get("collected_at")
        if not z:
            continue
        try:
            dt = datetime.strptime(str(z), "%Y-%m-%dT%H:%M:%SZ")
            val = float(rr)
        except Exception:
            continue
        rows.append((dt, val))
    if not rows:
        return False
    rows.sort(key=lambda x: x[0])
    diffs = [
        int((rows[i][0] - rows[i - 1][0]).total_seconds())
        for i in range(1, len(rows))
        if (rows[i][0] - rows[i - 1][0]).total_seconds() > 0
    ]
    interval_sec = int(round(sum(diffs) / len(diffs))) if diffs else 60
    interval_sec = max(1, min(3600, interval_sec))

    streak_sec = 0
    for _dt, val in rows:
        if val > threshold:
            streak_sec += interval_sec
            if streak_sec >= 5 * 60:
                return True
        else:
            streak_sec = 0
    return False


def _activity_level_from_code(code: str) -> str:
    """编码第 2 维：H=高活跃，L=低活跃。"""
    parts = (code or "").split("-")
    return parts[1] if len(parts) > 1 else "L"


def _compute_onset_difficulty_dates(
    uid: str,
    persona: dict,
    health_rows: list[dict],
) -> set[str]:
    """按活跃度 H/L 从 sleep_latency>30 的夜里抽样：H≥15 天，L 为 7–10 天。"""
    blocked = set(
        (persona.get("sleep_event_probabilities") or {}).get("blocked_events") or []
    )
    if "入睡困难" in blocked:
        return set()

    is_high = _activity_level_from_code(persona.get("code") or "") == "H"

    dated: list[tuple[str, int]] = []
    for rec in health_rows:
        if not isinstance(rec, dict) or not rec.get("record_date"):
            continue
        raw = rec.get("raw_data") or {}
        dated.append((str(rec["record_date"]), int(raw.get("sleep_latency") or 0)))

    eligible = [
        d for d, lat in dated if lat > SLEEP_ONSET_DIFFICULTY_LATENCY_MIN
    ]
    if not eligible:
        return set()

    if is_high:
        target = min(len(eligible), ONSET_ACTIVITY_HIGH_MIN_NIGHTS)
    else:
        rng_target = random.Random(
            int(hashlib.md5(f"{uid}:onset-l-count".encode()).hexdigest(), 16)
        )
        lo = min(ONSET_ACTIVITY_LOW_MIN_NIGHTS, len(eligible))
        hi = min(ONSET_ACTIVITY_LOW_MAX_NIGHTS, len(eligible))
        target = rng_target.randint(lo, hi) if hi >= lo else lo

    rng = random.Random(
        int(hashlib.md5(f"{uid}:onset-schedule".encode()).hexdigest(), 16)
    )
    pool = list(eligible)
    rng.shuffle(pool)
    return set(pool[:target])


def _pick_onset_difficulty_minute(sleep_rec: dict, rng: random.Random) -> int:
    """入睡困难锚点：优先 idf_data[0] 的 awake 段内。"""
    idf_data = sleep_rec.get("idf_data") or []
    stage_min = _pick_minute_in_first_onset_awake(idf_data, rng)
    if stage_min is not None:
        return stage_min
    if idf_data:
        seg = idf_data[0]
        sm = _time_to_minutes(str(seg.get("start") or seg.get("start_time") or ""))
        em = _time_to_minutes(str(seg.get("end") or seg.get("end_time") or ""))
        if sm is not None and em is not None:
            if em < sm:
                em += 1440
            return rng.randint(sm, max(sm, em)) % 1440
    raw = (sleep_rec.get("raw_data") or {})
    sleep_t = str(raw.get("sleep_time") or "")
    if "T" in sleep_t:
        hm_local = _utc_iso_to_local_hm(sleep_t, tz_offset_hours=8)
        if hm_local:
            m = _time_to_minutes(hm_local)
            if m is not None:
                return (m - rng.randint(5, 15)) % 1440
    return 30


def _make_sleeping_event(uid: str, record_date: str, event_hhmm: str) -> dict:
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return {
        "uid": uid,
        "record_date": record_date,
        "event_timestamp": event_hhmm,
        "event_type": "入睡困难",
        "type": "abnormal",
        "code": "sleeping",
        "detail": {
            "trigger_cause": "检测到入睡困难情况",
            "action_taken": "触发睡眠初始化阶段评估",
            "result_summary": "判定为入睡阶段异常",
        },
        "related_event_id": "",
        "sort_order": 0,
        "create_time": ts,
        "update_time": ts,
    }


def _make_normal_event(
    uid: str,
    record_date: str,
    event_hhmm: str,
    event_type: str,
    code: str,
) -> dict:
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    detail_map = {
        "打鼾": ("检测到用户打鼾", "触发身体指标分析", "判定为唤醒干扰"),
        "梦话": ("检测到用户梦话", "触发语音特征分析", "判定为睡眠语音现象"),
        "咳嗽": ("检测到用户咳嗽", "触发呼吸干扰分析", "判定为呼吸道轻微干扰"),
        "自然微动": ("检测到自然微动", "触发体动平稳性分析", "判定为正常睡眠微动"),
        "睡眠姿势切换": ("检测到睡眠姿势切换", "触发体位变化分析", "判定为正常体位调整"),
        "肢体动作": ("检测到肢体动作", "触发体动模式分析", "判定为正常干扰现象"),
        "单次体动": ("检测到单次体动", "触发体动突发性分析", "判定为单次体动事件"),
        "呼吸声": ("检测到呼吸声", "触发呼吸节律分析", "判定为正常干扰现象"),
        "吞咽": ("检测到吞咽动作", "触发生理微动作分析", "判定为正常生理现象"),
    }
    tc, ac, rs = detail_map.get(
        event_type, ("检测到正常睡眠事件", "触发事件分析", "判定为正常干扰现象")
    )
    return {
        "uid": uid,
        "record_date": record_date,
        "event_timestamp": event_hhmm,
        "event_type": event_type,
        "type": "normal",
        "code": code,
        "detail": {
            "trigger_cause": tc,
            "action_taken": ac,
            "result_summary": rs,
        },
        "related_event_id": "",
        "sort_order": 0,
        "create_time": ts,
        "update_time": ts,
    }


def _rebalance_one_night_events(
    uid: str,
    record_date: str,
    sleep_rec: dict,
    events: list[dict],
    persona: dict,
    vitals_day: list[dict],
    env_day: list[dict],
    duration_cfg: dict | None = None,
    gen: dict | None = None,
    onset_schedule_dates: set[str] | None = None,
) -> list[dict]:
    rng = random.Random(f"{uid}:{record_date}:sleep-events")
    raw = (sleep_rec.get("raw_data") or {})
    abnormal_probs = _abnormal_prob_map(persona)
    normal_probs = _normal_prob_map(persona)
    hp_events_cfg = (persona.get("sleep_event_probabilities") or {}).get("high_probability_events") or []

    # 构建 force_allowed 集合：高概率事件的噪声类别绕过 blocked 过滤
    force_allowed: set[str] = set()
    for hp in hp_events_cfg:
        category = hp.get("category")
        if category == "持续性噪声":
            force_allowed.update(["家电持续声", "环境持续声", "邻里持续声", "自然持续声"])
        elif category == "一次性噪声":
            force_allowed.update(["突发撞击声", "突发交通声", "人声/门铃声", "自然突发声", "物品突发声"])
        else:
            force_allowed.add(hp.get("label", ""))

    # 按人格阻止列表过滤事件（低敏感人格不应有入睡困难、噩梦、噪声事件）
    blocked = set(
        (persona.get("sleep_event_probabilities") or {}).get("blocked_events") or []
    )
    if blocked:
        events = [
            e for e in events
            if (str(e.get("event_type") or "") not in blocked
                and str(e.get("code") or "") not in blocked)
            or str(e.get("event_type") or "") in force_allowed
        ]

    # 保险丝：异常总数/每类上限/最小间隔
    max_abnormal = 3
    max_per_code = 1
    min_gap_min = 20

    # 先按配置概率做“夜间存在”门控（仅 abnormal 主事件）
    has_type = defaultdict(list)
    for idx, e in enumerate(events):
        if e.get("type") == "abnormal":
            has_type[str(e.get("event_type") or "")].append(idx)

    drop_indices = set()
    for et, idxs in has_type.items():
        p = abnormal_probs.get(et)
        if p is None:
            continue
        if rng.random() > p:
            drop_indices.update(idxs)

    kept = [e for i, e in enumerate(events) if i not in drop_indices]

    # --- 高概率事件注入（force_allowed 绕过 blocked 过滤） ---
    for hp in hp_events_cfg:
        hp_label = hp.get("label", "")
        # 入睡困难由 onset_schedule_dates + 活跃度调度，跳过高概率路径
        if hp_label == "入睡困难":
            continue
        prob = float(hp.get("probability", 0.9))
        if not _should_high_prob_occur(prob, rng):
            continue
        et_name, et_code = _resolve_high_prob_event(hp, rng)
        if not et_name or not et_code:
            continue
        # 噪声类高概率事件绕过 blocked 过滤
        if et_name in blocked and et_name not in force_allowed:
            continue
        already = any(
            str(e.get("event_type")) == et_name and e.get("type") == "abnormal"
            for e in kept
        )
        if already:
            continue
        idf_data = sleep_rec.get("idf_data") or []
        allowed = _ABNORMAL_EVENT_STAGE_ALLOWED.get(et_name, {"light", "rem"})
        stage_min = _pick_minute_in_stage_windows(idf_data, allowed, rng)
        if stage_min is None:
            sleep_t = str(raw.get("sleep_time") or "")
            stage_min = 30
            if "T" in sleep_t:
                hm_local = _utc_iso_to_local_hm(sleep_t, tz_offset_hours=8)
                if hm_local:
                    m = _time_to_minutes(hm_local)
                    if m is not None:
                        stage_min = (m + rng.randint(30, 180)) % 1440
        kept.append(_make_abnormal_event(uid, record_date, _minutes_to_hhmm(stage_min), et_name, et_code))

    # --- 异常事件零比例控制（确定性） ---
    code = persona.get("code", "M-L-C")
    is_evening = code.startswith("E")
    zero_threshold = 0.3 if is_evening else 0.4
    zero_hash = int(hashlib.md5(f"{uid}:{record_date}:zero-abnormal".encode()).hexdigest(), 16)
    zero_hash_val = (zero_hash % 10000) / 10000.0
    if zero_hash_val < zero_threshold:
        kept = [e for e in kept if e.get("type") != "abnormal"]

    # 保险丝应用到 abnormal 主事件
    abnormal_primary = []
    normal_primary = []
    others = []
    for e in kept:
        if e.get("type") == "abnormal":
            abnormal_primary.append(e)
        elif e.get("type") == "normal":
            normal_primary.append(e)
        else:
            others.append(e)

    abnormal_primary.sort(
        key=lambda e: (_time_to_minutes(str(e.get("event_timestamp") or "")) or 9999)
    )
    selected = []
    code_count: dict[str, int] = defaultdict(int)
    last_min = None
    for e in abnormal_primary:
        if len(selected) >= max_abnormal:
            continue
        code = str(e.get("code") or "")
        if code_count[code] >= max_per_code:
            continue
        t = _time_to_minutes(str(e.get("event_timestamp") or ""))
        if t is None:
            continue
        if last_min is not None:
            gap = (t - last_min) % 1440
            if gap < min_gap_min:
                continue
        selected.append(e)
        code_count[code] += 1
        last_min = t

    # normal：夜级概率门控 + 最小下限补位 + 保险丝
    normal_by_type = defaultdict(list)
    for e in normal_primary:
        normal_by_type[str(e.get("event_type") or "")].append(e)
    normal_code_map = _normal_code_map()
    planned_types = set()
    for et, p in normal_probs.items():
        if et in blocked:
            continue
        if et == "打鼾":  # 打鼾单独处理
            continue
        if rng.random() < float(p):
            planned_types.add(et)

    # 先按计划挑选已有事件（每类最多 1）
    normal_kept = []
    for et in planned_types:
        cand = normal_by_type.get(et) or []
        if cand:
            cand.sort(
                key=lambda x: (_time_to_minutes(str(x.get("event_timestamp") or "")) or 9999)
            )
            normal_kept.append(cand[0])

    # 对计划中但缺失的类型补位，避免高概率 normal 长期为 0
    existing_normal_types = {str(e.get("event_type") or "") for e in normal_kept}
    sleep_t = str(raw.get("sleep_time") or "")
    base_min = 30
    if "T" in sleep_t:
        hm_local = _utc_iso_to_local_hm(sleep_t, tz_offset_hours=8)
        if hm_local:
            m = _time_to_minutes(hm_local)
            if m is not None:
                base_min = m
    idf_data = sleep_rec.get("idf_data") or []
    for et in sorted(planned_types, key=lambda x: -float(normal_probs.get(x, 0.0))):
        if et in existing_normal_types:
            continue
        code = normal_code_map.get(et)
        if not code:
            continue
        # 阶段感知放置：根据事件类型选择合适的睡眠阶段
        allowed = _EVENT_STAGE_ALLOWED.get(et, {"light", "rem", "deep"})
        t = _pick_minute_in_stage_windows(idf_data, allowed, rng, base_min=base_min)
        if t is None:
            t = (base_min + rng.randint(30, 320)) % 1440
        normal_kept.append(
            _make_normal_event(uid, record_date, _minutes_to_hhmm(t), et, code)
        )

    max_normal = 8
    max_normal_per_code = 1
    normal_min_gap = 8
    # 优先保留配置概率更高的 normal 类型
    normal_kept.sort(
        key=lambda e: (
            -float(normal_probs.get(str(e.get("event_type") or ""), 0.0)),
            (_time_to_minutes(str(e.get("event_timestamp") or "")) or 9999),
        )
    )
    normal_selected = []
    normal_code_count: dict[str, int] = defaultdict(int)
    last_nm = None
    for e in normal_kept:
        if len(normal_selected) >= max_normal:
            continue
        code = str(e.get("code") or "")
        et = str(e.get("event_type") or "")
        if normal_code_count[code] >= max_normal_per_code:
            continue
        t = _time_to_minutes(str(e.get("event_timestamp") or ""))
        if t is None:
            continue
        if last_nm is not None and ((t - last_nm) % 1440) < normal_min_gap:
            continue
        normal_selected.append(e)
        normal_code_count[code] += 1
        last_nm = t

    # --- 打鼾多段连续事件生成（独立于 normal 保险丝） ---
    # 与睡眠报告一致：仅当呼吸暂停次数 ≥5 时生成打鼾事件
    _APNEA_SNORING_THRESHOLD = 5
    snoring_cfg = _snoring_config_for_persona(persona, gen or {})
    snoring_events = []
    if "打鼾" not in blocked:
        apnea_count = int(raw.get("apnea_count", 0) or 0)
        if apnea_count >= _APNEA_SNORING_THRESHOLD:
            n_segments = rng.randint(
                snoring_cfg["segment_count_range"][0],
                snoring_cfg["segment_count_range"][1],
            )
            total_minutes = rng.randint(
                snoring_cfg["total_minutes_range"][0],
                snoring_cfg["total_minutes_range"][1],
            )
            total_sec = total_minutes * 60

            sleep_t = str(raw.get("sleep_time") or "")
            wake_t = str(raw.get("wake_time") or raw.get("wake_up_time") or "")
            bed_min = _time_to_minutes(_utc_iso_to_local_hm(sleep_t, tz_offset_hours=8)) or 0
            wake_min = _time_to_minutes(_utc_iso_to_local_hm(wake_t, tz_offset_hours=8)) or (bed_min + 420)
            if wake_min < bed_min:
                wake_min += 1440
            sleep_window_sec = (wake_min - bed_min) * 60

            # 将总时长分配到各段
            # 每段最小10分钟
            min_seg_sec = 10 * 60
            segment_durations = []
            remaining = total_sec
            for i in range(n_segments):
                if i == n_segments - 1:
                    segment_durations.append(remaining)
                else:
                    # 剩余段数需要至少 min_seg_sec
                    others_min = (n_segments - i - 1) * min_seg_sec
                    max_share = remaining - others_min
                    min_share = min_seg_sec
                    if max_share < min_share:
                        share = remaining // (n_segments - i)
                    else:
                        share = rng.randint(min_share, max_share)
                    segment_durations.append(share)
                    remaining -= share

            # 在睡眠窗口内不重叠放置各段
            used_ranges: list[tuple[int, int]] = []
            for seg_dur_sec in segment_durations:
                for _ in range(50):
                    max_start_sec = max(0, sleep_window_sec - seg_dur_sec)
                    start_sec = rng.randint(0, max_start_sec) if max_start_sec > 0 else 0
                    start_min = (bed_min + start_sec // 60) % 1440
                    end_sec = start_sec + seg_dur_sec
                    overlap = any(start_sec < ue and end_sec > us for us, ue in used_ranges)
                    if not overlap:
                        used_ranges.append((start_sec, end_sec))
                        break
                seg_events = _generate_snoring_segment_events(
                    uid, record_date, start_min, seg_dur_sec,
                    snoring_cfg["event_duration_sec"], rng,
                )
                snoring_events.extend(seg_events)

    # 移除 normal_selected 中的旧打鼾事件（来自 generate_health_data）
    normal_selected = [e for e in normal_selected if str(e.get("code") or "") != "snoring"]

    valid_ids = {str(e.get("_id") or "") for e in selected if e.get("_id")}
    filtered_others = []
    for e in others:
        if e.get("type") != "intervention":
            filtered_others.append(e)
            continue
        rid = str(e.get("related_event_id") or "")
        if not rid or rid in valid_ids:
            filtered_others.append(e)

    # --- 入睡困难：仅 schedule 内且 sleep_latency>30 的日期，绕过 zero_abnormal 和 fuse ---
    selected = [
        e for e in selected if str(e.get("event_type") or "") != "入睡困难"
    ]
    onset_events: list[dict] = []
    if (
        onset_schedule_dates is not None
        and record_date in onset_schedule_dates
    ):
        stage_min = _pick_onset_difficulty_minute(sleep_rec, rng)
        onset_events.append(
            _make_sleeping_event(uid, record_date, _minutes_to_hhmm(stage_min))
        )

    out = selected + normal_selected + snoring_events + filtered_others + onset_events
    for ev in out:
        detail = dict(ev.get("detail") or {})
        detail.pop("duration_sec", None)
        if str(ev.get("type") or "") == "intervention":
            d0 = rng.randint(10, 45)
        elif str(ev.get("code") or "") == "snoring":
            d0 = ev.get("duration_sec", rng.randint(7, 8))
        else:
            code_str = str(ev.get("code") or "")
            event_type_str = str(ev.get("event_type") or "")
            d0 = _default_duration_sec(event_type_str, code_str, rng, duration_cfg)
        ev["duration_sec"] = max(1, d0)
        ev["detail"] = detail
        if str(ev.get("code") or "") != "snoring":
            _attach_snoring_payload(ev, env_day, rng)

    # 为缺少 _id 的事件补 _id
    for ev in out:
        if not ev.get("_id"):
            ev["_id"] = gh.generate_object_id()

    # 为每个 abnormal 事件补配对的 AI 主动干预（若尚无对应干预）
    existing_related = {
        str(e.get("related_event_id") or "")
        for e in out
        if e.get("event_type") == "AI主动干预"
    }
    ts_now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    new_interventions = []
    for ev in out:
        if ev.get("type") != "abnormal":
            continue
        ev_id = str(ev.get("_id") or "")
        if not ev_id or ev_id in existing_related:
            continue
        code = str(ev.get("code") or "")
        event_name = str(ev.get("event_type") or "")
        parent_min = _time_to_minutes(str(ev.get("event_timestamp") or ""))
        ai_min = ((parent_min + 2) % 1440) if parent_min is not None else None
        ai_ts = _minutes_to_hhmm(ai_min) if ai_min is not None else ev.get("event_timestamp", "")
        ai_event = {
            "uid": uid,
            "record_date": record_date,
            "event_timestamp": ai_ts,
            "event_type": "AI主动干预",
            "type": "intervention",
            "code": code,
            "detail": {
                "trigger_cause": gh._ai_intervention_trigger_cause_for_code(code, event_name),
                "action_taken": gh._fallback_intervention_action_taken(code),
                "result_summary": gh._ai_intervention_result_summary_for_code(code),
            },
            "related_event_id": ev_id,
            "sort_order": 0,
            "create_time": ts_now,
            "update_time": ts_now,
            "duration_sec": rng.randint(10, 45),
            "_id": gh.generate_object_id(),
        }
        _ensure_event_language(ai_event)
        new_interventions.append(ai_event)
    out.extend(new_interventions)
    for ev in out:
        _ensure_event_language(ev)
    return out


def generate_sleep_events_for_persona(
    persona: dict,
    cfg: dict,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    overwrite: bool = False,
) -> str | None:
    uid = persona["user_id"]
    out_path = os.path.join(OUTPUT_DIR, f"{uid}_sleep_events.json")
    partial_range = bool(start_date or end_date)
    if not partial_range and not overwrite and os.path.exists(out_path):
        print(f"[{persona.get('name', uid)}] sleep_events 已存在，跳过（--overwrite 覆盖）")
        return out_path

    existing_kept: list[dict] = []
    if partial_range and os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                old_ev = json.load(f)
            if isinstance(old_ev, list):
                lo = start_date.isoformat() if start_date else "0000-01-01"
                hi = end_date.isoformat() if end_date else "9999-12-31"
                for ev in old_ev:
                    if not isinstance(ev, dict):
                        continue
                    rd = str(ev.get("record_date") or "")
                    if rd < lo or rd > hi:
                        existing_kept.append(ev)
        except (OSError, json.JSONDecodeError):
            existing_kept = []

    health_path = os.path.join(OUTPUT_DIR, f"{uid}_health_data.json")
    if not os.path.exists(health_path):
        print(f"[{uid}] 未找到 {health_path}")
        return None
    env_path = os.path.join(OUTPUT_DIR, f"{uid}_environment_data.json")
    vit_path = os.path.join(OUTPUT_DIR, f"{uid}_vitals_data.json")
    if not os.path.exists(env_path) or not os.path.exists(vit_path):
        print(f"[{uid}] 需要先有 {uid}_environment_data.json 与 {uid}_vitals_data.json")
        return None

    with open(health_path, "r", encoding="utf-8") as f:
        health_rows = json.load(f)
    if not isinstance(health_rows, list):
        print(f"[{uid}] health_data 格式无效")
        return None

    gh._SLEEP_EVENTS_AUX_INDEX_CACHE.pop(uid, None)

    with open(env_path, "r", encoding="utf-8") as f:
        env_rows = json.load(f)
    with open(vit_path, "r", encoding="utf-8") as f:
        vit_rows = json.load(f)
    env_by_date = _build_rows_by_date(env_rows)
    vit_by_date = _build_rows_by_date(vit_rows)

    gen = merge_generation(cfg, persona)
    gopts = _generation_options_payload(gen, persona)
    duration_cfg = gen.get("event_duration_sec") or {}
    code = persona.get("code") or "M-L-C"

    all_events: list[dict] = list(existing_kept)
    health_by_date = {
        str(r.get("record_date")): r
        for r in health_rows
        if isinstance(r, dict) and r.get("record_date")
    }

    scoped_health: list[dict] = []
    for rec in health_rows:
        if not isinstance(rec, dict) or not rec.get("record_date"):
            continue
        rds = str(rec["record_date"])
        if start_date and rds < start_date.isoformat():
            continue
        if end_date and rds > end_date.isoformat():
            continue
        scoped_health.append(rec)

    onset_schedule = _compute_onset_difficulty_dates(uid, persona, scoped_health)

    for rec in health_rows:
        if not isinstance(rec, dict):
            continue
        rd = rec.get("record_date")
        if not rd:
            continue
        rds = str(rd)
        if start_date and rds < start_date.isoformat():
            continue
        if end_date and rds > end_date.isoformat():
            continue

        sleep_cfg = _sleep_time_cfg_from_persona(persona, rec.get("data_label"))

        evs = gh.generate_sleep_events(
            rec,
            uid,
            rds,
            None,
            code,
            None,
            None,
            sleep_time_cfg_override=sleep_cfg,
            generation_options=gopts,
        )
        tuned = _rebalance_one_night_events(
            uid,
            rds,
            rec,
            list(evs or []),
            persona,
            vit_by_date.get(rds, []),
            env_by_date.get(rds, []),
            duration_cfg=duration_cfg,
            gen=gen,
            onset_schedule_dates=onset_schedule,
        )
        all_events.extend(tuned)

    if all_events:

        def _sort_key_evt(ev):
            rd = ev.get("record_date")
            day = health_by_date.get(str(rd)) if rd else None
            st, we = (None, None)
            if day:
                st, we = gh.sleep_local_window_bounds_from_sleep_data(day)
            return (rd, gh.session_anchor_event_local_dt(ev, st, we))

        all_events.sort(key=_sort_key_evt)

    for ev in all_events:
        if isinstance(ev, dict):
            _ensure_event_language(ev)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    atomic_write_json(out_path, all_events)
    print(f"[{persona.get('name', uid)}] 睡眠事件 {len(all_events)} 条 → {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="按人格配置与 health/env/vitals 生成 sleep_events")
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--config", default=CONFIG_PATH)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    personas = cfg.get("personas") or []
    if args.user_id:
        personas = [p for p in personas if p.get("user_id") == args.user_id]
        if not personas:
            print(f"未找到 user_id={args.user_id}")
            sys.exit(1)

    start_date = (
        datetime.strptime(args.start_date, "%Y-%m-%d").date() if args.start_date else None
    )
    end_date = (
        datetime.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else None
    )

    for p in personas:
        generate_sleep_events_for_persona(
            p, cfg, start_date=start_date, end_date=end_date, overwrite=args.overwrite
        )
    print("完成。")


if __name__ == "__main__":
    main()
