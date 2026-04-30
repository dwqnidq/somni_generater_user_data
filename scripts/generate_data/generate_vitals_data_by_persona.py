"""根据 health_data_personas_config.json 与 output/{uid}_health_data.json 生成体征数据。

在每条睡眠记录的 bed_time～wake_up_time（本地）内按 generation.sample_interval_sec（默认 60 秒）
等间隔生成采样点；心率/呼吸/HRV/体动随 idf 分期与昼夜形态变化，逻辑对齐 generate_health_data.HealthDataGenerator.generate_vital_signs。

用法：
  python scripts/generate_data/generate_vitals_data_by_persona.py
  python scripts/generate_data/generate_vitals_data_by_persona.py --user-id 69aea6d8af5e6cbf08027966 --overwrite
"""

from __future__ import annotations

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
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_ROOT)

import generate_health_data as gh
from persona_generation_config import merge_generation
from personality_profile import get_hrv_adjustment, parse_personality_code
from utils import atomic_write_json

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "health_data_personas_config.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")


def _dense_local_dts_bed_to_wake(raw_data: dict, interval_sec: int) -> list[datetime]:
    bed, wake = gh._extract_local_sleep_window(
        raw_data or {}, start_key="bed_time", end_key="wake_up_time"
    )
    if not bed or not wake or wake <= bed:
        return []
    out: list[datetime] = []
    t = bed
    delta = timedelta(seconds=max(1, int(interval_sec)))
    while t <= wake:
        out.append(t)
        t += delta
    return out


def _base_body_motion(turnover_count: int, personality_type: str) -> int:
    dims = parse_personality_code(personality_type)
    is_sensitive = dims.get("sensitivity") == "H"
    # 连续线性映射：turnover 6-80 → body_motion 15-85
    tc = max(6, min(80, turnover_count))
    base_level = 15 + (tc - 6) * (85 - 15) / max(1, 80 - 6)
    base_level = max(10, min(90, base_level))
    if is_sensitive:
        base_level = min(90, base_level + 8)
    return int(round(base_level))


def _hrv_scalar_base(sleep_record: dict, personality_type: str) -> float:
    raw_data = sleep_record.get("raw_data", {}) or {}
    average_heartbeat = float(raw_data.get("average_heartbeat", 70))
    sleep_score = float(raw_data.get("sleep_score", 70))
    deep_sleep_ratio = float(raw_data.get("deep_sleep_ratio", 20))
    awake_ratio = float(raw_data.get("awake_ratio", 10))
    hrv_lo, hrv_hi = 1.5, 2.0
    base_hrv = (hrv_lo + hrv_hi) / 2.0
    heart_rate_factor = (70.0 - average_heartbeat) * 0.003
    sleep_score_factor = (sleep_score - 75.0) * 0.0015
    deep_sleep_factor = (deep_sleep_ratio - 20.0) * 0.004
    awake_factor = (8.0 - awake_ratio) * 0.004
    hrv_adj = float(get_hrv_adjustment(personality_type)) * 0.004
    hrv = (
        base_hrv
        + heart_rate_factor
        + sleep_score_factor
        + deep_sleep_factor
        + awake_factor
        + hrv_adj
    )
    return max(hrv_lo, min(hrv_hi, hrv))


def _bounds_from_raw(raw_data: dict) -> dict:
    ah = float(raw_data.get("average_heartbeat", 70))
    ar = float(raw_data.get("average_respiration", 16))
    return {
        "hr_lo": max(48, int(ah - 22)),
        "hr_hi": min(118, int(ah + 22)),
        "rr_lo": max(10, int(ar - 4)),
        "rr_hi": min(26, int(ar + 5)),
        "sys_lo": 98,
        "sys_hi": 132,
        "dia_lo": 62,
        "dia_hi": 90,
        "bo_lo": 92,
        "bo_hi": 99,
        "mot_lo": 1,
        "mot_hi": 100,
        "heart_rate": max(50, min(105, int(ah))),
        "respiration_rate": max(11, min(24, float(ar))),
        "blood_oxygen": max(93, min(99, int(raw_data.get("blood_oxygen", 96) or 96))),
        "blood_pressure_systolic": int(raw_data.get("blood_pressure_systolic", 118) or 118),
        "blood_pressure_diastolic": int(raw_data.get("blood_pressure_diastolic", 78) or 78),
    }


def _vitals_rows_one_day(
    user_id: str,
    record_date: str,
    sleep_record: dict,
    personality_type: str,
    gen: dict,
) -> list[dict]:
    raw_data = sleep_record.get("raw_data") or {}
    collected_local_dts = _dense_local_dts_bed_to_wake(
        raw_data, int(gen.get("sample_interval_sec") or 60)
    )
    if not collected_local_dts:
        return []

    b = _bounds_from_raw(raw_data)
    hr_lo, hr_hi = b["hr_lo"], b["hr_hi"]
    rr_lo, rr_hi = b["rr_lo"], b["rr_hi"]
    sys_lo, sys_hi = b["sys_lo"], b["sys_hi"]
    dia_lo, dia_hi = b["dia_lo"], b["dia_hi"]
    bo_lo, bo_hi = b["bo_lo"], b["bo_hi"]
    mot_lo, mot_hi = b["mot_lo"], b["mot_hi"]

    heart_rate = b["heart_rate"]
    respiration_rate = b["respiration_rate"]
    blood_oxygen = b["blood_oxygen"]
    blood_pressure_systolic = b["blood_pressure_systolic"]
    blood_pressure_diastolic = b["blood_pressure_diastolic"]

    turnover_count = int(raw_data.get("turnover_count", 0) or 0)
    base_motion = _base_body_motion(turnover_count, personality_type)

    apnea_count = int(raw_data.get("apnea_count", 0) or 0)
    if apnea_count >= 20:
        apnea_bo_offset = -3
    elif apnea_count >= 10:
        apnea_bo_offset = -2
    elif apnea_count >= 5:
        apnea_bo_offset = -1
    else:
        apnea_bo_offset = 0

    hrv_lo, hrv_hi = 1.5, 2.0
    hrv_base = _hrv_scalar_base(sleep_record, personality_type)

    sleep_window_start, sleep_window_end = gh._extract_local_sleep_window(
        raw_data, start_key="bed_time", end_key="wake_up_time"
    )
    stage_windows = {}
    if sleep_window_start and sleep_window_end:
        stage_windows = gh._build_stage_windows_from_idf(
            sleep_record.get("idf_data") or [],
            sleep_window_start,
            sleep_window_end,
        )
    awake_windows = gh._idf_awake_windows_minutes(
        sleep_record.get("idf_data") or []
    ) + gh._raw_awake_windows_minutes(raw_data)

    idf_for_align = sleep_record.get("idf_data") or []
    idf_end_utc = None
    if idf_for_align and sleep_window_start and sleep_window_end:
        idf_end_utc = gh._idf_last_segment_end_utc_iso_z(
            idf_for_align, sleep_window_start, sleep_window_end
        )

    # 用“缓慢爬升-回落包络”模拟异常影响（替代瞬时尖峰）
    n_collect = len(collected_local_dts)
    hr_env = [0.0] * n_collect
    rr_env = [0.0] * n_collect
    if n_collect >= 30:
        env_count = 1 + (1 if apnea_count >= 5 else 0) + (1 if turnover_count >= 25 else 0)
        env_count = min(env_count, 3)
        anchor_pool = list(range(max(8, n_collect // 8), max(9, n_collect - 8)))
        rng_anchors = random.sample(anchor_pool, min(env_count, len(anchor_pool))) if anchor_pool else []
        for c in rng_anchors:
            rise = random.randint(3, 8)
            hold = random.randint(4, 10)
            decay = random.randint(8, 20)
            amp_hr = random.uniform(4.0, 12.0)
            amp_rr = random.uniform(1.0, 4.0)
            st = max(0, c - rise)
            pk1 = c
            pk2 = min(n_collect - 1, c + hold)
            ed = min(n_collect - 1, pk2 + decay)
            for i in range(st, ed + 1):
                if i <= pk1:
                    frac = (i - st) / max(1, pk1 - st)
                elif i <= pk2:
                    frac = 1.0
                else:
                    frac = 1.0 - (i - pk2) / max(1, ed - pk2)
                frac = max(0.0, min(1.0, frac))
                hr_env[i] += amp_hr * frac
                rr_env[i] += amp_rr * frac

    bed_for_session = sleep_window_start
    sleep_onset_local = gh._sleep_onset_local_naive(raw_data, bed_for_session)
    wake_end_local = sleep_window_end

    rows: list[dict] = []
    prev_hr = None
    prev_rr = None
    prev_stage = None

    for idx, collected_at_local in enumerate(collected_local_dts):
        stage = gh._sleep_stage_at_local_dt(collected_at_local, stage_windows)
        if not stage and awake_windows and gh._is_in_awake_window(
            collected_at_local, awake_windows
        ):
            stage = "awake"

        hr_stage_adj = {"deep": -22, "light": -8, "rem": 6, "awake": 18}
        rr_stage_adj = {"deep": -2.0, "light": -0.35, "rem": 1.8, "awake": 2.2}
        hrv_stage_adj = {"deep": 0.11, "light": 0.025, "rem": 0.0, "awake": -0.11}
        mot_stage_adj = {"deep": -12, "light": -2, "rem": 2, "awake": 20}

        hr_adj = hr_stage_adj.get(stage, -6)
        rr_adj = rr_stage_adj.get(stage, -0.25)
        hrv_adj = hrv_stage_adj.get(stage, 0.01)
        mot_adj = mot_stage_adj.get(stage, -2)

        circ_t = gh._circadian_progress_t_rel(
            collected_at_local,
            sleep_onset_local,
            wake_end_local,
            bed_for_session,
        )
        circ = gh._circadian_vitals_offsets(circ_t)
        pre = gh._presleep_transition_offsets(
            collected_at_local, bed_for_session, sleep_onset_local
        )

        hr_noise = random.randint(-2, 2)
        rr_noise = random.uniform(-0.75, 0.75)
        hrv_noise = random.uniform(-0.03, 0.03)
        if stage == "rem":
            hr_noise += int(round(random.uniform(-8, 10)))
            rr_noise += random.uniform(-3.2, 3.8)
            hrv_noise += random.uniform(-0.09, 0.11)
        elif stage == "deep":
            hr_noise = int(round(random.uniform(-1.5, 1.5)))
            rr_noise *= 0.45
            hrv_noise *= 0.35
        elif stage == "light":
            hr_noise = int(round(random.uniform(-2, 2)))
        elif stage == "awake":
            hr_noise += int(round(random.uniform(-3, 5)))
            hrv_noise += random.uniform(-0.025, 0.025)

        target_hr = max(
            hr_lo,
            min(
                hr_hi,
                heart_rate
                + hr_adj
                + hr_noise
                + circ["hr"]
                + pre["hr"],
            ),
        )
        target_hr = max(hr_lo, min(hr_hi, target_hr + hr_env[idx]))

        spo2_noise = random.randint(-2, 2)
        if apnea_count >= 5:
            spo2_noise += random.randint(-2, 0)  # 呼吸暂停时 SpO2 更容易下降
        row_bo = max(
            bo_lo, min(bo_hi, blood_oxygen + apnea_bo_offset + spo2_noise)
        )
        sys_stage_adj = {"deep": -10, "light": -6, "rem": 5, "awake": 8}
        dia_stage_adj = {"deep": -6, "light": -4, "rem": 4, "awake": 5}
        s_adj = sys_stage_adj.get(stage, -4)
        d_adj = dia_stage_adj.get(stage, -3)
        row_sys = max(
            sys_lo,
            min(
                sys_hi,
                int(
                    round(
                        blood_pressure_systolic
                        + s_adj
                        + random.randint(-2, 2)
                        + circ["sys"]
                    )
                ),
            ),
        )
        row_dia = max(
            dia_lo,
            min(
                dia_hi,
                int(
                    round(
                        blood_pressure_diastolic
                        + d_adj
                        + random.randint(-2, 2)
                        + circ["dia"]
                    )
                ),
            ),
        )
        if row_sys <= row_dia:
            row_sys = min(sys_hi, row_dia + random.randint(20, 35))

        target_rr = max(
            rr_lo,
            min(
                rr_hi,
                int(
                    round(
                        respiration_rate
                        + rr_adj
                        + rr_noise
                        + circ["rr"]
                        + pre["rr"]
                    )
                ),
            ),
        )
        target_rr = max(rr_lo, min(rr_hi, target_rr + rr_env[idx]))

        # 平滑：限制单分钟变化，避免“突然抬高”
        if prev_hr is None:
            row_hr = float(target_hr)
        else:
            alpha_hr = 0.22 if stage == prev_stage else 0.14
            blended_hr = prev_hr + alpha_hr * (float(target_hr) - prev_hr)
            delta_hr = max(-3.0, min(3.0, blended_hr - prev_hr))
            row_hr = prev_hr + delta_hr
        row_hr = max(float(hr_lo), min(float(hr_hi), row_hr))
        prev_hr = row_hr

        if prev_rr is None:
            row_rr = float(target_rr)
        else:
            alpha_rr = 0.28 if stage == prev_stage else 0.20
            blended_rr = prev_rr + alpha_rr * (float(target_rr) - prev_rr)
            delta_rr = max(-1.0, min(1.0, blended_rr - prev_rr))
            row_rr = prev_rr + delta_rr
        row_rr = max(float(rr_lo), min(float(rr_hi), row_rr))
        prev_rr = row_rr
        prev_stage = stage

        row_hrv = round(
            max(
                hrv_lo,
                min(
                    hrv_hi,
                    hrv_base
                    + hrv_adj
                    + hrv_noise
                    + circ["hrv"]
                    + pre["hrv"],
                ),
            ),
            3,
        )
        mot_noise = random.randint(-5, 5)
        if stage == "rem":
            mot_noise = random.randint(0, 10)
        row_motion = max(
            mot_lo,
            min(
                mot_hi,
                base_motion + mot_adj + mot_noise + circ["motion"] + pre["motion"],
            ),
        )

        create_time = collected_at_local + timedelta(seconds=1)
        collected_at_utc_z = gh.local_naive_dt_to_utc_iso_z(collected_at_local)
        if idf_end_utc and n_collect > 0 and idx == n_collect - 1:
            collected_at_utc_z = idf_end_utc

        rows.append(
            {
                "uid": user_id,
                "record_date": record_date,
                "collected_at": collected_at_utc_z,
                "data_source": "radar",
                "metrics": {
                    "respiration_rate": int(round(row_rr)),
                    "heart_rate": int(round(row_hr)),
                    "body_motion_level": int(round(row_motion)),
                    "blood_oxygen": int(round(row_bo)),
                    "blood_pressure_systolic": int(round(row_sys)),
                    "blood_pressure_diastolic": int(round(row_dia)),
                    "hrv": row_hrv,
                },
                "device_id": "",
                "session_id": "",
                "create_time": create_time.strftime("%Y-%m-%d %H:%M:%S"),
                "update_time": create_time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return rows


def generate_vitals_for_persona(
    persona: dict,
    cfg: dict,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    overwrite: bool = False,
) -> str | None:
    uid = persona["user_id"]
    out_path = os.path.join(OUTPUT_DIR, f"{uid}_vitals_data.json")
    if not overwrite and os.path.exists(out_path):
        print(f"[{persona.get('name', uid)}] 体征文件已存在，跳过（--overwrite 覆盖）")
        return out_path

    health_path = os.path.join(OUTPUT_DIR, f"{uid}_health_data.json")
    if not os.path.exists(health_path):
        print(f"[{uid}] 未找到 {health_path}，请先运行 generate_health_data_by_persona_config.py")
        return None

    with open(health_path, "r", encoding="utf-8") as f:
        health_rows = json.load(f)
    if not isinstance(health_rows, list):
        print(f"[{uid}] health_data 格式无效")
        return None

    gen = merge_generation(cfg, persona)
    ptype = persona.get("code") or "M-L-C"
    all_rows: list[dict] = []

    for rec in health_rows:
        if not isinstance(rec, dict):
            continue
        rd = rec.get("record_date")
        if not rd:
            continue
        if start_date and rd < start_date.isoformat():
            continue
        if end_date and rd > end_date.isoformat():
            continue
        all_rows.extend(_vitals_rows_one_day(uid, str(rd), rec, ptype, gen))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    atomic_write_json(out_path, all_rows)
    print(f"[{persona.get('name', uid)}] 体征数据 {len(all_rows)} 条 → {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="按人格配置与 health_data 生成 vitals_data（默认 60s 间隔）")
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
        generate_vitals_for_persona(
            p, cfg, start_date=start_date, end_date=end_date, overwrite=args.overwrite
        )
    print("完成。")


if __name__ == "__main__":
    main()
