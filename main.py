#!/usr/bin/env python3
"""一键按人格生成数据（顺序固定）。

生成顺序：
  0a. （可选 --with-virtual-map-data / --with-sleep-map-pool）虚拟用户睡眠地图排行池（不写每人 health）：
      generate_sleep_map_pool → output/somni_sleep_analysis.json + somni_sleep_district.json
  0b. （可选 --with-heatmap-users）虚拟用户热力图得分（不写每人 health 到 output 根目录）：
      generate_heatmap_users → output/virtual_heatmap/{uid}_sleep_map_score.json + heatmap_users_index.json
  人格主流程（步骤 1～7 等）仅针对 config 中 8 个人格的 user_id，不会对虚拟池/热力图用户跑 health。
  0. （非 --llm-only）和风今日天气快照（output/qweather_today_snapshot.json）→ 各用户天气结构（{uid}_weather.json，
     generate_user_weather_snapshot_json）→ 北京 Link 路况（{uid}_traffic_link_realtime.json）
     → 日历事件（{uid}_calendar_events.json，日期=人格 date_range ∩ --start-date/--end-date）
     → 每日情绪与步数（{uid}_daily_emotion_steps.json）
  1. 睡眠健康数据（health_data）
  2. 环境数据（environment_data）
  3. 体征数据（vitals_data）
  4. 睡眠事件（sleep_events）
  5. 事件影响回写（events -> environment/vitals；成功后在 output 写入空标记 {uid}_event_impacts_applied，
     下次无 --overwrite 时若标记存在则跳过本步）
  6. 睡眠地图逐日分析（{uid}_somni_sleep_analysis.json，非 LLM，跑完第 5 步即写）
     随后写睡眠艺术（{uid}_sleep_art_data.json，依赖 health_data；--with-llm 时位于 LLM 链之前）
  7. 睡眠报告（{uid}_sleep_report.json，非 LLM，依赖 health_data / sleep_events / environment_data；
     写盘后补全 auditory（重建 audios → 同分钟合并 → 相邻分钟再合并并累加 duration_sec；data_points 仍按分钟独立生成））
  8～18. 以下均需 --with-llm（每用户各整批一次，写入 output/{uid}_*.json）：
      8. 近14天趋势 AI 分析（ai_analysis_14d，向前 14 日 health 不齐则跳过该日）
      9. 晨间闹钟上下文洞察（morning_alarm_insight；无明日日程/无日程文件仍调用 LLM，仅结合天气与路况）
      10. 近14天睡眠共性（sleep_pattern_commonality）
      11. 睡眠共性洞察汇总（sleep_pattern_commonality_insight）
      12. 睡眠事件 AI 干预重写（sleep_ai_intervention，起始日同上）
      13. 睡眠地图排名原因（sleep_map_ranking_reason，起始日同上；写入 somni_sleep_analysis.evaluation）
      14. 睡眠质量模块（sleep_quality，起始日同上）
      15. 睡眠听觉模块（sleep_auditory，起始日同上）
      16. 睡眠主摘要（sleep_main_summary，起始日同上）
      17. 睡眠 notice（sleep_notice，起始日同上）
      18. 睡眠事件环境干预分析（sleep_event_environment_intervention，起始日同上）
      19. LLM 写回（write_back：fusion / hidden_discovery / sleep_events.detail / quality / auditory / main.summary / notice / env / ranking_reason；
          末尾合并 auditory.audios 同分钟 Snore，并按 sleep_events.noise_db 刷新按分钟 data_points；
          受 --start-date/--end-date 与 LLM 相同的 record_date 闭区间过滤）
      20. （可选 --prune-after-llm-skips）按跳过边界裁剪 LLM 侧车 JSON；默认不裁剪，避免误删已生成记录

  8～18. 可选 --llm-max-records N：各逐日 LLM 步在 --start-date/--end-date 过滤后最多处理 N 个锚点
          （含跳过尝试）；步骤 19 写回不受此参数限制。

  其中 10（sleep_pattern_commonality）锚点及向前 14 日 health 不齐则跳过；11～18 在 start/end 内
  全量调用；步骤 8（ai_analysis_14d）、9（morning）保留各自缺数/失败跳过。

  依赖 config/health_data_personas_config.json；输出在 output/ 目录。

用法：
  python main.py
  python main.py --user-id 69aea6d8af5e6cbf08027966 --overwrite
  python main.py --state good --start-date 2026-03-01 --end-date 2026-03-15
  # 仅生成指定用户，并同时生成 LLM 共性分析：
  python main.py --user-id 69aea6d8af5e6cbf08027966 --with-llm
  # 仅跑全部 LLM 步骤（跳过天气/路况/日历/每日情绪、前 6 步；LLM 前仍会从已有 health 尝试写 sleep_art）：
  python main.py --user-id 69aea6d8af5e6cbf08027966 --with-llm --llm-only
  # 限制各逐日 LLM 在日期过滤后最多处理 3 个锚点（用于试跑省调用）：
  python main.py --user-id 69aea6d8af5e6cbf08027966 --with-llm --llm-max-records 3
  # API 配额中途耗尽：直接再次运行相同命令（默认续跑，跳过 output 中已有日期；勿加 --overwrite）
  # 仅把 output 里已有 LLM 产物写回（不跑生成；可选 --start-date/--end-date）：
  python main.py --write-back-only
  python main.py --write-back-only --user-id 69aea6d8af5e6cbf08027966 --start-date 2026-05-01
  # 前置仅生成虚拟用户排行池（不写 8 人格以外的 health）：
  python main.py --with-virtual-map-data --start-date 2026-04-01 --end-date 2026-05-31
  # 前置再生成热力图得分（可选，默认 100 个虚拟用户仅落盘 sleep_map_score）：
  python main.py --with-heatmap-users --heatmap-count 50 --start-date 2026-04-01 --end-date 2026-05-14
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_GEN = os.path.join(ROOT, "scripts", "generate_data")
_SCRIPTS_GEN_AI = os.path.join(_SCRIPTS_GEN, "generate_ai")
for _p in (ROOT, _SCRIPTS_GEN, _SCRIPTS_GEN_AI):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.chdir(ROOT)

from generate_environment_data_by_persona import generate_environment_for_persona  # noqa: E402
from generate_health_data_by_persona_config import generate_persona_health_data  # noqa: E402
from apply_event_impacts_by_persona import apply_event_impacts_for_persona  # noqa: E402
from generate_sleep_events_by_persona import generate_sleep_events_for_persona  # noqa: E402
from generate_vitals_data_by_persona import generate_vitals_for_persona  # noqa: E402
from generate_sleep_pattern_commonality import (  # noqa: E402
    _coerce_commonality_metric_list,
    generate_for_uid as generate_sleep_pattern_commonality_for_uid,
)
from generate_sleep_pattern_commonality_insight import generate_insight_for_uid as generate_commonality_insight_for_uid  # noqa: E402
from generate_sleep_ai_intervention import generate_ai_intervention_for_uid  # noqa: E402
from generate_ai_analysis_14d import generate_ai_analysis_14d_for_uid  # noqa: E402
from generate_sleep_auditory import generate_auditory_for_uid  # noqa: E402
from generate_sleep_quality import generate_quality_for_uid  # noqa: E402
from generate_sleep_main_summary import generate_main_summary_for_uid  # noqa: E402
from generate_sleep_notice import generate_notice_for_uid  # noqa: E402
from generate_sleep_event_environment_intervention import generate_intervention_for_uid  # noqa: E402
from generate_sleep_map_ranking_reason import generate_ranking_reason_for_uid  # noqa: E402
from generate_morning_timeline_alarm_context_advisory import generate_alarm_insight_for_uid  # noqa: E402
from multi_day_llm_helpers import effective_start_after_skips  # noqa: E402
from prune_output_by_llm_skip_boundary import (  # noqa: E402
    compute_keep_from_date,
    prune_uid_output_files,
)
from generate_somni_sleep_analysis_from_health import write_somni_sleep_analysis_for_uid  # noqa: E402
from utils import fetch_qweather_monthly_data  # noqa: E402
from generate_user_weather_snapshot_json import generate_weather_snapshots_for_personas  # noqa: E402
from generate_beijing_link_traffic_data import generate_traffic_link_realtime_for_personas  # noqa: E402
from generate_calendar_events import generate_calendar_events_for_personas  # noqa: E402
from generate_daily_emotion_steps_data import generate_daily_emotion_steps_for_personas  # noqa: E402
from generate_sleep_art_data import write_sleep_art_data_for_persona  # noqa: E402
from sleep_report.generate import (  # noqa: E402
    generate_sleep_report as _gen_sleep_report_single,
    check_and_process_sleep_talk,
)
from sleep_report.sleep_helpers import build_sleep_events_index  # noqa: E402

DEFAULT_CONFIG = os.path.join(ROOT, "config", "health_data_personas_config.json")
_SLEEP_MAP_POOL_SCRIPT = os.path.join(_SCRIPTS_GEN, "generate_sleep_map_pool.py")
_HEATMAP_USERS_SCRIPT = os.path.join(_SCRIPTS_GEN, "generate_heatmap_users.py")
_DEFAULT_MAP_POOL_START = "2026-01-01"
_DEFAULT_MAP_POOL_END = "2026-03-31"
_DEFAULT_HEATMAP_START = "2026-03-01"
_DEFAULT_HEATMAP_DAYS = 14


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


def _resolve_map_pool_dates(
    start_date: date | None, end_date: date | None
) -> tuple[str, str]:
    start_s = start_date.isoformat() if start_date else _DEFAULT_MAP_POOL_START
    end_s = end_date.isoformat() if end_date else _DEFAULT_MAP_POOL_END
    return start_s, end_s


def _resolve_heatmap_params(
    start_date: date | None, end_date: date | None, days_override: int | None
) -> tuple[str, int]:
    start_s = start_date.isoformat() if start_date else _DEFAULT_HEATMAP_START
    if days_override is not None and days_override > 0:
        return start_s, days_override
    if start_date and end_date and end_date >= start_date:
        return start_s, (end_date - start_date).days + 1
    return start_s, _DEFAULT_HEATMAP_DAYS


def _run_subprocess_step(label: str, cmd: list[str]) -> None:
    print(f"\n[前置] {label}")
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=ROOT, check=True)


def _run_sleep_map_pool_prefix(
    *,
    start_s: str,
    end_s: str,
    users_per_district: int,
    seed: int,
    aggregated_only: bool,
) -> None:
    cmd = [
        sys.executable,
        _SLEEP_MAP_POOL_SCRIPT,
        "--start-date",
        start_s,
        "--end-date",
        end_s,
        "--users-per-district",
        str(users_per_district),
        "--seed",
        str(seed),
    ]
    if aggregated_only:
        cmd.append("--aggregated-only")
    _run_subprocess_step(
        f"虚拟用户睡眠地图池（{start_s}～{end_s}，每区 {users_per_district} 人）",
        cmd,
    )


def _run_heatmap_users_prefix(
    *,
    count: int,
    days: int,
    start_s: str,
    config_path: str,
    seed: int | None,
) -> None:
    cmd = [
        sys.executable,
        _HEATMAP_USERS_SCRIPT,
        "--count",
        str(count),
        "--days",
        str(days),
        "--start-date",
        start_s,
        "--config",
        config_path,
    ]
    if seed is not None:
        cmd.extend(["--seed", str(seed)])
    _run_subprocess_step(
        f"虚拟用户热力图（{count} 人 × {days} 天，起 {start_s}）",
        cmd,
    )


def run_for_persona(
    persona: dict,
    cfg: dict,
    *,
    state_mode: str,
    good_ratio: float,
    start_date: date | None,
    end_date: date | None,
    overwrite: bool,
    with_llm: bool = False,
    llm_only: bool = False,
    llm_max_records: int | None = None,
    prune_after_llm_skips: bool = False,
    resume_llm: bool = True,
) -> None:
    name = persona.get("name", persona.get("user_id", ""))
    uid = persona.get("user_id", "")
    output_dir = os.path.join(ROOT, "output")
    total_steps = 19 if with_llm else 7
    print(f"\n========== {name} ({uid}) ==========")

    if not llm_only:
        print(f"[1/{total_steps}] 睡眠健康数据 (health_data) …")
        health_path = generate_persona_health_data(
            persona,
            state_mode=state_mode,
            good_ratio=good_ratio,
            start_date_override=start_date,
            end_date_override=end_date,
            overwrite=overwrite,
        )
        if not health_path:
            print("  跳过后续步骤（健康数据未生成）。")
            return

        print(f"[2/{total_steps}] 环境数据 (environment_data) …")
        generate_environment_for_persona(
            persona, cfg, start_date=start_date, end_date=end_date, overwrite=overwrite
        )

        print(f"[3/{total_steps}] 体征数据 (vitals_data) …")
        generate_vitals_for_persona(
            persona, cfg, start_date=start_date, end_date=end_date, overwrite=overwrite
        )

        print(f"[4/{total_steps}] 睡眠事件 (sleep_events) …")
        generate_sleep_events_for_persona(
            persona, cfg, start_date=start_date, end_date=end_date, overwrite=overwrite
        )

        print(f"[5/{total_steps}] 事件影响回写 (events -> environment/vitals) …")
        apply_event_impacts_for_persona(
            persona,
            start_date.isoformat() if start_date else None,
            end_date.isoformat() if end_date else None,
            overwrite=overwrite,
            output_dir=output_dir,
        )

        print(f"[6/{total_steps}] 睡眠地图分析 (somni_sleep_analysis) …")
        sm_path, sm_n = write_somni_sleep_analysis_for_uid(
            uid,
            output_dir,
            user_name=(str(name or "").strip() or f"sleep_map_user_{uid[:8]}"),
        )
        if sm_path:
            print(f"  已写入 {sm_n} 条 → {sm_path}")
        else:
            print(f"  [跳过] 无 health 数据或未生成 {uid}_health_data.json")

        print("  睡眠艺术数据 (sleep_art_data) …")
        write_sleep_art_data_for_persona(persona, output_dir, overwrite=overwrite)

        print(f"[7/{total_steps}] 睡眠报告 (sleep_report) …")
        _health_file = os.path.join(output_dir, f"{uid}_health_data.json")
        _report_file = os.path.join(output_dir, f"{uid}_sleep_report.json")
        if not overwrite and os.path.exists(_report_file):
            print(f"  已存在，跳过（可加 --overwrite 强制重生成）")
        elif os.path.exists(_health_file):
            with open(_health_file, "r", encoding="utf-8") as f:
                _sd_list = json.load(f)
            _sleep_std = cfg.get(
                "sleepStandard",
                {"deep": [15, 25], "light": [45, 60], "rem": [20, 25], "awake": 10},
            )
            _persona_type = str(persona.get("code") or "M-L-C").strip() or "M-L-C"
            _sei = build_sleep_events_index(uid, output_dir=output_dir)
            _sd_list.sort(key=lambda r: str(r.get("record_date") or ""))
            _reports: list = []
            _recent_t: list = []
            for _sd in _sd_list:
                try:
                    _r = _gen_sleep_report_single(
                        _sd, uid, "", _sleep_std, _persona_type, _sei,
                        recent_titles=_recent_t,
                    )
                    _reports.append(_r)
                    _t = _r.get("main", {}).get("title")
                    if _t:
                        _recent_t.append(_t)
                        if len(_recent_t) > 2:
                            _recent_t.pop(0)
                except Exception as _e:
                    print(f"    生成 {_sd.get('record_date', '?')} 日报告出错: {_e}")
            _reports = check_and_process_sleep_talk(_reports)
            with open(_report_file, "w", encoding="utf-8") as f:
                json.dump(_reports, f, ensure_ascii=False, indent=2)
            print(f"  已写入 {len(_reports)} 条 → {_report_file}")
            _start_s = start_date.isoformat() if start_date else None
            _end_s = end_date.isoformat() if end_date else None
            print("  补全听觉 audios 与打鼾曲线 (data_points，按分钟) …")
            from write_back.refresh_sleep_report_auditory_snoring import (  # noqa: E402
                refresh_sleep_report_auditory_snoring_for_uid,
            )

            _ref = refresh_sleep_report_auditory_snoring_for_uid(
                uid,
                output_dir,
                start_date=_start_s,
                end_date=_end_s,
                refresh_audios=True,
            )
            print(
                f"  已更新 {_ref.get('days_updated', 0)} 个报告日的 data_points；"
                f"Snore 合并 {_ref.get('snore_audios_merged_days', 0)} 日、"
                f"移除 {_ref.get('snore_entries_removed', 0)} 条重复 audios"
            )
        else:
            print(f"  [跳过] 未找到 {_health_file}")

    if with_llm:
        from generate_ai.llm_client import LlmQuotaExhausted  # noqa: E402

        if resume_llm:
            print("  [LLM] 断点续跑已开启：output 中已有日期的记录将跳过（--overwrite 会关闭）")

        # 仅 LLM 时共 12 个子步骤（11 个 LLM 文件 + 写回）；完整流程时为第 8～19 步
        llm_sub_total = 12 if llm_only else total_steps

        def _llm_quota_abort(exc: LlmQuotaExhausted) -> None:
            print(f"  [中断] LLM 配额/限流耗尽: {exc}")
            print(
                "  已完成日期已写入 output/。请恢复 API 配额后，用相同命令重新运行"
                "（不要加 --overwrite）；也可用 --write-back-only 仅写回已有 LLM 文件。"
            )
            raise

        def _llm_label(idx_in_llm: int) -> str:
            if llm_only:
                return f"[{idx_in_llm}/{llm_sub_total}]"
            return f"[{7 + idx_in_llm}/{total_steps}]"

        if llm_only:
            print("  睡眠艺术数据 (sleep_art_data) …")
            write_sleep_art_data_for_persona(persona, output_dir, overwrite=overwrite)

        start_s = start_date.isoformat() if start_date else None
        end_s = end_date.isoformat() if end_date else None
        if llm_max_records is not None and llm_max_records > 0:
            print(f"  [LLM] 各逐日步骤最多处理锚点数: {llm_max_records}（在日期过滤之后截断）")

        def _llm_max() -> int | None:
            return llm_max_records if llm_max_records and llm_max_records > 0 else None

        def _normalize_commonality_rows(results: list) -> list[dict]:
            out_rows: list[dict] = []
            for row in results:
                if not isinstance(row, dict):
                    continue
                inner = row.get("sleep_pattern_commonality")
                blocks: list[dict] = []
                if isinstance(inner, list):
                    for item in inner:
                        if isinstance(item, dict):
                            blocks.append(
                                {
                                    "highlight": item.get("highlight", ""),
                                    "analysis": item.get("analysis", ""),
                                    "type": str(item.get("type") or ""),
                                    "list": _coerce_commonality_metric_list(
                                        item.get("list")
                                    ),
                                }
                            )
                elif isinstance(inner, dict):
                    blocks.append(
                        {
                            "highlight": inner.get("highlight", ""),
                            "analysis": inner.get("analysis", ""),
                            "type": str(inner.get("type") or ""),
                            "list": _coerce_commonality_metric_list(
                                inner.get("list")
                            ),
                        }
                    )
                out_rows.append(
                    {
                        "uid": str(row.get("uid") or uid),
                        "record_date": str(row.get("record_date") or ""),
                        "sleep_pattern_commonality": blocks,
                    }
                )
            return out_rows

        print(f"{_llm_label(1)} 近14天趋势 AI 分析 (ai_analysis_14d) …")
        try:
            ai14_rows, L1_skip = generate_ai_analysis_14d_for_uid(
                uid=uid,
                output_dir=output_dir,
                start_date=start_s,
                end_date=end_s,
                max_records=_llm_max(),
                resume=resume_llm,
            )
        except LlmQuotaExhausted as e:
            _llm_quota_abort(e)
        ai14_path = os.path.join(output_dir, f"{uid}_ai_analysis_14d.json")
        with open(ai14_path, "w", encoding="utf-8") as f:
            json.dump(ai14_rows, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {len(ai14_rows)} 条 → {ai14_path}")
        print(f"  [ai_analysis_14d] 范围内最后跳过日: {L1_skip or '（无）'}")

        print(f"{_llm_label(2)} 晨间闹钟上下文洞察 (morning_alarm_insight) …")
        try:
            alarm_rows, L2_skip = generate_alarm_insight_for_uid(
                uid=uid,
                output_dir=output_dir,
                start_date=start_s,
                end_date=end_s,
                max_records=_llm_max(),
                schedules_dir="output",
                resume=resume_llm,
            )
        except LlmQuotaExhausted as e:
            _llm_quota_abort(e)
        alarm_path = os.path.join(output_dir, f"{uid}_morning_alarm_insight.json")
        with open(alarm_path, "w", encoding="utf-8") as f:
            json.dump(alarm_rows, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {len(alarm_rows)} 条 → {alarm_path}")
        print(f"  [morning_alarm_insight] 范围内最后跳过日: {L2_skip or '（无）'}")

        print(
            f"  [LLM] 步骤 10～18 按日范围: start={start_s or '—'} end={end_s or '—'}"
            f"（全量锚点，不因 ai/morning 跳过边界缩减）"
        )

        print(f"{_llm_label(3)} 近14天睡眠共性分析 (sleep_pattern_commonality) …")
        try:
            results_c, _Lc = generate_sleep_pattern_commonality_for_uid(
                uid=uid,
                output_dir=output_dir,
                start_date=start_s,
                end_date=end_s,
                max_records=_llm_max(),
                resume=resume_llm,
            )
        except LlmQuotaExhausted as e:
            _llm_quota_abort(e)
        out_rows_c = _normalize_commonality_rows(results_c)
        out_path_c = os.path.join(output_dir, f"{uid}_sleep_pattern_commonality.json")
        with open(out_path_c, "w", encoding="utf-8") as f:
            json.dump(out_rows_c, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {len(out_rows_c)} 条（逐日）→ {out_path_c}")

        print(f"{_llm_label(4)} 睡眠共性洞察汇总 (sleep_pattern_commonality_insight) …")
        try:
            insight_rows, L3_skip = generate_commonality_insight_for_uid(
                uid=uid,
                output_dir=output_dir,
                start_date=start_s,
                end_date=end_s,
                max_records=_llm_max(),
                resume=resume_llm,
            )
        except LlmQuotaExhausted as e:
            _llm_quota_abort(e)
        insight_path = os.path.join(output_dir, f"{uid}_sleep_pattern_commonality_insight.json")
        with open(insight_path, "w", encoding="utf-8") as f:
            json.dump(insight_rows, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {len(insight_rows)} 条 → {insight_path}")
        print(
            f"  [sleep_pattern_commonality_insight] LLM 失败最晚日（仅 API/解析失败时）: "
            f"{L3_skip or '（无）'}"
        )

        print(f"{_llm_label(5)} 睡眠事件 AI 干预重写 (sleep_ai_intervention，整批一次) …")
        try:
            intervention_rows = generate_ai_intervention_for_uid(
                uid=uid,
                output_dir=output_dir,
                start_date=start_s,
                end_date=end_s,
                max_records=_llm_max(),
                resume=resume_llm,
            )
        except LlmQuotaExhausted as e:
            _llm_quota_abort(e)
        intervention_path = os.path.join(output_dir, f"{uid}_sleep_ai_intervention.json")
        with open(intervention_path, "w", encoding="utf-8") as f:
            json.dump(intervention_rows, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {len(intervention_rows)} 条（逐日）→ {intervention_path}")

        print(f"{_llm_label(6)} 睡眠地图排名原因 (sleep_map_ranking_reason) …")
        try:
            ranking_rows = generate_ranking_reason_for_uid(
                uid=uid,
                output_dir=output_dir,
                start_date=start_s,
                end_date=end_s,
                max_records=_llm_max(),
                resume=resume_llm,
            )
        except LlmQuotaExhausted as e:
            _llm_quota_abort(e)
        ranking_path = os.path.join(output_dir, f"{uid}_sleep_map_ranking_reason.json")
        with open(ranking_path, "w", encoding="utf-8") as f:
            json.dump(ranking_rows, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {len(ranking_rows)} 条 → {ranking_path}")

        print(f"{_llm_label(7)} 睡眠质量模块 (sleep_quality) …")
        try:
            quality_rows = generate_quality_for_uid(
                uid=uid,
                output_dir=output_dir,
                start_date=start_s,
                end_date=end_s,
                max_records=_llm_max(),
                resume=resume_llm,
            )
        except LlmQuotaExhausted as e:
            _llm_quota_abort(e)
        quality_path = os.path.join(output_dir, f"{uid}_sleep_quality.json")
        with open(quality_path, "w", encoding="utf-8") as f:
            json.dump(quality_rows, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {len(quality_rows)} 条 → {quality_path}")

        print(f"{_llm_label(8)} 睡眠听觉模块 (sleep_auditory) …")
        try:
            auditory_rows = generate_auditory_for_uid(
                uid=uid,
                output_dir=output_dir,
                start_date=start_s,
                end_date=end_s,
                max_records=_llm_max(),
                resume=resume_llm,
            )
        except LlmQuotaExhausted as e:
            _llm_quota_abort(e)
        auditory_path = os.path.join(output_dir, f"{uid}_sleep_auditory.json")
        with open(auditory_path, "w", encoding="utf-8") as f:
            json.dump(auditory_rows, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {len(auditory_rows)} 条 → {auditory_path}")

        print(f"{_llm_label(9)} 睡眠主摘要 (sleep_main_summary) …")
        try:
            main_sum_rows = generate_main_summary_for_uid(
                uid=uid,
                output_dir=output_dir,
                start_date=start_s,
                end_date=end_s,
                max_records=_llm_max(),
                resume=resume_llm,
            )
        except LlmQuotaExhausted as e:
            _llm_quota_abort(e)
        main_sum_path = os.path.join(output_dir, f"{uid}_sleep_main_summary.json")
        with open(main_sum_path, "w", encoding="utf-8") as f:
            json.dump(main_sum_rows, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {len(main_sum_rows)} 条 → {main_sum_path}")

        print(f"{_llm_label(10)} 睡眠 notice (sleep_notice) …")
        try:
            notice_rows = generate_notice_for_uid(
                uid=uid,
                output_dir=output_dir,
                start_date=start_s,
                end_date=end_s,
                personality_type=str(persona.get("code") or "").strip() or None,
                max_records=_llm_max(),
                resume=resume_llm,
            )
        except LlmQuotaExhausted as e:
            _llm_quota_abort(e)
        notice_path = os.path.join(output_dir, f"{uid}_sleep_notice.json")
        with open(notice_path, "w", encoding="utf-8") as f:
            json.dump(notice_rows, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {len(notice_rows)} 条 → {notice_path}")

        print(f"{_llm_label(11)} 睡眠事件环境干预分析 (sleep_event_environment_intervention) …")
        try:
            env_int_rows = generate_intervention_for_uid(
                uid=uid,
                output_dir=output_dir,
                start_date=start_s,
                end_date=end_s,
                max_records=_llm_max(),
                resume=resume_llm,
            )
        except LlmQuotaExhausted as e:
            _llm_quota_abort(e)
        env_int_path = os.path.join(output_dir, f"{uid}_sleep_event_environment_intervention.json")
        with open(env_int_path, "w", encoding="utf-8") as f:
            json.dump(env_int_rows, f, ensure_ascii=False, indent=2)
        print(f"  已写入 {len(env_int_rows)} 条 → {env_int_path}")

        print(f"{_llm_label(12)} LLM 结果写回 (write_back) …")
        from write_back.write_back_llm_outputs import run_write_back_for_uid  # noqa: E402

        wb_stats = run_write_back_for_uid(uid, output_dir, start_date=start_s, end_date=end_s)
        print(
            f"  fusion={wb_stats['fusion_insight_written']}, "
            f"hidden_discovery日={wb_stats['hidden_discovery_written_days']}, "
            f"events_detail={wb_stats['sleep_events_detail_updates']}, "
            f"quality+={wb_stats['sleep_quality_appended']}, "
            f"auditory={wb_stats['sleep_auditory_updated']}, "
            f"main_summary={wb_stats['main_summary_updated']}, "
            f"notice={wb_stats['notice_updated']}, "
            f"env+={wb_stats['env_intervention_appended']}, "
            f"ranking_reason={wb_stats['ranking_reason_updated']}, "
            f"snore_audios合并日={wb_stats['snore_audios_merged_days']}, "
            f"snore移除条数={wb_stats['snore_entries_removed']}, "
            f"data_points刷新日={wb_stats['snoring_data_points_updated_days']}"
        )

        if prune_after_llm_skips:
            keep_from = compute_keep_from_date([L1_skip, L2_skip], start_s)
            if keep_from:
                print(
                    f"  [--prune-after-llm-skips] 裁剪 LLM 侧车 JSON（保留 >= {keep_from}；"
                    f"跳过日 L1={L1_skip or '-'} L2={L2_skip or '-'}）…"
                )
                prune_stats = prune_uid_output_files(uid, output_dir, keep_from)
                n_removed = prune_stats.get("total_removed", 0)
                if n_removed:
                    for suffix, st in prune_stats.get("files", {}).items():
                        print(f"    {suffix}: 删除 {st['removed']} 条（{st['before']} → {st['after']}）")
                    print(f"  共删除 {n_removed} 条早于 {keep_from} 的 LLM 记录")
                else:
                    print(f"  无需裁剪（无早于 {keep_from} 的记录）")
            else:
                print("  [裁剪] 无 LLM 跳过边界且未指定 --start-date，跳过 LLM 侧车日期裁剪")
        else:
            print("  [裁剪] 已跳过（默认保留全部 LLM 侧车记录；需裁剪请加 --prune-after-llm-skips）")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "按人格配置依次生成 qweather_today_snapshot → {uid}_weather.json → traffic → calendar → daily_emotion_steps → health → environment → "
            "vitals → sleep_events → 事件回写 → somni_sleep_analysis → sleep_art（+ 可选 LLM 九步与第 10 步写回，见 --with-llm；"
            "仅写回见 --write-back-only；--llm-only 时跳过前置与第 1～6 步，LLM 前仍尝试 sleep_art）"
        ),
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="health_data_personas_config.json 路径",
    )
    parser.add_argument("--user-id", default=None, help="仅处理该 user_id，不填则处理全部人格")
    parser.add_argument(
        "--state",
        choices=["good", "bad", "mixed"],
        default="mixed",
        help="睡眠健康数据日状态模式（默认 mixed）",
    )
    parser.add_argument(
        "--good-ratio",
        type=float,
        default=0.5,
        help="mixed 模式下好夜占比 0.0～1.0（默认 0.5）",
    )
    parser.add_argument("--start-date", default=None, help="覆盖起始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="覆盖结束日期 YYYY-MM-DD")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已存在的 health/environment/vitals/sleep_events 输出；并强制重新执行事件影响回写（忽略 {uid}_event_impacts_applied 标记）",
    )
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help=(
            "在完成第 6 步睡眠地图后额外调用 generate_ai 下全部 LLM 批处理（每用户各整批一次），"
            "顺序：ai_analysis_14d → morning_alarm_insight → sleep_pattern_commonality "
            "与 insight（起始日由跳过边界收敛）→ sleep_ai_intervention → sleep_map_ranking_reason → "
            "sleep_quality → sleep_auditory → sleep_main_summary → sleep_notice → "
            "sleep_event_environment_intervention → write_back 写回（"
            "干预/排名原因/质量/听觉/主摘要/notice/环境干预 的按日起始日与共性收敛后一致；"
            "写回受 --start-date/--end-date 与 LLM 相同的 record_date 区间过滤）"
        ),
    )
    parser.add_argument(
        "--llm-only",
        action="store_true",
        help="跳过天气/路况/日历/每日情绪、前 6 步（含睡眠地图）；LLM 前仍从已有 health 尝试写 sleep_art（需配合 --with-llm）",
    )
    parser.add_argument(
        "--write-back-only",
        action="store_true",
        help=(
            "仅执行 LLM 写回（output 下已有 *_ai_analysis_14d 等文件），不跑天气/健康/LLM 生成；"
            "可选 --user-id、--start-date、--end-date；与 --with-llm / --llm-only 互斥"
        ),
    )
    parser.add_argument(
        "--llm-max-records",
        type=int,
        default=None,
        metavar="N",
        help=(
            "与 --with-llm 联用：各 LLM 逐日步骤在 start/end 过滤后最多处理 N 个锚点（含跳过尝试；"
            "不填表示不限制）"
        ),
    )
    parser.add_argument(
        "--prune-after-llm-skips",
        action="store_true",
        help=(
            "与 --with-llm 联用：写回完成后按 ai_analysis_14d / morning / insight 的"
            "「最后跳过日」裁剪下列 LLM 侧车 JSON（仅依据 ai_analysis_14d / morning 跳过边界；默认不裁剪）："
            "ai_analysis_14d、morning_alarm_insight、sleep_pattern_commonality、"
            "sleep_pattern_commonality_insight、sleep_ai_intervention、sleep_map_ranking_reason、"
            "sleep_quality、sleep_auditory、sleep_main_summary、sleep_notice、"
            "sleep_event_environment_intervention"
        ),
    )
    parser.add_argument(
        "--no-prune-after-llm-skips",
        action="store_true",
        help="已弃用：默认即不裁剪 LLM 侧车 JSON，可忽略本参数",
    )
    parser.add_argument(
        "--no-resume-llm",
        action="store_true",
        help=(
            "与 --with-llm 联用：禁用 LLM 断点续跑（默认在未 --overwrite 时，跳过 output 中"
            "已有完整记录的日期，配额耗尽后可用相同命令续跑）"
        ),
    )
    parser.add_argument(
        "--with-virtual-map-data",
        action="store_true",
        help=(
            "在前置步骤最先执行：虚拟用户睡眠地图排行池（somni_sleep_analysis + somni_sleep_district），"
            "不生成每人 health_data；等价于 --with-sleep-map-pool。不含热力图（热力图见 --with-heatmap-users）"
        ),
    )
    parser.add_argument(
        "--with-sleep-map-pool",
        action="store_true",
        help=(
            "前置：调用 generate_sleep_map_pool.py；日期优先用 --start-date/--end-date，"
            f"否则 {_DEFAULT_MAP_POOL_START}～{_DEFAULT_MAP_POOL_END}"
        ),
    )
    parser.add_argument(
        "--with-heatmap-users",
        action="store_true",
        help=(
            "前置：热力图虚拟用户（仅 output/virtual_heatmap/*_sleep_map_score.json + heatmap_users_index），"
            "不在 output/ 根目录写入虚拟用户 health_data；8 人格健康数据仍只由后续主流程生成"
        ),
    )
    parser.add_argument(
        "--sleep-map-pool-users-per-district",
        type=int,
        default=13,
        metavar="N",
        help="每区虚拟用户数（默认 13，传给 generate_sleep_map_pool）",
    )
    parser.add_argument(
        "--sleep-map-pool-seed",
        type=int,
        default=42,
        help="睡眠地图池随机种子（默认 42）",
    )
    parser.add_argument(
        "--sleep-map-pool-aggregated-only",
        action="store_true",
        help="仅重聚合已有 beijing_sleep_map_multi_user.json（跳过 multi_user 生成）",
    )
    parser.add_argument(
        "--heatmap-count",
        type=int,
        default=100,
        metavar="N",
        help="热力图虚拟用户数量（默认 100）",
    )
    parser.add_argument(
        "--heatmap-days",
        type=int,
        default=None,
        metavar="N",
        help="每名热力图用户天数；不填则按 start/end 推算，否则默认 14",
    )
    parser.add_argument(
        "--heatmap-seed",
        type=int,
        default=None,
        help="热力图随机种子（不填则每次不同虚拟 user_id）",
    )
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    personas = cfg.get("personas") or []
    if args.user_id:
        personas = [p for p in personas if p.get("user_id") == args.user_id]
        if not personas:
            print(f"未找到 user_id={args.user_id}")
            sys.exit(1)

    start_date = _parse_date(args.start_date)
    end_date = _parse_date(args.end_date)

    llm_max_records: int | None = args.llm_max_records
    if llm_max_records is not None and llm_max_records <= 0:
        print("错误：--llm-max-records 须为正整数。", file=sys.stderr)
        sys.exit(2)
    if llm_max_records is not None and not args.with_llm:
        print("提示：--llm-max-records 仅在 --with-llm 时生效，已忽略。")
        llm_max_records = None

    if args.with_llm:
        from generate_ai.runtime import bootstrap_llm  # noqa: E402

        if not bootstrap_llm():
            print("错误：豆包 LLM 未就绪，请检查 .env 中 BASE_URL、DOUBAO_API_KEY、MODEL_NAME。", file=sys.stderr)
            sys.exit(2)
        import generate_ai.llm_client as _llm_client  # noqa: E402

        _llm_client.enable_http_status_logging(True)
        print(
            f"LLM：豆包（{os.getenv('BASE_URL', '').rstrip('/') or 'ark'} / "
            f"{_llm_client._qwen_model_name()}），每次调用将打印 HTTP status_code"
        )

    if args.write_back_only:
        if args.with_llm or args.llm_only:
            print(
                "错误：--write-back-only 不能与 --with-llm 或 --llm-only 同时使用。",
                file=sys.stderr,
            )
            sys.exit(2)
        from write_back.write_back_llm_outputs import run_write_back_for_uid  # noqa: E402

        output_dir = os.path.join(ROOT, "output")
        start_s = start_date.isoformat() if start_date else None
        end_s = end_date.isoformat() if end_date else None
        print(
            f"\n========== 仅写回 LLM 产物（--write-back-only）==========\n"
            f"配置：{args.config}  人格数：{len(personas)}  "
            f"start={start_s or '-'}  end={end_s or '-'}"
        )
        for persona in personas:
            uid = str(persona.get("user_id") or "").strip()
            if not uid:
                print("  [跳过] 人格缺少 user_id")
                continue
            name = persona.get("name", uid)
            print(f"\n--- {name} ({uid}) ---")
            wb = run_write_back_for_uid(uid, output_dir, start_date=start_s, end_date=end_s)
            print(
                f"  fusion={wb['fusion_insight_written']}, "
                f"hidden_discovery日={wb['hidden_discovery_written_days']}, "
                f"events_detail={wb['sleep_events_detail_updates']}, "
                f"quality+={wb['sleep_quality_appended']}, "
                f"main_summary={wb['main_summary_updated']}, "
                f"notice={wb['notice_updated']}, "
                f"env+={wb['env_intervention_appended']}, "
                f"ranking_reason={wb['ranking_reason_updated']}, "
                f"snore_audios合并日={wb['snore_audios_merged_days']}, "
                f"snore移除条数={wb['snore_entries_removed']}"
            )
        print("\n写回全部完成。")
        return

    llm_only = args.llm_only and args.with_llm
    with_sleep_map_pool = args.with_sleep_map_pool or args.with_virtual_map_data
    with_heatmap_users = args.with_heatmap_users
    if args.sleep_map_pool_users_per_district <= 0:
        print("错误：--sleep-map-pool-users-per-district 须为正整数。", file=sys.stderr)
        sys.exit(2)
    if args.heatmap_count is not None and args.heatmap_count <= 0:
        print("错误：--heatmap-count 须为正整数。", file=sys.stderr)
        sys.exit(2)
    if args.heatmap_days is not None and args.heatmap_days <= 0:
        print("错误：--heatmap-days 须为正整数。", file=sys.stderr)
        sys.exit(2)

    resume_llm = args.with_llm and not args.overwrite and not args.no_resume_llm

    _llm_chain = (
        "ai_analysis_14d → morning_alarm_insight → sleep_pattern_commonality → insight → "
        "sleep_ai_intervention → sleep_map_ranking_reason → sleep_quality → sleep_auditory → "
        "sleep_main_summary → sleep_notice → sleep_event_environment → write_back"
    )
    virtual_prefix = []
    if with_sleep_map_pool:
        virtual_prefix.append("sleep_map_pool")
    if with_heatmap_users:
        virtual_prefix.append("heatmap_users")
    virtual_prefix_s = (
        " → ".join(virtual_prefix) + " → " if virtual_prefix else ""
    )

    pipeline = (
        f"{_llm_chain}（仅LLM）"
        if llm_only
        else (
            virtual_prefix_s
            + "qweather_today_snapshot → user_weather_json → traffic_link_realtime → calendar_events → daily_emotion_steps → health_data"
            " → environment_data → vitals_data → sleep_events → events_impact_apply → somni_sleep_analysis"
            " → sleep_art_data"
            + (f" → {_llm_chain}（LLM）" if args.with_llm else "")
        )
    )
    print(
        f"生成顺序：{pipeline}\n"
        f"配置：{args.config}  人格数：{len(personas)}  overwrite={args.overwrite}"
        + (f"  llm_max_records={llm_max_records}" if llm_max_records else "")
        + (f"  resume_llm={resume_llm}" if args.with_llm else "")
        + (
            f"  virtual_map_pool={with_sleep_map_pool} heatmap={with_heatmap_users}"
            if (with_sleep_map_pool or with_heatmap_users)
            else ""
        )
    )

    output_dir = os.path.join(ROOT, "output")
    if not llm_only:
        if with_sleep_map_pool or with_heatmap_users:
            print(
                "\n========== 前置：虚拟用户排行池 / 热力图（非 8 人格 health 流水线）=========="
            )
            pool_start_s, pool_end_s = _resolve_map_pool_dates(start_date, end_date)
            if pool_end_s < pool_start_s:
                print("错误：地图池结束日期早于起始日期。", file=sys.stderr)
                sys.exit(2)
            if with_sleep_map_pool:
                _run_sleep_map_pool_prefix(
                    start_s=pool_start_s,
                    end_s=pool_end_s,
                    users_per_district=args.sleep_map_pool_users_per_district,
                    seed=args.sleep_map_pool_seed,
                    aggregated_only=args.sleep_map_pool_aggregated_only,
                )
            if with_heatmap_users:
                heatmap_start_s, heatmap_days = _resolve_heatmap_params(
                    start_date, end_date, args.heatmap_days
                )
                _run_heatmap_users_prefix(
                    count=args.heatmap_count,
                    days=heatmap_days,
                    start_s=heatmap_start_s,
                    config_path=os.path.abspath(args.config),
                    seed=args.heatmap_seed,
                )

        print("\n========== 前置：天气、路况、日历与每日情绪（当前人格列表）==========")
        print("[前置] 和风近 30 天天气数据 (qweather_monthly_data) …")
        qweather_path = os.path.join(output_dir, "qweather_monthly_data.json")
        fetch_qweather_monthly_data(output_dir=output_dir)
        print(f"  已写入 → {qweather_path}")
        print("[前置] 各用户天气结构 ({uid}_weather.json) …")
        generate_weather_snapshots_for_personas(personas, output_dir, overwrite=True)
        traffic_ts = int(time.time())
        print("[前置] 北京 Link 实时路况 (traffic_link_realtime) …")
        generate_traffic_link_realtime_for_personas(
            personas, output_dir, ts=traffic_ts, overwrite=args.overwrite
        )
        print("[前置] 日历事件 (calendar_events) …")
        generate_calendar_events_for_personas(
            personas,
            output_dir,
            start_date_override=start_date,
            end_date_override=end_date,
            overwrite=args.overwrite,
        )
        mix = (cfg.get("generation") or {}).get("day_state_mix") or {}
        max_bad = int(mix.get("max_bad_days_per_week", 3))
        print("[前置] 每日情绪与步数 (daily_emotion_steps) …")
        generate_daily_emotion_steps_for_personas(
            personas,
            state_mode=args.state,
            good_ratio=args.good_ratio,
            max_bad_days_per_week=max_bad,
            start_date_override=start_date,
            end_date_override=end_date,
            overwrite=args.overwrite,
        )

    _run_kwargs = dict(
        cfg=cfg,
        state_mode=args.state,
        good_ratio=args.good_ratio,
        start_date=start_date,
        end_date=end_date,
        overwrite=args.overwrite,
        with_llm=args.with_llm,
        llm_only=llm_only,
        llm_max_records=llm_max_records,
        prune_after_llm_skips=args.with_llm and args.prune_after_llm_skips,
        resume_llm=resume_llm,
    )

    if len(personas) <= 1:
        for persona in personas:
            run_for_persona(persona, **_run_kwargs)
    else:
        print(f"\n========== 并行生成（{len(personas)} 个人格，LLM 并发上限 3）==========")
        errors: dict[str, Exception] = {}
        with ThreadPoolExecutor(max_workers=len(personas)) as executor:
            futures = {
                executor.submit(run_for_persona, persona, **_run_kwargs): persona
                for persona in personas
            }
            for future in as_completed(futures):
                persona = futures[future]
                name = persona.get("name", persona.get("user_id", ""))
                try:
                    future.result()
                    print(f"  [完成] {name}")
                except Exception as e:
                    errors[name] = e
                    print(f"  [出错] {name}: {e}")
        if errors:
            print(f"\n{len(errors)} 个人格生成失败：")
            for name, e in errors.items():
                print(f"  - {name}: {e}")
            try:
                from generate_ai.llm_client import LlmQuotaExhausted  # noqa: E402

                if any(isinstance(e, LlmQuotaExhausted) for e in errors.values()):
                    print(
                        "\n提示：若为 API 配额/限流，恢复后请用相同命令重跑（勿 --overwrite）；"
                        "或 python main.py --write-back-only 写回已有 LLM 文件。"
                    )
                    sys.exit(3)
            except ImportError:
                pass
            sys.exit(1)

    print("\n全部完成。")


if __name__ == "__main__":
    main()
