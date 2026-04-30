"""根据 health_data_personas_config.json 与 output/{uid}_health_data.json 生成环境数据。

在每条睡眠记录的 bed_time～wake_up_time（本地）内按 generation.sample_interval_sec（默认 60 秒）
等间隔生成采样点，复用 generate_health_data 中 _sample_environment_metrics_sequence 的温湿光噪形态。

用法：
  python scripts/generate_data/generate_environment_data_by_persona.py
  python scripts/generate_data/generate_environment_data_by_persona.py --user-id 69aea6e3af5e6cbf08027967
  python scripts/generate_data/generate_environment_data_by_persona.py --overwrite
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
from personality_profile import parse_personality_code
from utils import atomic_write_json

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "health_data_personas_config.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")


def _synthetic_user_config(persona_code: str, gen: dict) -> dict:
    dims = parse_personality_code(persona_code)
    is_sensitive = dims.get("sensitivity") == "H"

    tr = gen["environment"]["temperature_bedroom_range"]
    hr = gen["environment"]["humidity_range"]
    ir = gen["environment"]["illuminance_sleep_range"]

    # 高敏感人格：温度和湿度范围收窄，环境更稳定
    if is_sensitive:
        t_mid = (tr[0] + tr[1]) / 2
        t_half = (tr[1] - tr[0]) * 0.35
        tr = [t_mid - t_half, t_mid + t_half]
        h_mid = (hr[0] + hr[1]) / 2
        h_half = (hr[1] - hr[0]) * 0.35
        hr = [h_mid - h_half, h_mid + h_half]

    return {
        "personalInformation": {"type": persona_code},
        "sleepTemperature": {"min": [int(tr[0])], "max": [int(tr[1])]},
        "sleepHumidity": {"min": [int(hr[0])], "max": [int(hr[1])]},
        "sleepIlluminance": {"min": [int(ir[0])], "max": [int(ir[1])]},
    }


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


def _post_enforce_noise_layers(
    metrics_seq: list[tuple],
    continuous_min: int,
    interval_sec: int,
) -> list[tuple]:
    """在已生成序列上仅保留一段持续噪声，不再注入随机突发峰值。"""
    n = len(metrics_seq)
    if n == 0:
        return metrics_seq
    rows = [list(x) for x in metrics_seq]

    points_30m = max(1, int(round((30 * 60) / max(1, interval_sec))))
    points_90m = max(points_30m, int(round((90 * 60) / max(1, interval_sec))))
    span = min(n, random.randint(points_30m, points_90m))
    start = random.randint(0, max(0, n - span))
    for j in range(start, min(n, start + span)):
        t, h, il, nz = rows[j]
        rows[j] = [t, h, il, max(int(nz), random.randint(continuous_min, continuous_min + 8))]

    return [tuple(r) for r in rows]


def _post_break_long_flat_temp_humi(metrics_seq: list[tuple], interval_sec: int) -> list[tuple]:
    """对温湿度做低频平滑，避免机械锯齿和长平段。"""
    n = len(metrics_seq)
    if n <= 2:
        return metrics_seq
    rows = [list(x) for x in metrics_seq]
    # 时间间隔越大，每步平滑权重越高，保证趋势自然且连续
    alpha = min(0.45, max(0.12, interval_sec / 600.0))

    temp_prev = float(rows[0][0])
    hum_prev = float(rows[0][1])
    rows[0][0] = round(temp_prev, 1)
    rows[0][1] = int(round(hum_prev))

    for i in range(1, n):
        t_raw = float(rows[i][0])
        h_raw = float(rows[i][1])
        temp_prev = temp_prev + alpha * (t_raw - temp_prev)
        hum_prev = hum_prev + alpha * (h_raw - hum_prev)
        rows[i][0] = round(max(16.0, min(34.0, temp_prev)), 1)
        rows[i][1] = int(round(max(20.0, min(85.0, hum_prev))))

    return [tuple(r) for r in rows]


def _rows_for_one_day(
    user_id: str,
    record_date: str,
    raw_data: dict,
    idf_data: list,
    persona_code: str,
    gen: dict,
    data_label: str,
) -> list[dict]:
    interval_sec = int(gen.get("sample_interval_sec") or 60)
    collected_local_dts = _dense_local_dts_bed_to_wake(raw_data, interval_sec)
    if not collected_local_dts:
        return []

    n = len(collected_local_dts)
    onset_env_idx = None
    if (data_label or "").lower() == "bad" and n >= 3:
        early_max_idx = min(n - 2, max(1, int(round((90 * 60) / max(1, interval_sec)))))
        onset_env_idx = random.randint(1, early_max_idx)

    # 不再注入随机噪声峰值；噪声抬升由 sleep_events 侧回写驱动
    noise_spike_idxs: set[int] = set()

    awake_windows = gh._idf_awake_windows_minutes(idf_data or []) + gh._raw_awake_windows_minutes(
        raw_data
    )
    user_cfg = _synthetic_user_config(persona_code, gen)
    metrics_seq = gh._sample_environment_metrics_sequence(
        collected_local_dts,
        user_cfg,
        onset_env_idx,
        noise_spike_idxs,
        awake_windows=awake_windows,
    )

    env_cfg = gen.get("environment") or {}
    dims = parse_personality_code(persona_code)
    is_sensitive = dims.get("sensitivity") == "H"
    # 高敏感人格对噪声更敏感，噪声下限更低
    cmin = int(env_cfg.get("continuous_noise_db_min", 35))
    if is_sensitive:
        cmin = max(25, cmin - 10)
    metrics_seq = _post_enforce_noise_layers(metrics_seq, cmin, interval_sec)
    metrics_seq = _post_break_long_flat_temp_humi(metrics_seq, interval_sec)

    sleep_window_start, sleep_window_end = gh._extract_local_sleep_window(
        raw_data, start_key="bed_time", end_key="wake_up_time"
    )
    idf_end_utc = None
    if idf_data and sleep_window_start and sleep_window_end:
        idf_end_utc = gh._idf_last_segment_end_utc_iso_z(
            idf_data, sleep_window_start, sleep_window_end
        )

    rows: list[dict] = []
    n_env = len(collected_local_dts)
    for idx, collected_at_local in enumerate(collected_local_dts):
        temperature, humidity, illuminance, noise = metrics_seq[idx]
        create_time = collected_at_local + timedelta(seconds=1)
        collected_at_utc_z = gh.local_naive_dt_to_utc_iso_z(collected_at_local)
        if idf_end_utc and n_env > 0 and idx == n_env - 1:
            collected_at_utc_z = idf_end_utc
        rows.append(
            {
                "uid": user_id,
                "session_id": "",
                "record_date": record_date,
                "collected_at": collected_at_utc_z,
                "temperature": temperature,
                "humidity": humidity,
                "illuminance": illuminance,
                "noise": noise,
                "device_id": "",
                "create_time": create_time.strftime("%Y-%m-%d %H:%M:%S"),
                "update_time": create_time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return rows


def generate_environment_for_persona(
    persona: dict,
    cfg: dict,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    overwrite: bool = False,
) -> str | None:
    uid = persona["user_id"]
    out_path = os.path.join(OUTPUT_DIR, f"{uid}_environment_data.json")
    if not overwrite and os.path.exists(out_path):
        print(f"[{persona.get('name', uid)}] 环境文件已存在，跳过（--overwrite 覆盖）")
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
    code = persona.get("code") or "M-L-C"
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
        raw = rec.get("raw_data") or {}
        idf = rec.get("idf_data") or []
        label = rec.get("data_label") or ""
        all_rows.extend(
            _rows_for_one_day(uid, str(rd), raw, idf, code, gen, label),
        )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    atomic_write_json(out_path, all_rows)
    print(f"[{persona.get('name', uid)}] 环境数据 {len(all_rows)} 条 → {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="按人格配置与 health_data 生成 environment_data（默认 60s 间隔）")
    parser.add_argument("--user-id", default=None, help="仅处理该 user_id")
    parser.add_argument("--config", default=CONFIG_PATH, help="health_data_personas_config.json 路径")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的环境 JSON")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD 过滤起始")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD 过滤结束")
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
        generate_environment_for_persona(
            p, cfg, start_date=start_date, end_date=end_date, overwrite=args.overwrite
        )
    print("完成。")


if __name__ == "__main__":
    main()
