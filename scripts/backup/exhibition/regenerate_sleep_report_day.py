"""单日 sleep_report 整份重生成（非 LLM 骨架 + LLM 侧车 + write_back + hidden_discovery）。"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Optional

from scripts.backup.exhibition.shared import (
    assert_writable_work_dir,
    env_intervention_session_id,
    health_dates,
    index_by_record_date,
    list_persona_uids,
    load_json_list,
    save_json_list,
)
from scripts.backup.exhibition.ensure_health_window import (
    ensure_fourteen_day_health,
    load_persona,
)
from scripts.backup.exhibition.fill_gaps import (
    _build_hidden_discovery,
    _payload_anchor,
)
from scripts.backup.exhibition.shorten_discovery import shorten_sleep_report_file

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_ROOT = os.path.abspath(os.path.join(BACKUP_DIR, "..", ".."))
GEN_DATA_DIR = os.path.join(PROJECT_ROOT, "scripts", "generate_data")
WRITE_BACK_DIR = os.path.join(GEN_DATA_DIR, "write_back")

for _p in (PROJECT_ROOT, GEN_DATA_DIR, WRITE_BACK_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_ai.llm_resume import checkpoint_save, load_rows_by_date, merge_rows_sorted  # noqa: E402
from generate_ai.runtime import bootstrap_llm, load_health_rows  # noqa: E402
from generate_ai.generate_sleep_quality import generate_quality_for_uid  # noqa: E402
from generate_ai.generate_sleep_auditory import generate_auditory_for_uid  # noqa: E402
from generate_ai.generate_sleep_main_summary import generate_main_summary_for_uid  # noqa: E402
from generate_ai.generate_sleep_notice import generate_notice_for_uid  # noqa: E402
from generate_ai.generate_sleep_event_environment_intervention import (  # noqa: E402
    generate_intervention_for_uid,
)
from generate_ai.generate_sleep_pattern_commonality import (  # noqa: E402
    build_14d_payload,
    generate_commonality_for_date,
)
from generate_ai.generate_sleep_pattern_commonality_insight import (  # noqa: E402
    generate_insight_for_commonality,
)
from sleep_report.generate import (  # noqa: E402
    check_and_process_sleep_talk,
    generate_sleep_report,
)
from sleep_report.sleep_helpers import build_sleep_events_index  # noqa: E402
from write_back.write_back_llm_outputs import run_write_back_for_uid  # noqa: E402
from write_back.refresh_sleep_report_auditory_snoring import (  # noqa: E402
    refresh_sleep_report_auditory_snoring_for_uid,
)

DEFAULT_SLEEP_STANDARD = {
    "deep": [15, 25],
    "light": [45, 60],
    "rem": [20, 25],
    "awake": 10,
}

TARGET_DATE_DEFAULT = "2026-06-06"

SIDECAR_SUFFIXES = (
    "sleep_quality",
    "sleep_auditory",
    "sleep_main_summary",
    "sleep_notice",
    "sleep_event_environment_intervention",
    "sleep_pattern_commonality",
    "sleep_pattern_commonality_insight",
)


def _purge_sidecar_date(data_dir: str, uid: str, record_date: str, suffix: str) -> None:
    path = os.path.join(data_dir, f"{uid}_{suffix}.json")
    by_date = load_rows_by_date(path)
    if record_date not in by_date:
        return
    del by_date[record_date]
    checkpoint_save(path, merge_rows_sorted(by_date))


def _recent_titles_before(
    report_by_date: dict[str, dict],
    record_date: str,
    *,
    max_n: int = 2,
) -> list[str]:
    titles: list[str] = []
    for rd in sorted(report_by_date.keys()):
        if rd >= record_date:
            break
        row = report_by_date[rd]
        main = row.get("main") if isinstance(row.get("main"), dict) else {}
        title = str(main.get("title") or "").strip()
        if title:
            titles.append(title)
    return titles[-max_n:]


def _health_row_for_date(uid: str, data_dir: str, record_date: str) -> Optional[dict]:
    try:
        rows = load_health_rows(uid, data_dir)
    except FileNotFoundError:
        return None
    for row in rows:
        if str(row.get("record_date") or "") == record_date:
            return row
    return None


def _regenerate_base_row(
    uid: str,
    data_dir: str,
    record_date: str,
) -> dict[str, Any]:
    health_row = _health_row_for_date(uid, data_dir, record_date)
    if not health_row:
        raise ValueError(f"uid={uid} 无 {record_date} 的 health 行")

    persona = load_persona(uid)
    personality_type = str(persona.get("code") or "M-L-C").strip() or "M-L-C"
    sleep_events_index = build_sleep_events_index(uid, output_dir=data_dir)

    rep_path = os.path.join(data_dir, f"{uid}_sleep_report.json")
    report_rows = load_json_list(rep_path)
    report_by = index_by_record_date(report_rows)
    recent_titles = _recent_titles_before(report_by, record_date)

    new_row = generate_sleep_report(
        health_row,
        uid,
        "",
        DEFAULT_SLEEP_STANDARD,
        personality_type,
        sleep_events_index,
        recent_titles=recent_titles,
        output_dir=data_dir,
    )
    report_by[record_date] = new_row
    merged = sorted(report_by.values(), key=lambda r: str(r.get("record_date") or ""))
    merged = check_and_process_sleep_talk(merged)
    save_json_list(rep_path, merged)
    return {"record_date": record_date, "replaced": True}


def _regenerate_llm_sidecars(
    uid: str,
    data_dir: str,
    record_date: str,
    *,
    personality_type: str,
    retry_delay: float,
) -> dict[str, int]:
    for suffix in SIDECAR_SUFFIXES:
        _purge_sidecar_date(data_dir, uid, record_date, suffix)

    date_kw = dict(
        uid=uid,
        output_dir=data_dir,
        start_date=record_date,
        end_date=record_date,
        resume=True,
        retry_delay=retry_delay,
    )
    stats: dict[str, int] = {}
    stats["quality"] = len(generate_quality_for_uid(**date_kw))
    stats["auditory"] = len(generate_auditory_for_uid(**date_kw))
    stats["main_summary"] = len(generate_main_summary_for_uid(**date_kw))
    stats["notice"] = len(
        generate_notice_for_uid(**date_kw, personality_type=personality_type or None)
    )
    env_session_id = env_intervention_session_id(uid, record_date)
    env_kw = dict(date_kw)
    if env_session_id:
        env_kw["session_id_by_date"] = {record_date: env_session_id}
    stats["env_intervention"] = len(generate_intervention_for_uid(**env_kw))
    return stats


def _regenerate_hidden_discovery(
    uid: str,
    data_dir: str,
    record_date: str,
) -> dict[str, Any]:
    ensure_fourteen_day_health(uid, data_dir, record_date)
    health_rows = load_json_list(os.path.join(data_dir, f"{uid}_health_data.json"))
    payload_anchor = _payload_anchor(record_date, health_dates(uid, data_dir))
    try:
        build_14d_payload(uid, payload_anchor, data_dir, health_rows=health_rows)
    except ValueError as exc:
        return {"ok": False, "reason": f"14d payload 不足: {exc}"}

    commonality = generate_commonality_for_date(
        uid, payload_anchor, data_dir, health_rows=health_rows
    )
    if not commonality:
        return {"ok": False, "reason": "commonality 生成失败"}
    insight = generate_insight_for_commonality(commonality)
    if not insight:
        return {"ok": False, "reason": "insight 生成失败"}
    hd = _build_hidden_discovery(commonality, insight)
    if hd is None:
        return {"ok": False, "reason": "hidden_discovery 合并失败"}

    rep_path = os.path.join(data_dir, f"{uid}_sleep_report.json")
    report_rows = load_json_list(rep_path)
    report_by = index_by_record_date(report_rows)
    row = report_by.get(record_date)
    if not row:
        return {"ok": False, "reason": "sleep_report 无该日"}
    qa = row.get("quality_analysis")
    if not isinstance(qa, dict):
        qa = {}
        row["quality_analysis"] = qa
    qa["hidden_discovery"] = hd
    save_json_list(rep_path, sorted(report_by.values(), key=lambda r: str(r.get("record_date") or "")))
    return {"ok": True}


def regenerate_sleep_report_day_for_uid(
    uid: str,
    data_dir: str,
    record_date: str = TARGET_DATE_DEFAULT,
    *,
    retry_delay: float = 0.5,
    dry_run: bool = False,
    skip_llm: bool = False,
) -> dict[str, Any]:
    """重生成指定 uid 单日 sleep_report（含 LLM 写回）。"""
    if dry_run:
        health_ok = _health_row_for_date(uid, data_dir, record_date) is not None
        rep_path = os.path.join(data_dir, f"{uid}_sleep_report.json")
        return {
            "uid": uid,
            "record_date": record_date,
            "has_health": health_ok,
            "has_sleep_report_file": os.path.isfile(rep_path),
            "dry_run": True,
        }

    assert_writable_work_dir(data_dir)
    persona = load_persona(uid)
    personality_type = str(persona.get("code") or "M-L-C").strip() or "M-L-C"

    result: dict[str, Any] = {"uid": uid, "record_date": record_date}

    print(f"  [base] uid={uid} date={record_date} …")
    result["base"] = _regenerate_base_row(uid, data_dir, record_date)

    ref = refresh_sleep_report_auditory_snoring_for_uid(
        uid,
        data_dir,
        start_date=record_date,
        end_date=record_date,
        refresh_audios=True,
    )
    result["auditory_refresh"] = ref

    if skip_llm:
        result["llm_skipped"] = True
        return result

    bootstrap_llm()
    print(f"  [llm] uid={uid} date={record_date} …")
    result["llm_sidecars"] = _regenerate_llm_sidecars(
        uid,
        data_dir,
        record_date,
        personality_type=personality_type,
        retry_delay=retry_delay,
    )

    wb = run_write_back_for_uid(
        uid,
        data_dir,
        start_date=record_date,
        end_date=record_date,
    )
    result["write_back"] = wb

    print(f"  [hd] uid={uid} date={record_date} …")
    result["hidden_discovery"] = _regenerate_hidden_discovery(uid, data_dir, record_date)

    shorten_stats = shorten_sleep_report_file(
        os.path.join(data_dir, f"{uid}_sleep_report.json"),
        dry_run=False,
    )
    result["shorten"] = shorten_stats

    return result


def regenerate_sleep_report_day_for_all(
    data_dir: str,
    *,
    record_date: str = TARGET_DATE_DEFAULT,
    uid: str | None = None,
    retry_delay: float = 0.5,
    dry_run: bool = False,
    skip_llm: bool = False,
) -> list[dict[str, Any]]:
    results: list[dict] = []
    uids = list_persona_uids(data_dir, uid)
    for i, persona_uid in enumerate(uids):
        print(f"\n=== regenerate sleep_report {record_date} uid={persona_uid} ({i + 1}/{len(uids)}) ===")
        try:
            stats = regenerate_sleep_report_day_for_uid(
                persona_uid,
                data_dir,
                record_date,
                retry_delay=retry_delay,
                dry_run=dry_run,
                skip_llm=skip_llm,
            )
        except Exception as exc:
            stats = {"uid": persona_uid, "record_date": record_date, "error": str(exc)}
            print(f"  [错误] {exc}")
        results.append(stats)
        if not dry_run and not skip_llm and i < len(uids) - 1 and retry_delay > 0:
            time.sleep(retry_delay)
    return results
