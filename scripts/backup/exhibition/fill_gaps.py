"""为展会备份补齐 ai_analysis_14d 与 sleep_report.hidden_discovery（调用 LLM）。"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Optional

from scripts.backup.exhibition.shared import (
    DATE_RANGE_END,
    DATE_RANGE_START,
    PROMPT_MORNING_ALARM,
    PROMPT_SLEEP_TREND_14D,
    ai_analysis_dates,
    assert_writable_work_dir,
    ensure_morning_aux_files,
    health_dates,
    hidden_discovery_dates,
    index_by_record_date,
    list_persona_uids,
    load_json_list,
    missing_dates_in_range,
    report_dates,
    report_dates_in_range,
    resolve_path,
    save_json_list,
    LATEST_AUX_REL,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT = os.path.abspath(os.path.join(BACKUP_DIR, "..", ".."))
GEN_DATA_DIR = os.path.join(PROJECT_ROOT, "scripts", "generate_data")
WRITE_BACK_DIR = os.path.join(GEN_DATA_DIR, "write_back")

for _p in (PROJECT_ROOT, GEN_DATA_DIR, WRITE_BACK_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_ai.runtime import bootstrap_llm  # noqa: E402
from generate_ai.trend_14d_analysis import generate_ai_analysis  # noqa: E402
from generate_ai.generate_ai_analysis_14d import (  # noqa: E402
    _build_schedule_records_for_date,
    _load_calendar_events,
    _load_daily_emotion_steps_rows,
    _load_weather_by_date,
    _today_health_for_date,
)
from generate_ai.generate_sleep_pattern_commonality import (  # noqa: E402
    build_14d_payload,
    generate_commonality_for_date,
)
from generate_ai.generate_sleep_pattern_commonality_insight import (  # noqa: E402
    generate_insight_for_commonality,
)
from generate_ai.generate_morning_timeline_alarm_context_advisory import (  # noqa: E402
    generate_alarm_insight_for_uid,
)
from merge_fusion_insight import merge_fusion_insight_from_morning  # noqa: E402
from scripts.patch_sleep_report_discover_icons import (  # noqa: E402
    ICON_BY_TYPE,
    MODULE_FIELD_ORDER,
    MODULE_ICON_URL,
    _ordered_discover_item,
    _ordered_item,
)
from scripts.backup.exhibition.ensure_health_window import (  # noqa: E402
    collect_missing_for_anchors,
    ensure_fourteen_day_health,
    ensure_health_for_anchors,
)
from scripts.backup.exhibition.shorten_discovery import (  # noqa: E402
    shorten_discover_content,
    shorten_discover_title,
)


def _payload_anchor(anchor: str, hdates: set[str]) -> str:
    return anchor


def _build_hidden_discovery(
    commonality: list[dict[str, Any]],
    insight: dict[str, Any],
) -> Optional[dict[str, Any]]:
    tgt = shorten_discover_title(str(insight.get("target") or ""))
    desc = shorten_discover_content(str(insight.get("description") or ""))
    if not (tgt and desc):
        return None
    module = [_ordered_item({"target": tgt, "description": desc}, MODULE_FIELD_ORDER, MODULE_ICON_URL)]
    discover: list[dict[str, Any]] = []
    for block in commonality:
        if not isinstance(block, dict):
            continue
        metric_type = str(block.get("type") or "").strip()
        title = shorten_discover_title(str(block.get("highlight") or ""))
        content = shorten_discover_content(str(block.get("analysis") or ""))
        raw_list = block.get("list")
        lst = list(raw_list) if isinstance(raw_list, list) else []
        icon = ICON_BY_TYPE.get(metric_type, "")
        item = {"title": title, "content": content, "confidence": "", "type": metric_type, "list": lst}
        discover.append(_ordered_discover_item(item, icon) if icon else item)
    if not discover:
        return None
    return {"module": module, "discover": discover}


def _upsert_ai_row(rows: list[dict], by_date: dict[str, dict], row: dict) -> list[dict]:
    rd = str(row.get("record_date") or "")
    if not rd:
        return rows
    by_date[rd] = row
    return sorted(by_date.values(), key=lambda x: str(x.get("record_date") or ""))


def _run_ai_trend_for_dates(
    uid: str,
    data_dir: str,
    target_dates: list[str],
    *,
    weather_json: str | None,
    retry_delay: float,
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        missing = collect_missing_for_anchors(uid, data_dir, target_dates)
        return {
            "uid": uid,
            "target_ai_dates": target_dates,
            "health_would_fabricate": missing,
            "dry_run": True,
        }

    assert_writable_work_dir(data_dir)

    if not os.path.isfile(PROMPT_SLEEP_TREND_14D):
        raise FileNotFoundError(f"提示词不存在: {PROMPT_SLEEP_TREND_14D}")
    os.environ["SLEEP_TREND_14D_PROMPT"] = os.path.basename(PROMPT_SLEEP_TREND_14D)

    fabricated = ensure_health_for_anchors(uid, data_dir, target_dates)
    if fabricated:
        print(f"  [health] main.py 同款造数 {len(fabricated)} 天（{fabricated[0]}…{fabricated[-1]}）")

    weather_path = weather_json or os.path.join(PROJECT_ROOT, "output", "qweather_monthly_data.json")
    weather_by_date = _load_weather_by_date(weather_path)
    calendar_events = _load_calendar_events(uid, data_dir)
    emotion_rows = _load_daily_emotion_steps_rows(uid, data_dir)

    out_path = os.path.join(data_dir, f"{uid}_ai_analysis_14d.json")
    rows = load_json_list(out_path)
    by_date = index_by_record_date(rows)
    generated: list[str] = []
    skipped: list[str] = []

    import generate_ai.trend_14d_analysis as trend_mod

    original_compact = trend_mod.compact_schedule_records_for_trend_14d_prompt

    for i, target_date in enumerate(target_dates):
        print(f"  [ai] {i + 1}/{len(target_dates)} uid={uid} date={target_date} …", end=" ", flush=True)
        ensure_fourteen_day_health(uid, data_dir, target_date)

        today_health = _today_health_for_date(emotion_rows, target_date)
        if today_health is None:
            print("跳过（无 steps/score）")
            skipped.append(target_date)
            continue
        today_weather = weather_by_date.get(target_date) or {}
        if not today_weather:
            print("跳过（无天气）")
            skipped.append(target_date)
            continue

        injected = _build_schedule_records_for_date(calendar_events, target_date)

        def _patched_compact(_sched_seg, _inj=injected):
            return _inj if _inj else original_compact(_sched_seg)

        trend_mod.compact_schedule_records_for_trend_14d_prompt = _patched_compact
        try:
            new_rows = generate_ai_analysis(
                uid,
                use_doubao=True,
                output_dir=data_dir,
                start_date=target_date,
                end_date=target_date,
                today_health=today_health,
                today_weather=today_weather,
            )
        finally:
            trend_mod.compact_schedule_records_for_trend_14d_prompt = original_compact

        if not new_rows:
            print("失败")
            skipped.append(target_date)
        else:
            print("完成")
            for row in new_rows:
                if isinstance(row, dict) and row.get("record_date"):
                    rows = _upsert_ai_row(rows, by_date, row)
                    generated.append(str(row.get("record_date")))
            save_json_list(out_path, rows)
        if i < len(target_dates) - 1:
            time.sleep(retry_delay)

    return {"uid": uid, "generated": generated, "skipped": skipped}


def generate_ai_trend_only_for_uid(
    uid: str,
    data_dir: str,
    *,
    start_date: str,
    end_date: str,
    weather_json: str | None = None,
    retry_delay: float = 0.5,
    dry_run: bool = False,
) -> dict[str, Any]:
    """仅生成/覆盖 ai_analysis_14d（sleep_trend_14d），不跑 morning/fusion，不改 sleep_report。"""
    assert_writable_work_dir(data_dir)
    target_dates = report_dates_in_range(uid, data_dir, start_date, end_date)
    if not dry_run:
        bootstrap_llm()
    return _run_ai_trend_for_dates(
        uid,
        data_dir,
        target_dates,
        weather_json=weather_json,
        retry_delay=retry_delay,
        dry_run=dry_run,
    )


def fill_ai_analysis_for_uid(
    uid: str,
    data_dir: str,
    *,
    start_date: str = DATE_RANGE_START,
    end_date: str = DATE_RANGE_END,
    weather_json: str | None = None,
    retry_delay: float = 0.5,
    dry_run: bool = False,
) -> dict[str, Any]:
    existing = ai_analysis_dates(uid, data_dir)
    rdates = report_dates(uid, data_dir)
    missing = [
        d
        for d in missing_dates_in_range(existing, start_date, end_date)
        if d in rdates
    ]
    return _run_ai_trend_for_dates(
        uid,
        data_dir,
        missing,
        weather_json=weather_json,
        retry_delay=retry_delay,
        dry_run=dry_run,
    )


def regenerate_ai_analysis_for_uid(
    uid: str,
    data_dir: str,
    *,
    start_date: str = DATE_RANGE_START,
    end_date: str = DATE_RANGE_END,
    weather_json: str | None = None,
    retry_delay: float = 0.5,
    dry_run: bool = False,
    aux_dir: str | None = None,
) -> dict[str, Any]:
    """全量重生成 ai_analysis_14d（sleep_trend_14d）+ morning_alarm + fusion_insight。"""
    target_dates = report_dates_in_range(uid, data_dir, start_date, end_date)
    if dry_run:
        src_root = resolve_path(aux_dir, LATEST_AUX_REL)
        would_copy: dict[str, str] = {}
        for suffix in ("weather", "traffic_link_realtime"):
            fname = f"{uid}_{suffix}.json"
            dst = os.path.join(data_dir, fname)
            if os.path.isfile(dst):
                continue
            src = os.path.join(src_root, fname)
            if os.path.isfile(src):
                would_copy[suffix] = src
        return {
            "uid": uid,
            "target_ai_dates": target_dates,
            "prompts": {
                "sleep_trend_14d": PROMPT_SLEEP_TREND_14D,
                "morning_alarm": PROMPT_MORNING_ALARM,
            },
            "aux_would_copy_from": would_copy,
            "health_would_fabricate": collect_missing_for_anchors(uid, data_dir, target_dates),
            "dry_run": True,
        }

    if not os.path.isfile(PROMPT_MORNING_ALARM):
        raise FileNotFoundError(f"提示词不存在: {PROMPT_MORNING_ALARM}")

    assert_writable_work_dir(data_dir)

    aux = ensure_morning_aux_files(uid, data_dir, aux_dir=aux_dir)
    if aux:
        print(f"  [aux] 已复制: {aux}")

    trend_stats = _run_ai_trend_for_dates(
        uid,
        data_dir,
        target_dates,
        weather_json=weather_json,
        retry_delay=retry_delay,
        dry_run=False,
    )

    print(f"  [morning] uid={uid} 生成 alarm_insight（{PROMPT_MORNING_ALARM}）…")
    morning_rows, morning_skipped = generate_alarm_insight_for_uid(
        uid,
        data_dir,
        start_date=start_date,
        end_date=end_date,
        system_prompt_path=PROMPT_MORNING_ALARM,
        retry_delay=retry_delay,
        resume=False,
    )

    fusion_written, fusion_skipped = merge_fusion_insight_from_morning(
        uid,
        data_dir,
        start_date=start_date,
        end_date=end_date,
    )

    return {
        "uid": uid,
        "target_dates": len(target_dates),
        "trend": trend_stats,
        "morning_generated": len(morning_rows),
        "morning_last_skipped": morning_skipped,
        "fusion_written": fusion_written,
        "fusion_skipped_no_ai_row": fusion_skipped,
    }


def regenerate_ai_for_all(
    data_dir: str,
    *,
    uid: str | None = None,
    start_date: str = DATE_RANGE_START,
    end_date: str = DATE_RANGE_END,
    weather_json: str | None = None,
    retry_delay: float = 0.5,
    dry_run: bool = False,
    aux_dir: str | None = None,
) -> list[dict[str, Any]]:
    if not dry_run:
        bootstrap_llm()
    results: list[dict] = []
    for persona_uid in list_persona_uids(data_dir, uid):
        print(f"\n=== regenerate-ai uid={persona_uid} ===")
        results.append(
            regenerate_ai_analysis_for_uid(
                persona_uid,
                data_dir,
                start_date=start_date,
                end_date=end_date,
                weather_json=weather_json,
                retry_delay=retry_delay,
                dry_run=dry_run,
                aux_dir=aux_dir,
            )
        )
    return results


def fill_hidden_discovery_for_uid(
    uid: str,
    data_dir: str,
    *,
    start_date: str = DATE_RANGE_START,
    end_date: str = DATE_RANGE_END,
    retry_delay: float = 0.5,
    dry_run: bool = False,
) -> dict[str, Any]:
    rdates = report_dates(uid, data_dir)
    existing = hidden_discovery_dates(uid, data_dir)
    missing = [
        d
        for d in missing_dates_in_range(existing, start_date, end_date)
        if d in rdates
    ]
    if dry_run:
        return {
            "uid": uid,
            "missing_hidden_discovery_dates": missing,
            "health_would_fabricate": collect_missing_for_anchors(uid, data_dir, missing),
            "dry_run": True,
        }

    assert_writable_work_dir(data_dir)

    if missing:
        fabricated = ensure_health_for_anchors(uid, data_dir, missing)
        if fabricated:
            print(f"  [health] main.py 同款造数 {len(fabricated)} 天")

    rep_path = os.path.join(data_dir, f"{uid}_sleep_report.json")
    report_rows = load_json_list(rep_path)
    rep_by = index_by_record_date(report_rows)
    health_rows = load_json_list(os.path.join(data_dir, f"{uid}_health_data.json"))
    generated: list[str] = []
    skipped: list[str] = []

    for i, target_date in enumerate(missing):
        print(f"  [hd] {i + 1}/{len(missing)} uid={uid} date={target_date} …", end=" ", flush=True)
        ensure_fourteen_day_health(uid, data_dir, target_date)
        health_rows = load_json_list(os.path.join(data_dir, f"{uid}_health_data.json"))
        payload_anchor = _payload_anchor(target_date, health_dates(uid, data_dir))
        try:
            build_14d_payload(uid, payload_anchor, data_dir, health_rows=health_rows)
        except ValueError:
            print("跳过（14天 payload 不足）")
            skipped.append(target_date)
            continue

        commonality = generate_commonality_for_date(
            uid, payload_anchor, data_dir, health_rows=health_rows
        )
        if not commonality:
            print("失败（commonality）")
            skipped.append(target_date)
            continue
        insight = generate_insight_for_commonality(commonality)
        if not insight:
            print("失败（insight）")
            skipped.append(target_date)
            continue
        hd = _build_hidden_discovery(commonality, insight)
        if hd is None:
            print("失败（merge）")
            skipped.append(target_date)
            continue

        row = rep_by.get(target_date)
        if not row:
            skipped.append(target_date)
            continue
        qa = row.get("quality_analysis")
        if not isinstance(qa, dict):
            qa = {}
            row["quality_analysis"] = qa
        qa["hidden_discovery"] = hd
        generated.append(target_date)
        print("完成")
        save_json_list(rep_path, report_rows)
        if i < len(missing) - 1:
            time.sleep(retry_delay)

    return {"uid": uid, "generated": generated, "skipped": skipped}


def fill_gaps_for_all(
    data_dir: str,
    *,
    uid: str | None = None,
    start_date: str = DATE_RANGE_START,
    end_date: str = DATE_RANGE_END,
    weather_json: str | None = None,
    skip_ai: bool = False,
    skip_discovery: bool = False,
    retry_delay: float = 0.5,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    if not dry_run:
        bootstrap_llm()
    results: list[dict] = []
    for persona_uid in list_persona_uids(data_dir, uid):
        print(f"\n=== uid={persona_uid} ===")
        if not skip_ai:
            results.append(
                fill_ai_analysis_for_uid(
                    persona_uid,
                    data_dir,
                    start_date=start_date,
                    end_date=end_date,
                    weather_json=weather_json,
                    retry_delay=retry_delay,
                    dry_run=dry_run,
                )
            )
        if not skip_discovery:
            results.append(
                fill_hidden_discovery_for_uid(
                    persona_uid,
                    data_dir,
                    start_date=start_date,
                    end_date=end_date,
                    retry_delay=retry_delay,
                    dry_run=dry_run,
                )
            )
    return results
