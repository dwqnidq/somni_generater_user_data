"""
根据 output/schedules_report.md 中八人格两天日程模板，生成每位用户的日历事件数据。
- 模板日：2026-05-29（第 1 天）、2026-05-30（第 2 天），在 date_range 内按日交替循环。
- 日期范围取自 health_data_personas_config.json 中该人格的 date_range（end 含当天），
  与 generate_persona_health_data 一致；可选 start_date_override / end_date_override 再收窄。
- 范围内每个自然日都会生成日程（两天模板交替）；使用模板中的时间段（不再随机改间隔）。
- 输出到 output/{user_id}_calendar_events.json
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta
from typing import Any

from schedules_report_md import (
    DEFAULT_SCHEDULES_REPORT_MD,
    load_two_day_templates,
)

CREATE_TIME = "2026-05-06T06:37:00.000Z"


def _effective_calendar_range(
    persona: dict[str, Any],
    start_override: date | None,
    end_override: date | None,
) -> tuple[date, date] | None:
    """与 generate_persona_health_data 相同：配置区间 ∩ CLI 覆盖；无效则 None。"""
    dr = persona.get("date_range") or {}
    start_s, end_s = dr.get("start"), dr.get("end")
    if not start_s or not end_s:
        return None
    config_start = datetime.strptime(str(start_s), "%Y-%m-%d").date()
    config_end = datetime.strptime(str(end_s), "%Y-%m-%d").date()
    from date_range_helpers import apply_date_range_overrides  # noqa: WPS433

    return apply_date_range_overrides(
        config_start, config_end, start_override, end_override
    )


def parse_time(t: str) -> tuple[int, int]:
    """解析 'HH:MM' 为 (hour, minute)。"""
    h, m = t.split(":")
    return int(h), int(m)


def calc_duration(start: str, end: str) -> int:
    """计算时长（分钟），支持跨午夜。"""
    sh, sm = parse_time(start)
    eh, em = parse_time(end)
    s_total = sh * 60 + sm
    e_total = eh * 60 + em
    if e_total <= s_total:
        e_total += 24 * 60
    return e_total - s_total


def _extract_sleep_window(
    persona_cfg: dict[str, Any],
) -> tuple[int, int] | None:
    states = persona_cfg.get("sleep_metric_states") or {}
    state = states.get("good") or (next(iter(states.values()), None) if states else None)
    if not state:
        return None
    bed_range = state.get("bed_time_range")
    wake_range = state.get("wake_up_time_range")
    if not bed_range or not wake_range:
        return None
    sleep_start_h, sleep_start_m = parse_time(bed_range[0])
    sleep_end_h, sleep_end_m = parse_time(wake_range[1])
    return sleep_start_h * 60 + sleep_start_m, sleep_end_h * 60 + sleep_end_m


def _overlaps_sleep_window(
    ev_start_min: int,
    ev_end_min: int,
    sleep_start_min: int,
    sleep_end_min: int,
) -> bool:
    def _check_segment(ss: int, se: int) -> bool:
        if sleep_start_min <= sleep_end_min:
            return ss < sleep_end_min and se > sleep_start_min
        in_late = ss < 1440 and se > sleep_start_min
        in_early = ss < sleep_end_min and se > 0
        return in_late or in_early

    s1 = ev_start_min % 1440
    e1 = ev_end_min % 1440
    if e1 == 0:
        e1 = 1440
    if _check_segment(s1, e1):
        return True
    if ev_end_min > 1440:
        if _check_segment(0, ev_end_min - 1440):
            return True
    return False


def _event_minutes(ev: dict[str, str]) -> tuple[int, int]:
    sh, sm = parse_time(ev["start"])
    eh, em = parse_time(ev["end"])
    s_total = sh * 60 + sm
    e_total = eh * 60 + em
    if e_total <= s_total:
        e_total += 24 * 60
    return s_total, e_total


def _should_skip_event(
    ev: dict[str, str],
    sleep_window: tuple[int, int] | None,
) -> bool:
    s_total, e_total = _event_minutes(ev)
    if sleep_window and _overlaps_sleep_window(
        s_total, e_total, sleep_window[0], sleep_window[1]
    ):
        return True
    ev_start_h, _ = parse_time(ev["start"])
    ev_end_h, _ = parse_time(ev["end"])
    if ev_start_h >= 23 or ev_end_h >= 23 or ev_end_h < ev_start_h:
        return True
    return False


def _build_events_for_uid(
    uid: str,
    days: list[list[dict[str, Any]]],
    range_start: date,
    range_end: date,
    persona_cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    sleep_window: tuple[int, int] | None = None
    if persona_cfg:
        sleep_window = _extract_sleep_window(persona_cfg)

    offset = 0
    cur = range_start
    while cur <= range_end:
        date_str = cur.strftime("%Y-%m-%d")
        day_schedule = days[offset % 2]
        offset += 1
        for ev in day_schedule:
            if _should_skip_event(ev, sleep_window):
                continue
            events.append(
                {
                    "uid": uid,
                    "event_date": date_str,
                    "event_type": ev["type"],
                    "event_name": ev["name"],
                    "start_time": ev["start"],
                    "end_time": ev["end"],
                    "duration_minutes": calc_duration(ev["start"], ev["end"]),
                    "create_time": CREATE_TIME,
                    "update_time": CREATE_TIME,
                    "language": "zh",
                }
            )
        cur += timedelta(days=1)
    return events


def generate_calendar_events_for_personas(
    personas: list[dict[str, Any]],
    output_dir: str,
    *,
    start_date_override: date | None = None,
    end_date_override: date | None = None,
    overwrite: bool = False,
    schedules_md_path: str | None = None,
) -> None:
    """为 personas 中在 schedules_report.md 有模板的 user_id 写入日历 JSON。"""
    templates = load_two_day_templates(schedules_md_path)
    uids = {str(p.get("user_id")) for p in personas if p.get("user_id")}
    by_uid: dict[str, dict[str, Any]] = {
        str(p["user_id"]): p for p in personas if p.get("user_id")
    }
    os.makedirs(output_dir, exist_ok=True)

    for uid, info in templates.items():
        if uid not in uids:
            continue
        persona_cfg = by_uid.get(uid)
        if not persona_cfg:
            continue
        persona_name = info.get("name", uid)
        span = _effective_calendar_range(
            persona_cfg, start_date_override, end_date_override
        )
        if not span:
            print(
                f"[跳过] {persona_name} ({uid})：无有效 date_range 或与 "
                f"--start-date/--end-date 交集为空"
            )
            continue
        range_start, range_end = span
        out_path = os.path.join(output_dir, f"{uid}_calendar_events.json")
        if (not overwrite) and os.path.isfile(out_path):
            print(f"[跳过] {persona_name} ({uid}) 已存在：{out_path}")
            continue

        days = info["days"]
        events = _build_events_for_uid(
            uid, days, range_start, range_end, persona_cfg=persona_cfg
        )
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
        print(
            f"✓ {persona_name} ({uid})  {range_start}～{range_end}  "
            f"→  {len(events)} 条事件  →  {out_path}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default=None,
        help="输出目录（默认仓库根下 output/）",
    )
    parser.add_argument(
        "--schedules-md",
        default=None,
        help=f"日程模板 Markdown（默认 {DEFAULT_SCHEDULES_REPORT_MD}）",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="若输出已存在则跳过（默认与旧版一致：直接覆盖写入）",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="health_data_personas_config.json（默认仓库 config/ 下该文件）",
    )
    parser.add_argument("--start-date", default=None, help="覆盖起始 YYYY-MM-DD（与配置求交）")
    parser.add_argument("--end-date", default=None, help="覆盖结束 YYYY-MM-DD（与配置求交）")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = args.output_dir or os.path.join(script_dir, "..", "..", "output")
    output_dir = os.path.normpath(output_dir)
    cfg_path = args.config or os.path.normpath(
        os.path.join(script_dir, "..", "..", "config", "health_data_personas_config.json")
    )
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    personas = cfg.get("personas") or []
    start_o = datetime.strptime(args.start_date, "%Y-%m-%d").date() if args.start_date else None
    end_o = datetime.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else None
    generate_calendar_events_for_personas(
        personas,
        output_dir,
        start_date_override=start_o,
        end_date_override=end_o,
        overwrite=not args.skip_existing,
        schedules_md_path=args.schedules_md,
    )


if __name__ == "__main__":
    main()
