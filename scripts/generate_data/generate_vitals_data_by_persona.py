"""根据 health_data_personas_config.json 与 output/{uid}_health_data.json 生成体征数据。

在每条睡眠记录的 bed_time～wake_up_time（本地）内按 generation.sample_interval_sec（默认 60 秒）
等间隔生成采样点；心率/呼吸随 idf 分期与昼夜形态变化。

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


def _stage_to_sleep_state(stage: str | None) -> int:
    """实时睡眠状态：1 清醒；2 REM；3 浅睡；4 深睡；7 离床。"""
    return {"awake": 1, "rem": 2, "light": 3, "deep": 4}.get(stage or "", 1)


def _stage_to_activity_state(stage: str | None) -> int:
    """人体活动状态：0 无人；1 静息；2 安静；3 动作；4 持续动作。"""
    if stage == "deep":
        return 1
    if stage == "light":
        return 2
    if stage == "rem":
        return 3
    if stage == "awake":
        return 4 if random.random() < 0.4 else 3
    return 2


def _vital_signs_state(row_rr: float, rr_lo: int, rr_hi: int, presence_state: int) -> int:
    """生命体征异常：0 正常；5 未检测到；12 呼吸过高；13 呼吸过低。"""
    if presence_state == 2:
        return 5
    if row_rr >= rr_hi:
        return 12
    if row_rr <= rr_lo:
        return 13
    return 0


def _extended_radar_metrics(
    *,
    stage: str | None,
    row_rr: float,
    rr_lo: int,
    rr_hi: int,
    collected_at_local: datetime,
    bed_time: datetime | None,
    hr_env_spike: float,
) -> dict[str, int | float]:
    presence_state = 1
    if hr_env_spike > 6.0 or random.random() < 0.01:
        presence_state = 2

    signal_strength = round(random.uniform(-72.0, -38.0), 2)
    if presence_state == 2:
        signal_strength = round(random.uniform(-92.0, -62.0), 2)

    activity_state = _stage_to_activity_state(stage)
    if presence_state == 2 and activity_state == 1:
        activity_state = 2

    in_bed_duration = 0
    if bed_time and collected_at_local >= bed_time:
        in_bed_duration = max(
            0, int((collected_at_local - bed_time).total_seconds() // 60)
        )

    return {
        "presence_state": presence_state,
        "activity_state": activity_state,
        "nearest_target_distance": random.randint(40, 180),
        "vital_signs_state": _vital_signs_state(row_rr, rr_lo, rr_hi, presence_state),
        "signal_strength": signal_strength,
        "in_bed_duration": in_bed_duration,
        "out_bed_duration": 0,
        "sleep_state": _stage_to_sleep_state(stage),
    }


def _bounds_from_raw(raw_data: dict) -> dict:
    ah = float(raw_data.get("average_heartbeat", 70))
    ar = float(raw_data.get("average_respiration", 16))
    return {
        "hr_lo": max(48, int(ah - 22)),
        "hr_hi": min(118, int(ah + 22)),
        "rr_lo": max(10, int(ar - 4)),
        "rr_hi": min(26, int(ar + 5)),
        "heart_rate": max(50, min(105, int(ah))),
        "respiration_rate": max(11, min(24, float(ar))),
    }


def _vitals_rows_one_day(
    user_id: str,
    record_date: str,
    sleep_record: dict,
    personality_type: str,
    gen: dict,
) -> list[dict]:
    del personality_type  # 保留参数以兼容调用方
    raw_data = sleep_record.get("raw_data") or {}
    collected_local_dts = _dense_local_dts_bed_to_wake(
        raw_data, int(gen.get("sample_interval_sec") or 60)
    )
    if not collected_local_dts:
        return []

    b = _bounds_from_raw(raw_data)
    hr_lo, hr_hi = b["hr_lo"], b["hr_hi"]
    rr_lo, rr_hi = b["rr_lo"], b["rr_hi"]
    heart_rate = b["heart_rate"]
    respiration_rate = b["respiration_rate"]

    apnea_count = int(raw_data.get("apnea_count", 0) or 0)
    turnover_count = int(raw_data.get("turnover_count", 0) or 0)

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
        hr_adj = hr_stage_adj.get(stage, -6)
        rr_adj = rr_stage_adj.get(stage, -0.25)

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
        if stage == "rem":
            hr_noise += int(round(random.uniform(-8, 10)))
            rr_noise += random.uniform(-3.2, 3.8)
        elif stage == "deep":
            hr_noise = int(round(random.uniform(-1.5, 1.5)))
            rr_noise *= 0.45
        elif stage == "light":
            hr_noise = int(round(random.uniform(-2, 2)))
        elif stage == "awake":
            hr_noise += int(round(random.uniform(-3, 5)))

        target_hr = max(
            hr_lo,
            min(
                hr_hi,
                heart_rate + hr_adj + hr_noise + circ["hr"] + pre["hr"],
            ),
        )
        target_hr = max(hr_lo, min(hr_hi, target_hr + hr_env[idx]))

        target_rr = max(
            rr_lo,
            min(
                rr_hi,
                int(
                    round(
                        respiration_rate + rr_adj + rr_noise + circ["rr"] + pre["rr"]
                    )
                ),
            ),
        )
        target_rr = max(rr_lo, min(rr_hi, target_rr + rr_env[idx]))

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

        create_time = collected_at_local + timedelta(seconds=1)
        collected_at_utc_z = gh.local_naive_dt_to_utc_iso_z(collected_at_local)
        if idf_end_utc and n_collect > 0 and idx == n_collect - 1:
            collected_at_utc_z = idf_end_utc

        metrics = {
            "respiration_rate": int(round(row_rr)),
            "heart_rate": int(round(row_hr)),
        }
        metrics.update(
            _extended_radar_metrics(
                stage=stage,
                row_rr=row_rr,
                rr_lo=rr_lo,
                rr_hi=rr_hi,
                collected_at_local=collected_at_local,
                bed_time=bed_for_session,
                hr_env_spike=hr_env[idx],
            )
        )

        rows.append(
            {
                "uid": user_id,
                "record_date": record_date,
                "collected_at": collected_at_utc_z,
                "data_source": "radar",
                "metrics": metrics,
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
