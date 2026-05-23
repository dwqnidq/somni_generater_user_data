"""鼾声/音频数据构建（非 LLM 部分）。"""

from __future__ import annotations

import json
import os
import random
import re
from collections import deque
from datetime import datetime, timedelta

from .shared import (
    _variant_pick,
    _parse_utc_iso_to_local_dt,
    session_anchor_event_local_dt,
    collected_at_to_local_naive_dt,
)

_AUDITORY_SLEEP_EVENT_CODES = frozenset({"snoring", "sleep_talking", "cough_clearing"})
_CODE_TO_AUDIO_TYPE = {"snoring": "Snore", "sleep_talking": "Somniloquy", "cough_clearing": "Cough"}


def build_auditory_snore_module(audios, record_date):
    """
    根据 audios 中 type 为 Snore 的条数生成 auditory.module（单条分析，中文）。
    标题与正文均按日期轮换，不涉及大模型。
    """
    snore_n = sum(1 for a in (audios or []) if (a.get("type") or "") == "Snore")
    key_base = f"snore_mod|{snore_n}"
    if snore_n <= 0:
        opts = [
            (
                "",
                "",
            ),
            (
                "",
                "",
            ),
            (
                "",
                "",
            ),
        ]
    elif snore_n == 1:
        opts = [
            (
                "打鼾片段检出",
                f"检出 1 段打鼾相关录音，提示睡眠中存在可识别的鼾声事件。建议控制睡前饮酒、避免仰卧，并关注是否伴随日间困倦。",
            ),
            (
                "单次鼾声事件",
                f"当晚捕捉到 1 次打鼾片段，强度与持续时间需结合多日趋势判断。可尝试抬高床头、减重与规律运动以减轻振动。",
            ),
        ]
    elif snore_n == 2:
        opts = [
            (
                "鼾声活动小结",
                f"共检出 2 段打鼾录音，夜间上气道可能存在间歇性狭窄。建议留意鼻塞、过敏与睡姿，并观察是否影响深睡连续性。",
            ),
            (
                "双段打鼾记录",
                f"记录到 2 次打鼾事件，提示睡眠中振动声较明显。若合并呼吸暂停风险指标升高，建议就医做进一步评估。",
            ),
        ]
    else:
        opts = [
            (
                "频繁鼾声提示",
                f"共检出 {snore_n} 段打鼾录音，夜间鼾声活动较频繁。建议优先排查鼻塞与仰卧习惯，并关注是否伴有呼吸节律异常。",
            ),
            (
                "多段打鼾分析",
                f"当晚打鼾片段达 {snore_n} 次，提示上气道阻力可能偏高。可结合身体电量与日间嗜睡情况，必要时咨询睡眠专科。",
            ),
            (
                "鼾声密度观察",
                f"打鼾录音较多（{snore_n} 段），睡眠中气道稳定性值得关注。建议保持侧卧、控制体重，并持续对比后续夜晚是否改善。",
            ),
        ]
    pair = _variant_pick(record_date, key_base, opts)
    target, desc = pair
    return [{"target": target, "description": desc}]


def _compact_sleep_events_for_auditory_prompt(events, max_n=80):
    out = []
    for e in (events or [])[: max(0, int(max_n or 0))]:
        if not isinstance(e, dict):
            continue
        d = e.get("detail") if isinstance(e.get("detail"), dict) else {}
        out.append(
            {
                "event_timestamp": e.get("event_timestamp"),
                "event_type": e.get("event_type"),
                "type": e.get("type"),
                "code": e.get("code"),
                "duration_sec": e.get("duration_sec"),
                "trigger_cause": d.get("trigger_cause"),
                "action_taken": d.get("action_taken"),
                "result_summary": d.get("result_summary"),
            }
        )
    return out


def _environment_samples_for_auditory_prompt(user_id, record_date, max_n=120, output_dir="output"):
    path = os.path.join(output_dir, f"{user_id}_environment_data.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    rows = [x for x in data if isinstance(x, dict) and x.get("record_date") == record_date]
    rows.sort(key=lambda x: str(x.get("collected_at") or ""))
    cap = max(1, min(int(max_n or 120), 500))
    slim = []
    for x in rows[:cap]:
        slim.append(
            {
                "collected_at": x.get("collected_at"),
                "temperature": x.get("temperature"),
                "humidity": x.get("humidity"),
                "illuminance": x.get("illuminance"),
                "noise": x.get("noise"),
            }
        )
    return slim


def _sleep_metrics_for_auditory_prompt(sleep_data):
    raw = sleep_data.get("raw_data") or {}
    return {
        "record_date": sleep_data.get("record_date"),
        "apnea_count": raw.get("apnea_count"),
        "average_heartbeat": raw.get("average_heartbeat"),
        "average_respiration": raw.get("average_respiration"),
        "awake_ratio": raw.get("awake_ratio"),
        "deep_sleep_ratio": raw.get("deep_sleep_ratio"),
        "light_sleep_ratio": raw.get("light_sleep_ratio"),
        "rem_ratio": raw.get("rem_ratio"),
        "sleep_score": raw.get("sleep_score"),
        "total_sleep_minutes": raw.get("total_sleep_minutes"),
        "sleep_time": raw.get("sleep_time"),
        "wake_time": raw.get("wake_time"),
        "sleep_latency": raw.get("sleep_latency"),
        "sleep_efficiency": raw.get("sleep_efficiency"),
    }


def sleep_data_shallow_from_report_for_auditory(report):
    """从已落盘的 sleep_report 条目还原听觉分析所需的 raw_data 形状（无原始 health 文件时）。"""
    if not isinstance(report, dict):
        return {"record_date": "", "raw_data": {}}
    ss = report.get("sleep_summary") or {}
    sq = report.get("quality_analysis", {}).get("sleep_quality") or {}
    st = report.get("quality_analysis", {}).get("sleep_structure") or {}

    def _health_ratio_from_structure(block_key, net_key="percent_of_net_sleep"):
        blk = st.get(block_key) or {}
        if block_key == "awake":
            return blk.get("percent_of_time_in_bed", blk.get("percent"))
        return blk.get(net_key, blk.get("percent"))

    raw_like = {
        "apnea_count": int(report.get("apnea_count", 0) or 0),
        "average_heartbeat": ss.get("avg_heart_rate"),
        "average_respiration": ss.get("avg_respiratory_rate"),
        "awake_ratio": _health_ratio_from_structure("awake"),
        "deep_sleep_ratio": _health_ratio_from_structure("deep_sleep"),
        "light_sleep_ratio": _health_ratio_from_structure("light_sleep"),
        "rem_ratio": _health_ratio_from_structure("rem_sleep"),
        "sleep_score": None,
        "total_sleep_minutes": ss.get("total_minutes"),
        "sleep_time": None,
        "wake_time": None,
        "sleep_latency": sq.get("sleep_onset_latency_minutes"),
        "sleep_efficiency": sq.get("sleep_efficiency"),
    }
    return {"record_date": report.get("record_date", ""), "raw_data": raw_like}


def _parse_model_json_array(text):
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    if t.startswith("```"):
        lines = t.splitlines()
        if len(lines) >= 2:
            inner = "\n".join(lines[1:])
            if inner.rstrip().endswith("```"):
                inner = inner.rstrip()[:-3].rstrip()
            t = inner.strip()
            if t.lower().startswith("json"):
                t = t[4:].lstrip().strip()
    try:
        parsed = json.loads(t)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    lb = t.find("[")
    rb = t.rfind("]")
    if lb != -1 and rb != -1 and rb > lb:
        try:
            parsed = json.loads(t[lb : rb + 1])
            if isinstance(parsed, list):
                return parsed
        except Exception:
            return None
    return None


def _normalize_auditory_module_list(items):
    if not isinstance(items, list):
        return None
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        tgt = str(it.get("target") or "").strip()
        desc = str(it.get("description") or "").strip()
        if tgt and desc:
            out.append({"target": tgt, "description": desc})
        elif not tgt and not desc:
            out.append({"target": "", "description": ""})
    return out


def _snoring_data_points_for_auditory_prompt(auditory_dict):
    """为听觉提示词合并鼾声分贝点与对应 audio 的持续时长。"""
    aud = auditory_dict if isinstance(auditory_dict, dict) else {}
    duration_by_time = {}
    for audio in aud.get("audios") or []:
        if not isinstance(audio, dict):
            continue
        time_key = audio.get("time")
        if (audio.get("type") or "") == "Snore" and time_key:
            duration_by_time[str(time_key)] = audio.get("duration_sec")

    out = []
    snoring = aud.get("snoring_analysis") or {}
    for point in snoring.get("data_points") or []:
        if not isinstance(point, dict):
            continue
        time_value = point.get("time")
        item = {
            "time": time_value,
            "value": point.get("value"),
            "duration_sec": duration_by_time.get(str(time_value)),
        }
        out.append(item)
    return out


def build_apnea_auditory_target_title(record_date):
    """呼吸暂停 ≥5 次时 auditory.target 的标题轮换（与 risk_alert 配套）。"""
    return _variant_pick(
        record_date or "",
        "apnea_alert_title",
        [
            "呼吸健康风险提示",
            "夜间呼吸节律关注",
            "睡眠呼吸风险提醒",
            "呼吸相关健康提示",
        ],
    )


def generate_auditory(sleep_data, user_id=None, sleep_events_index=None, output_dir="output"):
    """根据睡眠数据生成听觉报告。

    若有用户与睡眠窗：audios 条数严格等于睡眠窗内「打鼾 / 梦话 / 咳嗽」睡眠事件条数
    （与事件一一对应，由 build_audios_from_auditory_sleep_events 生成）；无窗或无事件列表则为空。
    """
    apnea_count = int(sleep_data["raw_data"].get("apnea_count", 0) or 0)
    rd = sleep_data.get("record_date", "") or ""
    # 仅当呼吸暂停次数 ≥5 时展示呼吸风险提示（标题按日期轮换）
    if apnea_count >= 5:
        target = build_apnea_auditory_target_title(rd)
        risk_alert = f"昨晚出现{apnea_count}次呼吸暂停疑似时间，建议关注。"
    else:
        target = ""
        risk_alert = ""

    audios = []
    sleep_time, window_end = sleep_local_window_bounds_from_sleep_data(sleep_data)
    flat = []
    if sleep_events_index is not None:
        flat = [e for lst in sleep_events_index.values() for e in lst]
    elif user_id:
        sleep_events_file = os.path.join(output_dir, f"{user_id}_sleep_events.json")
        if os.path.exists(sleep_events_file):
            try:
                with open(sleep_events_file, "r", encoding="utf-8") as f:
                    flat = json.load(f)
            except Exception:
                flat = []

    if user_id and sleep_time and window_end:
        sel = collect_auditory_sleep_events_in_window(
            flat, user_id, sleep_time, window_end, rd or None
        )
        audios = build_audios_from_auditory_sleep_events(sel, rd)

    # snoring_analysis.data_points 在 report_audios 步由睡眠事件 noise_db 补全
    return {
        "target": target,
        "risk_alert": risk_alert,
        "snoring_analysis": {"data_points": []},
        "audios": audios,
    }


# audio.json 兼容：audioUrl 可能是一维或二维数组
def _pick_audio_group(audio_groups, rnd=None):
    """
    兼容 audio.json 新旧结构：
    - 旧：audioUrl 为 [ {url,duration_sec}, ... ]
    - 新：audioUrl 为 [ [ {url,duration_sec}, ... ], [ ... ], ... ]
    返回：被选定的"子数组"（list[dict]），并尽量过滤掉无效项。
    """
    if not audio_groups:
        return []

    # 新结构：二维数组
    if isinstance(audio_groups, list) and audio_groups and isinstance(audio_groups[0], list):
        candidates = []
        for g in audio_groups:
            if not isinstance(g, list):
                continue
            valid = [it for it in g if isinstance(it, dict) and it.get("url") and (it.get("duration_sec", 0) or 0) > 0]
            if valid:
                candidates.append(valid)
        if not candidates:
            return []
        if rnd is None:
            rnd = random
        return list(rnd.choice(candidates))

    # 旧结构：一维数组
    valid = [it for it in audio_groups if isinstance(it, dict) and it.get("url") and (it.get("duration_sec", 0) or 0) > 0]
    return list(valid)


def _flatten_audio_groups(audio_groups):
    """把一维/二维结构扁平化为 list[dict]（过滤无效项）。"""
    if not audio_groups:
        return []
    if isinstance(audio_groups, list) and audio_groups and isinstance(audio_groups[0], list):
        out = []
        for g in audio_groups:
            if isinstance(g, list):
                out.extend(g)
        audio_groups = out
    return [it for it in (audio_groups or []) if isinstance(it, dict) and it.get("url") and (it.get("duration_sec", 0) or 0) > 0]


# 从audio.json获取sleep_talk数据
def get_sleep_talk_data(min_snore=2, max_snore=6, include_sleep_talk=True, include_cough=True):
    """从audio.json获取audios数据。Snore 至少 min_snore 条，可选 Somniloquy/Cough。"""
    audio_file = 'qiniu/audio.json'
    if not os.path.exists(audio_file):
        return []

    try:
        with open(audio_file, 'r', encoding='utf-8') as f:
            audio_data = json.load(f)
    except Exception as e:
        print(f"读取audio.json文件时出错: {str(e)}")
        return []

    selected_audio = []

    # 打鼾音频：至少 min_snore 条，尽量不重复
    # 关键：每条数据先从 audioUrl 选定一个子数组，后续只从该子数组中取
    snoring_audio = _pick_audio_group(audio_data.get('audioUrl', []), rnd=random)
    if snoring_audio:
        random.shuffle(snoring_audio)
        min_n = max(int(min_snore or 0), 0)
        if max_snore is None:
            want = min_n
        else:
            max_n = max(int(max_snore or 0), 0)
            if max_n < min_n:
                max_n = min_n
            want = random.randint(min_n, max_n)
        picked = snoring_audio[: min(want, len(snoring_audio))]
        # 若素材不足，允许重复补齐
        while len(picked) < want:
            picked.append(random.choice(snoring_audio))
        for it in picked:
            selected_audio.append({"url": it.get("url"), "duration_sec": it.get("duration_sec", 0), "type": "Snore"})

    # 梦话音频
    if include_sleep_talk:
        sleep_talking_audio = [item for item in audio_data.get('sleepTalkingUrl', []) if item.get('duration_sec', 0) > 0 and item.get('url')]
        if sleep_talking_audio:
            it = random.choice(sleep_talking_audio)
            selected_audio.append({"url": it.get("url"), "duration_sec": it.get("duration_sec", 0), "type": "Somniloquy"})

    # 咳嗽音频
    if include_cough:
        cough_audio = [item for item in audio_data.get('coughUrl', []) if item.get('duration_sec', 0) > 0 and item.get('url')]
        if cough_audio:
            it = random.choice(cough_audio)
            selected_audio.append({"url": it.get("url"), "duration_sec": it.get("duration_sec", 0), "type": "Cough"})

    return selected_audio


def plan_auditory_snore_talk_cough_counts(sleep_data):
    """打鼾：0 次，或一晚 3～6 条；梦话 0-1 次；咳嗽 0 次或 3 次。"""
    raw = sleep_data.get("raw_data", {})
    apnea_count = int(raw.get("apnea_count", 0) or 0)
    if apnea_count >= 5:
        has_snore = random.random() < 0.80
    else:
        has_snore = random.random() < 0.55
    n_snore = random.randint(3, 6) if has_snore else 0
    include_sleep_talk = random.random() < 0.35
    include_cough = random.random() < 0.25
    return n_snore, include_sleep_talk, include_cough


def sleep_local_window_bounds_from_sleep_data(sleep_data):
    """返回 (sleep_time, window_end) 本地 naive datetime；无效时 (None, None)。
    window_end 为「起床」时刻（raw_data.wake_time），即睡眠过程（入睡→睡眠结束）上界；
    若无 wake_time 则退化为 wake_up_time。
    """
    raw = sleep_data.get("raw_data", {})
    sleep_time_str = raw.get("sleep_time", "")
    wake_up_time_str = raw.get("wake_up_time", "")
    wake_time_str = raw.get("wake_time", "")
    if not sleep_time_str or (not wake_time_str and not wake_up_time_str):
        return None, None
    sleep_time = _parse_utc_iso_to_local_dt(sleep_time_str)
    window_end = _parse_utc_iso_to_local_dt(wake_time_str) or _parse_utc_iso_to_local_dt(
        wake_up_time_str
    )
    if not sleep_time or not window_end:
        return None, None
    if window_end < sleep_time:
        window_end += timedelta(days=1)
    return sleep_time, window_end


def collect_auditory_sleep_events_in_window(
    all_events,
    user_id,
    sleep_time,
    window_end,
    session_record_date=None,
):
    """同一 record_date（与健康记录日一致，含跨午夜仍用当日）且落在睡眠窗内的打鼾/梦话/咳嗽事件。"""
    if not sleep_time or not window_end or not all_events:
        return []
    out = []
    for e in all_events:
        if user_id and e.get("uid") != user_id:
            continue
        if session_record_date is not None and e.get("record_date") != session_record_date:
            continue
        code = e.get("code")
        if code not in _AUDITORY_SLEEP_EVENT_CODES:
            continue
        dt = session_anchor_event_local_dt(e, sleep_time, window_end)
        if dt == datetime.min:
            continue
        if not (sleep_time <= dt <= window_end):
            continue
        out.append(e)
    out.sort(key=lambda x: session_anchor_event_local_dt(x, sleep_time, window_end))
    return out


def build_audios_from_auditory_sleep_events(events_sorted, record_date=""):
    """按事件条数从 audio.json 取 URL，每条带与事件一致的 time（HH:MM）。"""
    if not events_sorted:
        return []
    audio_file = "qiniu/audio.json"
    if not os.path.exists(audio_file):
        return []
    try:
        with open(audio_file, "r", encoding="utf-8") as f:
            audio_data = json.load(f)
    except Exception:
        return []

    rnd = random.Random(hash(record_date) & 0xFFFFFFFF)
    # 关键：同一条睡眠报告内，Snore 的素材池固定为 audioUrl 的某一个子数组
    snore_pool_fixed = _pick_audio_group(audio_data.get("audioUrl", []), rnd=rnd)

    def pool_for(atype):
        if atype == "Snore":
            return list(snore_pool_fixed)
        if atype == "Somniloquy":
            return _flatten_audio_groups(audio_data.get("sleepTalkingUrl", []))
        if atype == "Cough":
            return _flatten_audio_groups(audio_data.get("coughUrl", []))
        return []

    audios = []
    used_urls = set()
    for ev in events_sorted:
        code = ev.get("code")
        atype = _CODE_TO_AUDIO_TYPE.get(code)
        if not atype:
            continue
        pool = list(pool_for(atype))
        if not pool:
            continue
        rnd.shuffle(pool)
        pick = None
        for it in pool:
            u = it.get("url")
            if u and u not in used_urls:
                pick = it
                break
        if pick is None:
            pick = rnd.choice(pool)
        u = pick.get("url")
        if u:
            used_urls.add(u)
        ts = ev.get("event_timestamp") or ""
        audios.append(
            {
                "url": pick.get("url"),
                "duration_sec": pick.get("duration_sec", 0),
                "type": atype,
                "time": ts,
            }
        )
    return audios


def backfill_auditory_audio_times_from_window_events(
    audios, all_events, user_id, sleep_time, window_end, health_report_record_date=None
):
    """为尚无 time 的听觉类 audio 按睡眠窗内同类事件时间顺序补齐。
    health_report_record_date：与健康/报告 record_date 一致（凌晨时刻事件仍用该日）。
    """
    if not audios or not all_events:
        return

    queues = {
        "Snore": deque(),
        "Somniloquy": deque(),
        "Cough": deque(),
    }
    for e in collect_auditory_sleep_events_in_window(
        all_events,
        user_id,
        sleep_time,
        window_end,
        health_report_record_date,
    ):
        at = _CODE_TO_AUDIO_TYPE.get(e.get("code"))
        ts = e.get("event_timestamp")
        if at and ts:
            queues[at].append(ts)
    for audio in audios:
        at = audio.get("type")
        if not at or audio.get("time"):
            continue
        dq = queues.get(at)
        if dq:
            audio["time"] = dq.popleft()


def resolve_auditory_audio_local_dt(
    record_date, time_str, sleep_start=None, window_end=None
):
    """将 audio.time（HH:MM）解析为睡眠窗内的本地 naive datetime；无窗时退化为 record_date 当日组合。"""
    if not record_date or not time_str:
        return None
    ts = str(time_str).strip()
    if not ts:
        return None
    parsed_tm = None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed_tm = datetime.strptime(ts, fmt).time()
            break
        except ValueError:
            continue
    if parsed_tm is None:
        return None
    try:
        d0 = datetime.strptime(str(record_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    c0 = datetime.combine(d0, parsed_tm)
    candidates = [c0, c0 + timedelta(days=1), c0 - timedelta(days=1)]
    if sleep_start is not None and window_end is not None:
        in_window = [c for c in candidates if sleep_start <= c <= window_end]
        if len(in_window) == 1:
            return in_window[0]
        if len(in_window) > 1:
            return min(in_window)
        return min(candidates, key=lambda c: abs((c - sleep_start).total_seconds()))
    return c0


_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")


def _normalize_snoring_hhmm(raw) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = _HHMM_RE.match(s)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return f"{hh:02d}:{mm:02d}"


def _is_snoring_sleep_event(event: dict) -> bool:
    if not isinstance(event, dict):
        return False
    if str(event.get("code") or "").strip().lower() == "snoring":
        return True
    return str(event.get("event_type") or "").strip() == "打鼾"


def _snoring_noise_db_by_hhmm(snoring_events) -> dict[str, int]:
    """打鼾事件按 HH:MM 索引 noise_db（同分钟内生成器保证相同）。"""
    lookup: dict[str, int] = {}
    for ev in snoring_events or []:
        if not _is_snoring_sleep_event(ev):
            continue
        hhmm = _normalize_snoring_hhmm(ev.get("event_timestamp"))
        if not hhmm:
            continue
        noise = ev.get("noise_db")
        if noise is None:
            continue
        try:
            lookup[hhmm] = int(round(float(noise)))
        except (TypeError, ValueError):
            continue
    return lookup


def build_snoring_analysis_data_points(
    audios,
    snoring_events=None,
    *,
    apnea_count=0,
):
    """
    由 audios 中 type 为 Snore 且含 time 的条目生成 data_points：{time, value}。
    value 取自同分钟打鼾睡眠事件的 noise_db；缺失时在 60–85（呼吸暂停多时可至 95）随机。
    """
    lookup = _snoring_noise_db_by_hhmm(snoring_events)
    assigned = []
    for audio in audios or []:
        if (audio.get("type") or "") != "Snore" or not audio.get("time"):
            continue
        hhmm = _normalize_snoring_hhmm(audio.get("time"))
        snore_value = lookup.get(hhmm) if hhmm else None
        if snore_value is None:
            base_lo, base_hi = 60, 85
            if int(apnea_count or 0) >= 5:
                base_hi = min(95, base_hi + 5)
            snore_value = random.randint(base_lo, base_hi)
        assigned.append({"time": audio["time"], "value": snore_value})
    return assigned


def index_environment_noise_rows_by_record_date(user_id, output_dir="output"):
    """
    读取 output/{user_id}_environment_data.json，建立 record_date -> [(本地时刻, noise), ...]。
    与流水线 report_audios 中环境索引一致。
    """
    environment_file = os.path.join(output_dir, f"{user_id}_environment_data.json")
    out = {}
    if not os.path.exists(environment_file):
        return out
    try:
        with open(environment_file, "r", encoding="utf-8") as f:
            environment_data = json.load(f)
    except Exception:
        return out
    if not isinstance(environment_data, list):
        return out
    for row in environment_data:
        if not isinstance(row, dict):
            continue
        rd = row.get("record_date")
        if not rd:
            continue
        dt_local = collected_at_to_local_naive_dt(row.get("collected_at"))
        if dt_local is None:
            continue
        noise = row.get("noise")
        try:
            noise_val = int(round(float(noise)))
        except (TypeError, ValueError):
            continue
        out.setdefault(rd, []).append((dt_local, noise_val))
    for rows in out.values():
        rows.sort(key=lambda x: x[0])
    return out


def rebuild_auditory_audios_and_snoring_data_points(
    record_date,
    user_id,
    sleep_events,
    sleep_day,
    env_rows_for_date=None,
):
    """
    按睡眠窗从 sleep_events 重建 audios，并生成 snoring data_points（不写回文件）。
    与 user_gen_pipeline.report_audios 中 audios + data_points 计算一致；不含 sleep_events duration 回填。
    env_rows_for_date 已废弃，保留参数仅为兼容旧调用方。
    返回 (audios, data_points 列表)。
    """
    _ = env_rows_for_date
    apnea_count = int((sleep_day.get("raw_data") or {}).get("apnea_count", 0) or 0) if sleep_day else 0
    st, we = sleep_local_window_bounds_from_sleep_data(sleep_day or {})
    sel = (
        collect_auditory_sleep_events_in_window(
            sleep_events, user_id, st, we, session_record_date=record_date
        )
        if (st and we)
        else []
    )
    audios = build_audios_from_auditory_sleep_events(sel, record_date)
    if st and we:
        backfill_auditory_audio_times_from_window_events(
            audios, sleep_events, user_id, st, we, record_date
        )
    snoring_events = [e for e in sel if _is_snoring_sleep_event(e)]
    dps = build_snoring_data_points_per_minute(
        snoring_events,
        sleep_day=sleep_day,
    )
    return audios, dps


def build_snoring_data_points_per_minute(
    snoring_events,
    *,
    sleep_day=None,
    bedtime_min=None,
):
    """按分钟聚合 sleep_events.noise_db，生成 snoring_analysis.data_points。"""
    if bedtime_min is None and sleep_day:
        bedtime_min = _bedtime_minutes_from_sleep_day(sleep_day)
    from write_back.merge_snoring_data_points import build_snoring_data_points_from_events

    return build_snoring_data_points_from_events(
        snoring_events or [], bedtime_min=bedtime_min
    )


def _bedtime_minutes_from_sleep_day(sleep_day: dict) -> int | None:
    """从 health 日记录的 bed_time 解析本地 HH:MM 分钟数（用于跨午夜排序）。"""
    raw = (sleep_day or {}).get("raw_data") or {}
    bed_iso = raw.get("bed_time") or raw.get("sleep_time")
    if not bed_iso:
        return None
    try:
        from .shared import format_time_to_hhmm, utc_to_local

        hm = format_time_to_hhmm(utc_to_local(str(bed_iso)))
    except Exception:
        return None
    return _hhmm_to_minutes(hm)


def _hhmm_to_minutes(hhmm: str) -> int | None:
    norm = _normalize_snoring_hhmm(hhmm)
    if not norm:
        return None
    hh, mm = norm.split(":")
    return int(hh) * 60 + int(mm)


def _audio_time_to_session_dt(audio_time, record_date, sleep_time=None, window_end=None):
    """将报告 audio.time（HH:MM）对齐为会话内绝对时刻。"""
    if not audio_time or not record_date:
        return datetime.min
    pseudo_event = {"record_date": record_date, "event_timestamp": str(audio_time).strip()}
    return session_anchor_event_local_dt(pseudo_event, sleep_time, window_end)


def backfill_auditory_event_durations_from_report_audios(
    audios,
    all_events,
    user_id,
    record_date,
    sleep_time=None,
    window_end=None,
):
    """
    按睡眠报告 auditory.audios 回填 sleep_events 中听觉事件 duration_sec。
    匹配规则（同 uid+record_date+type）：
    1) 优先 time 精确匹配；
    2) 其次按 time 最近匹配；
    3) 仍无法匹配则按该类型事件时间顺序依次匹配。
    返回更新条数。
    """
    if not audios or not all_events or not record_date:
        return 0

    event_buckets = {"Snore": [], "Somniloquy": [], "Cough": []}
    for e in all_events:
        if user_id and e.get("uid") != user_id:
            continue
        if e.get("record_date") != record_date:
            continue
        atype = _CODE_TO_AUDIO_TYPE.get(e.get("code"))
        if atype not in event_buckets:
            continue
        dt = session_anchor_event_local_dt(e, sleep_time, window_end)
        event_buckets[atype].append((dt, e))

    for k in event_buckets:
        event_buckets[k].sort(key=lambda x: x[0])

    used_event_obj_ids = set()
    updated = 0

    for audio in audios:
        atype = audio.get("type")
        if atype not in event_buckets:
            continue
        try:
            duration = int(audio.get("duration_sec", 0) or 0)
        except (TypeError, ValueError):
            duration = 0
        if duration <= 0:
            continue

        candidates = [
            (dt, ev)
            for dt, ev in event_buckets[atype]
            if id(ev) not in used_event_obj_ids
        ]
        if not candidates:
            continue

        chosen = None
        audio_time = str(audio.get("time") or "").strip()
        if audio_time:
            # 1) 同 HH:MM 的精确匹配
            for dt, ev in candidates:
                if str(ev.get("event_timestamp") or "").strip() == audio_time:
                    chosen = ev
                    break
            # 2) 距离 audio.time 最近匹配
            if chosen is None:
                target_dt = _audio_time_to_session_dt(
                    audio_time, record_date, sleep_time, window_end
                )
                if target_dt != datetime.min:
                    _, chosen = min(
                        candidates,
                        key=lambda x: abs((x[0] - target_dt).total_seconds()),
                    )

        # 3) 无 time 或无法按 time 定位时，按顺序取首个未使用事件
        if chosen is None:
            chosen = candidates[0][1]

        chosen["duration_sec"] = duration
        used_event_obj_ids.add(id(chosen))
        updated += 1

    return updated
