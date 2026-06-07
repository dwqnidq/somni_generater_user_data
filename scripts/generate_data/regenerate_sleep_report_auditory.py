#!/usr/bin/env python3
"""生成 auditory 数据（audios + data_points + module），输出到新目录。

约束：
- auditory.audios 仅取健康数据睡眠窗（sleep_time ~ wake_time）内的听觉事件；
- snoring_analysis.data_points 与 audios 中 type=Snore 且有 time 的条目一一对齐（同分钟）。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
from datetime import timedelta
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.generate_data.sleep_report.auditory import (  # noqa: E402
    build_apnea_auditory_target_title,
    build_auditory_snore_module,
    build_audios_from_auditory_sleep_events,
    build_snoring_analysis_data_points,
    collect_auditory_sleep_events_in_window,
    sleep_local_window_bounds_from_sleep_data,
)


def _load_audio_catalog() -> dict:
    """读取 qiniu/audio.json（缺失时返回空结构）。"""
    path = Path(PROJECT_ROOT) / "qiniu" / "audio.json"
    if not path.exists():
        return {"audioUrl": [], "sleepTalkingUrl": [], "coughUrl": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"audioUrl": [], "sleepTalkingUrl": [], "coughUrl": []}
    if not isinstance(data, dict):
        return {"audioUrl": [], "sleepTalkingUrl": [], "coughUrl": []}
    return data


def _flatten_audio_items(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    if raw and isinstance(raw[0], list):
        out = []
        for group in raw:
            if isinstance(group, list):
                out.extend(group)
        raw = out
    return [x for x in raw if isinstance(x, dict) and x.get("url")]


def _pick_snore_audio(audio_catalog: dict, rd: str) -> dict:
    """从素材中挑 1 条 Snore 音频，保证可复现。"""
    pool = _flatten_audio_items(audio_catalog.get("audioUrl", []))
    if not pool:
        return {
            "url": "https://cdn.fulai.tech/audio/1774420567_y4ToKfwBQtb.wav",
            "duration_sec": 12,
            "type": "Snore",
        }
    seeded = random.Random(hash(f"snore|{rd}") & 0xFFFFFFFF)
    item = seeded.choice(pool)
    return {
        "url": item.get("url"),
        "duration_sec": int(item.get("duration_sec", 12) or 12),
        "type": "Snore",
    }


def _pick_snore_audio_by_key(audio_catalog: dict, seed_key: str) -> dict:
    pool = _flatten_audio_items(audio_catalog.get("audioUrl", []))
    if not pool:
        return {
            "url": "https://cdn.fulai.tech/audio/1774420567_y4ToKfwBQtb.wav",
            "duration_sec": 12,
            "type": "Snore",
        }
    seeded = random.Random(hash(seed_key) & 0xFFFFFFFF)
    item = seeded.choice(pool)
    return {
        "url": item.get("url"),
        "duration_sec": int(item.get("duration_sec", 12) or 12),
        "type": "Snore",
    }


def _fallback_time_in_sleep_window(sleep_day: dict, rd: str) -> str:
    """无事件时在睡眠窗内构造一个 HH:MM 时刻；兜底为 03:30。"""
    sleep_start, window_end = sleep_local_window_bounds_from_sleep_data(sleep_day)
    if sleep_start and window_end and window_end > sleep_start:
        total_min = max(1, int((window_end - sleep_start).total_seconds() // 60))
        seeded = random.Random(hash(f"time|{rd}") & 0xFFFFFFFF)
        offset = seeded.randint(0, total_min - 1)
        t = sleep_start + timedelta(minutes=offset)
        return f"{t.hour:02d}:{t.minute:02d}"
    return "03:30"


def _clamp_int(value: float, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(round(value))))


def _stable_apnea_count(uid: str, record_date: str) -> int:
    """每晚稳定生成 7~13 次呼吸暂停计数（与上游健康数据解耦）。"""
    seeded = random.Random(hash(f"apnea|{uid}|{record_date}") & 0xFFFFFFFF)
    return seeded.randint(7, 13)


def _reshape_points_with_rising_waves(data_points: list[dict], record_date: str) -> list[dict]:
    """让分贝值具备“先低后高”的起伏感，保持 time 顺序与条数不变。"""
    if not data_points:
        return []
    if len(data_points) == 1:
        p = dict(data_points[0])
        p["value"] = _clamp_int(float(p.get("value") or 62), 50, 95)
        return [p]

    vals = []
    for p in data_points:
        try:
            vals.append(float(p.get("value")))
        except (TypeError, ValueError):
            vals.append(62.0)

    old_min = min(vals)
    old_max = max(vals)
    low_base = _clamp_int(old_min - 2, 48, 85)
    high_base = _clamp_int(old_max + 2, low_base + 8, 95)
    if high_base <= low_base:
        high_base = min(95, low_base + 10)

    # 使用日期稳定随机源，保证同一 record_date 可复现。
    seeded = random.Random(hash(record_date) & 0xFFFFFFFF)
    n = len(data_points)
    out = []
    for i, p in enumerate(data_points):
        t = i / max(1, n - 1)
        baseline = low_base + (high_base - low_base) * t
        # 小幅波动：锯齿 + 轻微随机扰动，既有起伏又不破坏总体上升。
        saw = (-2 if i % 3 == 0 else (2 if i % 3 == 1 else -1))
        jitter = seeded.randint(-1, 1)
        value = _clamp_int(baseline + saw + jitter, 45, 95)
        item = dict(p)
        item["value"] = value
        out.append(item)

    # 兜底保证“先低后高”明显成立。
    if int(out[-1].get("value", 0)) <= int(out[0].get("value", 0)):
        out[-1]["value"] = _clamp_int(int(out[0]["value"]) + 6, 45, 95)
    return out


def _build_segmented_snore_points(sleep_day: dict, rd: str, min_points: int = 8) -> list[dict]:
    """构造分段 Snore 点位，表现为几段连续分钟数据。"""
    sleep_start, window_end = sleep_local_window_bounds_from_sleep_data(sleep_day)
    seeded = random.Random(hash(f"seg|{rd}") & 0xFFFFFFFF)

    if sleep_start and window_end and window_end > sleep_start:
        total_min = max(60, int((window_end - sleep_start).total_seconds() // 60))
    else:
        sleep_start = None
        total_min = 7 * 60
    target_n = max(min_points, seeded.randint(min_points, min_points + 6))
    seg_n = 2 if target_n < 12 else seeded.randint(2, 3)
    remaining = target_n
    seg_sizes = []
    for i in range(seg_n):
        left = seg_n - i - 1
        if left == 0:
            seg_sizes.append(remaining)
            break
        size = seeded.randint(3, max(3, remaining - left * 3))
        seg_sizes.append(size)
        remaining -= size

    offsets = []
    cursor = seeded.randint(8, 35)
    for idx, size in enumerate(seg_sizes):
        for _ in range(size):
            offsets.append(cursor)
            cursor += seeded.randint(1, 3)
        if idx != len(seg_sizes) - 1:
            cursor += seeded.randint(25, 80)
    max_offset = max(offsets) if offsets else 0
    if max_offset >= total_min:
        scale = max(1.0, (max_offset + 1) / float(total_min - 1))
        offsets = [int(o / scale) for o in offsets]
    offsets = sorted(set(max(0, min(total_min - 1, o)) for o in offsets))

    points = []
    for i, off in enumerate(offsets):
        if sleep_start:
            t = sleep_start + timedelta(minutes=off)
            hhmm = f"{t.hour:02d}:{t.minute:02d}"
        else:
            hhmm = f"{(off // 60) % 24:02d}:{off % 60:02d}"
        base = 60 + (i % 6) * 3
        points.append({"time": hhmm, "value": _clamp_int(base + seeded.randint(-2, 2), 50, 95)})
    return points


def _ensure_dense_snore_alignment(
    audios: list[dict],
    data_points: list[dict],
    sleep_day: dict,
    rd: str,
    audio_catalog: dict,
) -> tuple[list[dict], list[dict]]:
    """确保 Snore 点位数量更丰富，且每个点位都有同分钟 Snore 音频。"""
    merged = []
    seen_time = set()
    for p in data_points or []:
        if not isinstance(p, dict):
            continue
        t = str(p.get("time") or "").strip()
        if not t or t in seen_time:
            continue
        seen_time.add(t)
        merged.append({"time": t, "value": int(p.get("value", 58) or 58)})

    if len(merged) < 8:
        for p in _build_segmented_snore_points(sleep_day, rd, min_points=8):
            t = p["time"]
            if t in seen_time:
                continue
            seen_time.add(t)
            merged.append(p)

    snore_times = {
        str(a.get("time") or "").strip()
        for a in (audios or [])
        if isinstance(a, dict) and (a.get("type") or "") == "Snore" and a.get("time")
    }
    out_audios = list(audios or [])
    for idx, p in enumerate(sorted(merged, key=lambda x: str(x.get("time") or ""))):
        t = str(p.get("time") or "").strip()
        if not t or t in snore_times:
            continue
        extra = _pick_snore_audio_by_key(audio_catalog, f"{rd}|{t}|{idx}")
        extra["time"] = t
        out_audios.append(extra)
        snore_times.add(t)

    return out_audios, sorted(merged, key=lambda x: str(x.get("time") or ""))


def _format_hhmm(dt_obj) -> str:
    return f"{dt_obj.hour:02d}:{dt_obj.minute:02d}"


def _build_segment_plan(sleep_day: dict, rd: str) -> list[dict]:
    """生成最多 2 段 Snore 计划：每段 40-90 分钟，段间隔 30-45 分钟。"""
    seeded = random.Random(hash(f"segment-plan|{rd}") & 0xFFFFFFFF)
    sleep_start, wake_end = sleep_local_window_bounds_from_sleep_data(sleep_day)

    if sleep_start and wake_end and wake_end > sleep_start:
        seg_window_start = sleep_start + timedelta(hours=1)
        seg_window_end = wake_end - timedelta(hours=2)
        if seg_window_end <= seg_window_start:
            seg_window_start = sleep_start
            seg_window_end = wake_end
    else:
        # 无法解析睡眠窗时，退化为固定夜间窗口
        base = __import__("datetime").datetime.strptime(rd, "%Y-%m-%d")
        seg_window_start = base.replace(hour=1, minute=0)
        seg_window_end = base.replace(hour=6, minute=0)

    available_min = int((seg_window_end - seg_window_start).total_seconds() // 60)
    if available_min <= 0:
        return []

    # 优先 2 段，不满足空间则降级 1 段
    can_fit_two = available_min >= (40 + 40 + 30)
    segment_count = 2 if can_fit_two and seeded.random() < 0.7 else 1

    if segment_count == 2:
        for _ in range(120):
            dur1 = seeded.randint(40, 90)
            dur2 = seeded.randint(40, 90)
            gap = seeded.randint(30, 45)
            total_span = dur1 + gap + dur2
            if total_span > available_min:
                continue
            start_offset = seeded.randint(0, available_min - total_span)
            s1 = seg_window_start + timedelta(minutes=start_offset)
            s2 = s1 + timedelta(minutes=dur1 + gap)
            return [
                {"start_dt": s1, "duration_min": dur1},
                {"start_dt": s2, "duration_min": dur2},
            ]
        segment_count = 1

    max_dur = min(90, available_min)
    min_dur = 40 if max_dur >= 40 else max(10, max_dur)
    dur = seeded.randint(min_dur, max_dur)
    start_offset = seeded.randint(0, max(0, available_min - dur))
    s1 = seg_window_start + timedelta(minutes=start_offset)
    return [{"start_dt": s1, "duration_min": dur}]


def _build_snore_points_from_segments(segments: list[dict], rd: str) -> list[dict]:
    """按段生成每分钟 data_points，且每段分贝从低到高（30-70）。"""
    seeded = random.Random(hash(f"segment-points|{rd}") & 0xFFFFFFFF)
    points = []
    for seg_idx, seg in enumerate(segments):
        start_dt = seg["start_dt"]
        duration_min = int(seg["duration_min"])
        low = seeded.randint(30, 45)
        high = seeded.randint(max(50, low + 2), 70)
        for i in range(duration_min):
            t = i / max(1, duration_min - 1)
            baseline = low + (high - low) * t
            jitter = seeded.randint(-1, 1)
            val = _clamp_int(baseline + jitter, 30, 70)
            if points and points[-1]["segment"] == seg_idx:
                val = max(points[-1]["value"], val)
            dt = start_dt + timedelta(minutes=i)
            points.append({"time": _format_hhmm(dt), "value": val, "segment": seg_idx})
    return [{"time": p["time"], "value": p["value"]} for p in points]


def _build_snore_audios_from_segments(segments: list[dict], rd: str, audio_catalog: dict) -> list[dict]:
    """每段生成 1 条 Snore 音频，time 为段起点，duration_sec 为整段秒数。"""
    out = []
    for idx, seg in enumerate(segments):
        start_dt = seg["start_dt"]
        duration_min = int(seg["duration_min"])
        audio = _pick_snore_audio_by_key(audio_catalog, f"snore-seg|{rd}|{idx}|{_format_hhmm(start_dt)}")
        audio["time"] = _format_hhmm(start_dt)
        audio["duration_sec"] = duration_min * 60
        audio["type"] = "Snore"
        out.append(audio)
    return out


def _load_json_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _atomic_write_json(path: Path, data: list) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _events_for_uid(events: list, uid: str) -> list:
    out = []
    for e in events:
        if not isinstance(e, dict):
            continue
        ev_uid = str(e.get("uid") or "").strip()
        if ev_uid and ev_uid != uid:
            continue
        out.append(e)
    return out


def _discover_uids(source_dir: Path) -> list[str]:
    """从 source_dir 下 *_health_data.json 推断全部 uid。"""
    paths = glob.glob(str(source_dir / "*_health_data.json"))
    out = []
    for p in paths:
        name = Path(p).name
        if name.endswith("_health_data.json"):
            out.append(name[: -len("_health_data.json")])
    return sorted(set(out))


def refresh_uid(
    uid: str,
    source_dir: Path,
    target_dir: Path,
    start_date: str | None,
    end_date: str | None,
) -> dict:
    health_path = source_dir / f"{uid}_health_data.json"
    events_path = source_dir / f"{uid}_sleep_events.json"

    health_rows = _load_json_list(health_path)
    all_events = _events_for_uid(_load_json_list(events_path), uid)

    if not health_rows:
        return {"uid": uid, "generated_days": 0, "skipped": "no_health_data"}

    health_by_date = {
        str(r.get("record_date") or ""): r for r in health_rows if isinstance(r, dict) and r.get("record_date")
    }
    audio_catalog = _load_audio_catalog()

    generated_rows = []
    for sleep_day in health_rows:
        if not isinstance(sleep_day, dict):
            continue
        rd = str(sleep_day.get("record_date") or "")
        if not rd:
            continue
        if start_date and rd < start_date:
            continue
        if end_date and rd > end_date:
            continue

        sleep_day = health_by_date.get(rd) or sleep_day
        sleep_start, window_end = sleep_local_window_bounds_from_sleep_data(sleep_day)
        selected_events = []
        if sleep_start and window_end:
            selected_events = collect_auditory_sleep_events_in_window(
                all_events,
                uid,
                sleep_start,
                window_end,
                session_record_date=rd,
            )

        # 非 Snore 音频仍沿用原事件逻辑；Snore 按“分段规则”独立生成。
        event_audios = build_audios_from_auditory_sleep_events(selected_events, rd)
        non_snore_audios = [a for a in event_audios if (a.get("type") or "") != "Snore"]

        snore_segments = _build_segment_plan(sleep_day, rd)
        if not snore_segments:
            # 最小兜底：至少 1 段 40 分钟
            fallback_time = _fallback_time_in_sleep_window(sleep_day, rd)
            from datetime import datetime

            base = datetime.strptime(f"{rd} {fallback_time}", "%Y-%m-%d %H:%M")
            snore_segments = [{"start_dt": base, "duration_min": 40}]

        snore_audios = _build_snore_audios_from_segments(snore_segments, rd, audio_catalog)
        data_points = _build_snore_points_from_segments(snore_segments, rd)
        audios = non_snore_audios + snore_audios

        apnea_count = _stable_apnea_count(uid, rd)
        if apnea_count >= 5:
            target = build_apnea_auditory_target_title(rd)
            risk_alert = f"昨晚出现{apnea_count}次呼吸暂停疑似时间，建议关注。"
        else:
            target = ""
            risk_alert = ""
        generated_rows.append(
            {
                "uid": uid,
                "record_date": rd,
                "auditory": {
                    "module": build_auditory_snore_module(audios, rd),
                    "target": target,
                    "risk_alert": risk_alert,
                    "snoring_analysis": {"data_points": data_points},
                    "audios": audios,
                },
            }
        )

    target_dir.mkdir(parents=True, exist_ok=True)
    target_report_path = target_dir / f"{uid}_sleep_auditory.json"
    _atomic_write_json(target_report_path, generated_rows)
    return {"uid": uid, "generated_days": len(generated_rows), "auditory_path": str(target_report_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="批量生成 auditory 数据并写入新目录")
    parser.add_argument("--uid", default="", help="指定单个用户 uid（不传则处理全部用户）")
    parser.add_argument("--source-dir", default="output", help="输入目录，默认 output")
    parser.add_argument(
        "--target-dir",
        default="output_regenerated_auditory",
        help="输出目录（新文件夹），默认 output_regenerated_auditory",
    )
    parser.add_argument("--start-date", default="", help="开始日期 YYYY-MM-DD（可选）")
    parser.add_argument("--end-date", default="", help="结束日期 YYYY-MM-DD（可选）")
    parser.add_argument("--seed", type=int, default=0, help="随机种子（可选）")
    args = parser.parse_args()

    if args.seed:
        random.seed(args.seed)

    source_dir = Path(args.source_dir).resolve()
    target_dir = Path(args.target_dir).resolve()
    uid = str(args.uid).strip()
    uids = [uid] if uid else _discover_uids(source_dir)

    results = []
    for one_uid in uids:
        results.append(
            refresh_uid(
                uid=one_uid,
                source_dir=source_dir,
                target_dir=target_dir,
                start_date=args.start_date.strip() or None,
                end_date=args.end_date.strip() or None,
            )
        )
    print(json.dumps({"source_dir": str(source_dir), "target_dir": str(target_dir), "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
