"""
单用户健康数据生成流水线：按固定顺序执行各阶段，可只跑子集（需已有上游产物）。

步骤标识（USER_DATA_PIPELINE_ORDER）：
  health → sleep_events → fitness → environment → vitals
  → event_feedback → schedule → ai_analysis → sleep_report → report_audios
"""
# 文件作用：用于 user gen pipeline 相关的数据处理或流程支持。

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional, Sequence, Tuple

from personality_profile import get_total_sleep_minutes
from utils import atomic_write_json

# 运行期从 generate_health_data 注入，避免循环 import 时读全局
def _gh():
    import generate_health_data as g
    return g


@dataclass
class UserGenContext:
    """单用户生成任务的共享上下文。"""

    generator: object
    user: dict
    user_id: str
    start_date: datetime
    end_date: datetime
    start_date_str: str
    end_date_str: str
    user_preference: object
    user_profile: str
    session_id_local: str = ""
    use_doubao: bool = False


def _ctx_user_data_json(ctx: UserGenContext, suffix: str) -> str:
    """返回 output_dir 下 `{user_id}_{suffix}` 的绝对路径（suffix 如 health_data.json）。"""
    od = getattr(ctx.generator, "output_dir", None) or "output"
    return os.path.join(od, f"{ctx.user_id}_{suffix}")


def _report_audios_state_path(ctx: UserGenContext) -> str:
    return _ctx_user_data_json(ctx, "report_audios.state.json")


def _report_audios_sources_unchanged(ctx: UserGenContext) -> bool:
    """若 state 中记录的 sleep_report / sleep_events 修改时间与当前文件一致，则认为 report_audios 已跑过且源未变。"""
    state_path = _report_audios_state_path(ctx)
    sr = _ctx_user_data_json(ctx, "sleep_report.json")
    se = _ctx_user_data_json(ctx, "sleep_events.json")
    if not (os.path.isfile(state_path) and os.path.isfile(sr) and os.path.isfile(se)):
        return False
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            st = json.load(f)
        if not isinstance(st, dict):
            return False
        mr = float(st.get("sleep_report_mtime", -1.0))
        me = float(st.get("sleep_events_mtime", -1.0))
        return os.path.getmtime(sr) == mr and os.path.getmtime(se) == me
    except Exception:
        return False


def _write_report_audios_state(ctx: UserGenContext) -> None:
    sr = _ctx_user_data_json(ctx, "sleep_report.json")
    se = _ctx_user_data_json(ctx, "sleep_events.json")
    if not (os.path.isfile(sr) and os.path.isfile(se)):
        return
    atomic_write_json(
        _report_audios_state_path(ctx),
        {
            "sleep_report_mtime": os.path.getmtime(sr),
            "sleep_events_mtime": os.path.getmtime(se),
        },
    )


def _date_in_ctx_range(date_str: str, ctx: UserGenContext) -> bool:
    """判断 YYYY-MM-DD 日期是否落在当前用户生成窗口内。"""
    if not date_str:
        return False
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
    except ValueError:
        return False
    return ctx.start_date <= d <= ctx.end_date


def _init_json_array_file(file_path: str) -> None:
    """初始化 JSON 数组文件，确保模型调用前文件已存在。"""
    atomic_write_json(file_path, [])


def _append_json_item(file_path: str, item: dict) -> int:
    """向 JSON 数组文件追加 1 条记录（原子写回）。"""
    rows = []
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                rows = loaded
        except Exception:
            rows = []
    rows.append(item)
    atomic_write_json(file_path, rows)
    return len(rows)


def pipeline_step_health(ctx: UserGenContext) -> None:
    print("\n1. 生成健康数据...")
    health_path = _ctx_user_data_json(ctx, "health_data.json")
    if os.path.isfile(health_path):
        print(f"用户 {ctx.user_id} 的健康数据已存在，跳过")
        return
    g = _gh()
    personality_type = ctx.user.get("personalInformation", {}).get("type", "M-L-C")
    sleep_outlier_by_date = g.build_sleep_outlier_mode_by_date(
        ctx.start_date, ctx.end_date, personality_type
    )
    current_date = ctx.start_date
    health_data_list = []
    while current_date <= ctx.end_date:
        rec = current_date.strftime("%Y-%m-%d")
        mode = sleep_outlier_by_date.get(rec)
        sleep_data = ctx.generator.generate_sleep_data(
            ctx.user_id,
            ctx.user_profile,
            ctx.user,
            current_date,
            sleep_outlier_mode=mode,
        )
        health_data_list.append(sleep_data)
        current_date += timedelta(days=1)
    _gh().strip_sleep_calendar_flags_from_health_records(health_data_list)
    output_file = _ctx_user_data_json(ctx, "health_data.json")
    atomic_write_json(output_file, health_data_list)
    print(f"已生成用户 {ctx.user_id} 的健康数据，共{len(health_data_list)}条，保存到 {output_file}")


def pipeline_step_fitness(ctx: UserGenContext) -> None:
    print("\n2. 生成体征数据...")
    fitness_path = _ctx_user_data_json(ctx, "fitness_data.json")
    if os.path.isfile(fitness_path):
        print(f"用户 {ctx.user_id} 的体征数据 fitness_data 已存在，跳过")
        return
    fitness_data = ctx.generator.generate_fitness_data(
        ctx.user_id, ctx.user, ctx.start_date, ctx.end_date
    )
    fitness_output_file = os.path.join(
        ctx.generator.output_dir, f"{ctx.user_id}_fitness_data.json"
    )
    atomic_write_json(fitness_output_file, fitness_data)
    print(
        f"已生成用户 {ctx.user_id} 的体征数据，共{len(fitness_data)}条，保存到 {fitness_output_file}"
    )


def pipeline_step_schedule(ctx: UserGenContext) -> None:
    print("\n6. 生成日程数据...")
    schedule_path = _ctx_user_data_json(ctx, "schedule_data.json")
    if os.path.isfile(schedule_path):
        print(f"用户 {ctx.user_id} 的日程数据已存在，跳过")
        return
    try:
        schedule_data_list = ctx.generator.generate_schedule_data(
            ctx.user_id, ctx.start_date, ctx.end_date, 8, ctx.user_preference
        )
        schedule_output_file = _ctx_user_data_json(ctx, "schedule_data.json")
        atomic_write_json(schedule_output_file, schedule_data_list)
        print(
            f"已生成用户 {ctx.user_id} 的日程数据，共{len(schedule_data_list)}条，保存到 {schedule_output_file}"
        )
    except Exception as e:
        print(f"生成用户 {ctx.user_id} 的日程数据时出错：{e}")


def pipeline_step_ai_analysis(ctx: UserGenContext) -> None:
    g = _gh()
    print("\n7. 生成 AI 分析...")
    try:
        ai_analysis_output_file = os.path.join(
            ctx.generator.output_dir, f"{ctx.user_id}_ai_analysis.json"
        )
        use_db = bool(ctx.use_doubao or g.sleepAIInsights)
        if not os.path.exists(ai_analysis_output_file):
            _init_json_array_file(ai_analysis_output_file)
            ai_results = g.generate_ai_analysis(
                ctx.user_id,
                use_doubao=use_db,
                start_date=ctx.start_date_str,
                end_date=ctx.end_date_str,
                stream_output_file=ai_analysis_output_file,
            )
            if ai_results:
                print(
                    f"已生成用户 {ctx.user_id} 的 AI 分析，共{len(ai_results)}条，保存到 {ai_analysis_output_file}"
                )
            else:
                print(f"用户 {ctx.user_id} 的 AI 分析无结果，跳过")
        else:
            print(f"用户 {ctx.user_id} 的 AI 分析已存在，跳过")
    except Exception as e:
        print(f"生成用户 {ctx.user_id} 的 AI 分析时出错：{e}")


def pipeline_step_environment(ctx: UserGenContext) -> None:
    print("\n10. 生成环境数据...")
    environment_path = _ctx_user_data_json(ctx, "environment_data.json")
    if os.path.isfile(environment_path):
        print(f"用户 {ctx.user_id} 的环境数据已存在，跳过")
        return
    try:
        environment_data_list = ctx.generator.generate_multiple_environment_data(
            ctx.user_id, ctx.start_date_str, ctx.end_date_str
        )
        session_id = ctx.session_id_local or ""
        for data in environment_data_list:
            if "session_id" not in data:
                data["session_id"] = session_id
        ctx.generator.save_environment_data(ctx.user_id, environment_data_list)
        print(f"已生成用户 {ctx.user_id} 的环境数据，共{len(environment_data_list)}条")
        g = _gh()
        n_score = g.recalculate_rule_based_sleep_scores_in_health(
            ctx.user_id, output_dir=ctx.generator.output_dir
        )
        print(f"  已按规则与环境重算 health_data 中的 sleep_score，共 {n_score} 晚")
    except Exception as e:
        print(f"生成用户 {ctx.user_id} 的环境数据时出错：{e}")


def pipeline_step_event_feedback(ctx: UserGenContext) -> None:
    g = _gh()
    print("\n10.5 根据睡眠事件回填体征/环境数据...")
    feedback_report_path = _ctx_user_data_json(ctx, "event_feedback_report.json")
    if os.path.isfile(feedback_report_path):
        print(f"用户 {ctx.user_id} 的事件回填报告已存在，跳过")
        return
    try:
        result = g.apply_sleep_event_feedback_to_fitness_and_environment(ctx.user_id)
        fit_n = int((result or {}).get("fitness_updates", 0) or 0)
        env_n = int((result or {}).get("environment_updates", 0) or 0)
        vit_n = int((result or {}).get("vitals_updates", 0) or 0)
        skip_delta = int((result or {}).get("skipped_by_delta", 0) or 0)
        skip_cap = int((result or {}).get("skipped_by_cap", 0) or 0)
        skip_pri = int((result or {}).get("skipped_by_priority", 0) or 0)
        report_file = str((result or {}).get("report_file", "") or "")
        print(
            f"已按睡眠事件回填用户 {ctx.user_id} 的数据：fitness {fit_n} 条，environment {env_n} 条，vitals {vit_n} 条，超阈值跳过 {skip_delta} 条，达日上限跳过 {skip_cap} 条，优先级覆盖跳过 {skip_pri} 条"
        )
        if report_file:
            print(f"回填报告已写入: {report_file}")
    except Exception as e:
        print(f"按睡眠事件回填用户 {ctx.user_id} 的体征/环境数据时出错：{e}")


def pipeline_step_vitals(ctx: UserGenContext) -> None:
    print("\n9. 生成体征数据 (vitals_data)...")
    vitals_path = _ctx_user_data_json(ctx, "vitals_data.json")
    if os.path.isfile(vitals_path):
        print(f"用户 {ctx.user_id} 的体征数据 vitals_data 已存在，跳过")
        return
    try:
        with open(ctx.generator.config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
        sleep_data = ctx.generator.load_sleep_data(ctx.user_id)
        if sleep_data:
            vital_signs = ctx.generator.generate_vital_signs(sleep_data, ctx.user_id, config)
            session_id = ctx.session_id_local or ""
            for data in vital_signs:
                if "session_id" not in data:
                    data["session_id"] = session_id
            vitals_output_file = _ctx_user_data_json(ctx, "vitals_data.json")
            atomic_write_json(vitals_output_file, vital_signs)
            print(
                f"已生成用户 {ctx.user_id} 的体征数据 (vitals_data)，共{len(vital_signs)}条，保存到 {vitals_output_file}"
            )
        else:
            print(f"用户 {ctx.user_id} 没有健康数据，跳过生成体征数据")
    except Exception as e:
        print(f"生成用户 {ctx.user_id} 的体征数据时出错：{e}")


def _run_sleep_report_for_user(ctx: UserGenContext) -> None:
    g = _gh()
    sleep_report_file = _ctx_user_data_json(ctx, "sleep_report.json")
    health_data_file = _ctx_user_data_json(ctx, "health_data.json")
    if os.path.isfile(sleep_report_file):
        print(f"  用户 {ctx.user_id} 的睡眠报告已存在，跳过")
        return
    if not os.path.exists(health_data_file):
        print(f"  健康数据文件 {health_data_file} 不存在")
        return
    with open(ctx.generator.config_file, "r", encoding="utf-8") as f:
        config = json.load(f)
    sleep_standard = config.get(
        "sleepStandard", {"deep": [15, 25], "light": [45, 60], "rem": [20, 25], "awake": 10}
    )
    try:
        with open(health_data_file, "r", encoding="utf-8") as f:
            sleep_data_list = json.load(f)
        if not sleep_data_list:
            print(f"  文件 {health_data_file} 中没有睡眠数据")
            return
        sleep_data_list = [
            row
            for row in sleep_data_list
            if isinstance(row, dict)
            and _date_in_ctx_range(str(row.get("record_date") or ""), ctx)
        ]
        if not sleep_data_list:
            print(f"  用户 {ctx.user_id} 在当前日期分片内无睡眠数据，跳过睡眠报告")
            return
        sleep_data_list.sort(key=lambda row: str(row.get("record_date") or ""))
        reports = []
        _init_json_array_file(sleep_report_file)
        print(f"  已创建睡眠报告文件：{sleep_report_file}（将按条写入）")
        report_total = len(sleep_data_list)
        print(f"  用户ID: {ctx.user_id}")
        sleep_events_index = g.build_sleep_events_index(ctx.user_id)
        session_id = ctx.session_id_local or ""
        personality_type = "M-L-C"
        for u in config.get("user_profiles", []):
            if u.get("user_id") == ctx.user_id:
                session_id = u.get("session_id", "")
                personality_type = u.get("personalInformation", {}).get("type", "M-L-C")
                break
        print(f"  Session ID: {session_id}")
        # 必须顺序生成并传入 recent_titles；并行调用 generate_sleep_report 时 recent_titles 恒为 None，
        # pick_main_title 无法在候选间「避开与邻近日相同」，会出现大量连续同一标签。
        recent_titles_window: list[str] = []
        _RECENT_TITLE_MEM = 2
        for j, sleep_data in enumerate(sleep_data_list):
            if j % 5 == 0:
                print(f"  处理第 {j+1} 条睡眠数据")
            try:
                report = g.generate_sleep_report(
                    sleep_data,
                    ctx.user_id,
                    session_id,
                    sleep_standard,
                    personality_type,
                    sleep_events_index,
                    recent_titles=list(recent_titles_window),
                )
                reports.append(report)
                written_count = _append_json_item(sleep_report_file, report)
                print(f"  睡眠报告写入进度：{written_count}/{report_total}")
                used_title = report.get("main", {}).get("title")
                if used_title:
                    recent_titles_window.append(str(used_title))
                    if len(recent_titles_window) > _RECENT_TITLE_MEM:
                        recent_titles_window.pop(0)
            except Exception as e:
                print(f"    处理第 {j+1} 条数据时出错: {str(e)}")
                continue
        if len(reports) < len(sleep_data_list):
            print(
                f"  睡眠报告仅成功 {len(reports)}/{len(sleep_data_list)} 条，"
                f"为避免覆盖旧文件，本次不写 {sleep_report_file}"
            )
        else:
            atomic_write_json(sleep_report_file, reports)
            print(f"  睡眠报告已生成，保存到 {sleep_report_file}")
    except Exception as e:
        print(f"  处理文件 {health_data_file} 时出错: {str(e)}")


def pipeline_step_sleep_report(ctx: UserGenContext) -> None:
    print("\n11. 生成睡眠报告...")
    _run_sleep_report_for_user(ctx)


def pipeline_step_sleep_events(ctx: UserGenContext) -> None:
    g = _gh()
    print("\n10. 生成睡眠事件...")
    output_file = _ctx_user_data_json(ctx, "sleep_events.json")
    if os.path.exists(output_file):
        print(f"  用户 {ctx.user_id} 的睡眠事件数据已生成，跳过")
        return
    _init_json_array_file(output_file)
    print(f"  已创建睡眠事件文件：{output_file}（将按条写入）")
    with open("config/config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    user_profile = None
    for profile in config.get("user_profiles", []):
        if profile.get("user_id") == ctx.user_id:
            user_profile = profile
            break
    if not user_profile:
        print(f"  未找到用户 {ctx.user_id} 的配置，跳过生成睡眠事件")
        return
    personality_type_evt = user_profile.get("personalInformation", {}).get("type", "M-L-C")
    date_range = user_profile.get("date", {})
    start_date_str = date_range.get("start")
    end_date_str = date_range.get("end")
    if not start_date_str or not end_date_str:
        print(f"  用户 {ctx.user_id} 缺少日期配置，跳过生成睡眠事件")
        return
    start_date = max(datetime.strptime(start_date_str, "%Y-%m-%d"), ctx.start_date)
    end_date = min(datetime.strptime(end_date_str, "%Y-%m-%d"), ctx.end_date)
    if start_date > end_date:
        print(f"  用户 {ctx.user_id} 在当前日期分片内无睡眠事件可生成，跳过")
        return
    all_events = []
    health_by_date = g._health_data_by_record_date(ctx.user_id)
    current_date = start_date
    total_days = (end_date - start_date).days + 1
    day_i = 0
    written_events_count = 0
    while current_date <= end_date:
        record_date = current_date.strftime("%Y-%m-%d")
        if day_i % 15 == 0:
            print(f"  睡眠事件进度：{record_date}（约 {day_i + 1}/{total_days} 天）…")
        day_i += 1
        real_day = health_by_date.get(record_date)
        if real_day and real_day.get("raw_data", {}).get("sleep_time") and real_day.get(
            "raw_data", {}
        ).get("wake_time"):
            sleep_data = real_day
        else:
            sleep_time_min = user_profile.get("sleepTime", {}).get("min", ["23:00"])[0]
            sleep_time_max = user_profile.get("sleepTime", {}).get("max", ["00:00"])[0]
            awake_time_min = user_profile.get("awakeTime", {}).get("min", ["07:00"])[0]
            awake_time_max = user_profile.get("awakeTime", {}).get("max", ["08:00"])[0]
            sleep_min = datetime.strptime(sleep_time_min, "%H:%M")
            sleep_max = datetime.strptime(sleep_time_max, "%H:%M")
            if sleep_min.hour <= sleep_max.hour:
                sleep_hour = random.randint(sleep_min.hour, sleep_max.hour)
                if sleep_hour == sleep_min.hour:
                    sleep_minute = random.randint(sleep_min.minute, 59)
                elif sleep_hour == sleep_max.hour:
                    sleep_minute = random.randint(0, sleep_max.minute)
                else:
                    sleep_minute = random.randint(0, 59)
            else:
                if random.random() < 0.5:
                    sleep_hour = sleep_min.hour
                    sleep_minute = random.randint(sleep_min.minute, 59)
                else:
                    sleep_hour = sleep_max.hour
                    sleep_minute = random.randint(0, sleep_max.minute)
            awake_min = datetime.strptime(awake_time_min, "%H:%M")
            awake_max = datetime.strptime(awake_time_max, "%H:%M")
            awake_hour = random.randint(awake_min.hour, awake_max.hour)
            if awake_hour == awake_min.hour:
                awake_minute = random.randint(awake_min.minute, 59)
            elif awake_hour == awake_max.hour:
                awake_minute = random.randint(0, awake_max.minute)
            else:
                awake_minute = random.randint(0, 59)
            sleep_time_str = f"{sleep_hour:02d}:{sleep_minute:02d}"
            awake_time_str = f"{awake_hour:02d}:{awake_minute:02d}"
            if awake_hour < sleep_hour:
                wake_date = (current_date + timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                wake_date = record_date
            wake_dt_naive = datetime.strptime(
                f"{wake_date} {awake_time_str}", "%Y-%m-%d %H:%M"
            )
            wake_up_dt = wake_dt_naive + timedelta(minutes=random.randint(12, 42))
            sleep_onset_naive = datetime.strptime(
                f"{record_date} {sleep_time_str}", "%Y-%m-%d %H:%M"
            )
            gross_sleep_span = int(
                max(0, (wake_dt_naive - sleep_onset_naive).total_seconds() // 60)
            )
            target_net = get_total_sleep_minutes(personality_type_evt)
            if gross_sleep_span < 90:
                total_sleep_minutes = target_net
            else:
                waso_like = random.randint(
                    18, min(90, max(19, gross_sleep_span // 4))
                )
                total_sleep_minutes = gross_sleep_span - waso_like
                total_sleep_minutes = int(
                    total_sleep_minutes * 0.58
                    + target_net * 0.42
                    + random.randint(-20, 20)
                )
                total_sleep_minutes = max(
                    200, min(600, total_sleep_minutes, max(0, gross_sleep_span - 8))
                )
            sleep_data = {
                "raw_data": {
                    "sleep_time": f"{record_date}T{sleep_time_str}:00Z",
                    "wake_time": f"{wake_date}T{awake_time_str}:00Z",
                    "wake_up_time": wake_up_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "total_sleep_minutes": total_sleep_minutes,
                }
            }
        events = g.generate_sleep_events(
            sleep_data,
            ctx.user_id,
            record_date,
            None,
            personality_type_evt,
        )
        all_events.extend(events)
        for ev in events:
            if isinstance(ev, dict):
                written_events_count = _append_json_item(output_file, ev)
        print(
            f"  睡眠事件写入进度：天 {day_i}/{total_days}，累计 {written_events_count} 条"
        )
        current_date += timedelta(days=1)
    if all_events:

        def _pipeline_sleep_event_sort_key(ev):
            rd = ev.get("record_date")
            day = health_by_date.get(rd)
            st, we = (None, None)
            if day:
                st, we = g.sleep_local_window_bounds_from_sleep_data(day)
            return (rd, g.session_anchor_event_local_dt(ev, st, we))

        all_events.sort(key=_pipeline_sleep_event_sort_key)
        current_d = None
        order = 0
        for event in all_events:
            event_date = event["record_date"]
            if event_date != current_d:
                current_d = event_date
                order = 0
            event["sort_order"] = order
            order += 1
        g.save_sleep_events_to_file(all_events, ctx.user_id)
        print(f"  为用户 {ctx.user_id} 生成了 {len(all_events)} 个睡眠事件")
    else:
        g.save_sleep_events_to_file(all_events, ctx.user_id)
        print(f"  为用户 {ctx.user_id} 未生成睡眠事件，但创建了空文件")


def pipeline_step_report_audios(ctx: UserGenContext) -> None:
    g = _gh()
    print("\n12. 处理睡眠报告中的audios，添加time字段...")
    if _report_audios_sources_unchanged(ctx):
        print(f"  用户 {ctx.user_id} 的 report_audios 已完成且源文件未变化，跳过")
        return

    sleep_events_file = _ctx_user_data_json(ctx, "sleep_events.json")
    sleep_events = []
    if os.path.exists(sleep_events_file):
        try:
            with open(sleep_events_file, "r", encoding="utf-8") as f:
                sleep_events = json.load(f)
        except Exception as e:
            print(f"  读取用户 {ctx.user_id} 的睡眠事件文件时出错: {str(e)}")
    sleep_report_file = _ctx_user_data_json(ctx, "sleep_report.json")
    sleep_reports = []
    if os.path.exists(sleep_report_file):
        try:
            with open(sleep_report_file, "r", encoding="utf-8") as f:
                sleep_reports = json.load(f)
        except Exception as e:
            print(f"  读取用户 {ctx.user_id} 的睡眠报告文件时出错: {str(e)}")
    if not sleep_events or not sleep_reports:
        print(f"  用户 {ctx.user_id} 缺少睡眠事件或睡眠报告数据，跳过处理")
        return
    health_by_date = g._health_data_by_record_date(ctx.user_id)
    sleep_events_duration_updates = 0
    for report in sleep_reports:
        record_date = report.get("record_date")
        if not record_date or not _date_in_ctx_range(str(record_date), ctx):
            continue
        quality_analysis = report.setdefault("quality_analysis", {})
        auditory = quality_analysis.setdefault("auditory", {})
        sleep_day = health_by_date.get(record_date) or {}
        apnea_count = int((sleep_day.get("raw_data") or {}).get("apnea_count", 0) or 0)
        st, we = g.sleep_local_window_bounds_from_sleep_data(sleep_day)
        audios, assigned_snore_points = g.rebuild_auditory_audios_and_snoring_data_points(
            record_date,
            ctx.user_id,
            sleep_events,
            sleep_day,
        )
        auditory["audios"] = audios
        sleep_events_duration_updates += g.backfill_auditory_event_durations_from_report_audios(
            audios,
            sleep_events,
            ctx.user_id,
            record_date,
            st,
            we,
        )
        auditory["snoring_analysis"] = (
            {"data_points": assigned_snore_points}
            if assigned_snore_points
            else {"data_points": []}
        )
        if apnea_count >= 5:
            auditory["target"] = g.build_apnea_auditory_target_title(record_date)
            auditory["risk_alert"] = f"昨晚出现{apnea_count}次呼吸暂停疑似时间，建议关注。"
        else:
            auditory["target"] = ""
            auditory["risk_alert"] = ""
        # 以下在 audios / snoring_analysis.data_points / target 就绪后再生成听觉文案
        date_ev = [
            e
            for e in sleep_events
            if e.get("record_date") == record_date and e.get("uid") == ctx.user_id
        ]
        sleep_data_like = g.sleep_data_shallow_from_report_for_auditory(report)
        if g.sleepReportAI:
            auditory["module"] = g.generate_auditory_module_via_doubao(
                ctx.user_id, record_date, sleep_data_like, date_ev, auditory
            ) or g.build_auditory_snore_module(audios, record_date)
        else:
            auditory["module"] = g.build_auditory_snore_module(audios, record_date)
    try:
        with open(sleep_report_file, "w", encoding="utf-8") as f:
            json.dump(sleep_reports, f, ensure_ascii=False, indent=2)
        print(f"  已更新用户 {ctx.user_id} 的睡眠报告，添加了time字段")
    except Exception as e:
        print(f"  写回用户 {ctx.user_id} 的睡眠报告文件时出错: {str(e)}")

    if sleep_events_duration_updates > 0:
        try:
            with open(sleep_events_file, "w", encoding="utf-8") as f:
                json.dump(sleep_events, f, ensure_ascii=False, indent=2)
            print(
                f"  已回填用户 {ctx.user_id} 的睡眠事件 duration_sec，共 {sleep_events_duration_updates} 条"
            )
        except Exception as e:
            print(f"  写回用户 {ctx.user_id} 的睡眠事件文件时出错: {str(e)}")

    _write_report_audios_state(ctx)


USER_DATA_PIPELINE_ORDER: Tuple[str, ...] = (
    "health",
    "sleep_events",
    "fitness",
    "environment",
    "vitals",
    "event_feedback",
    "schedule",
    "ai_analysis",
    "sleep_report",
    "report_audios",
)

USER_DATA_PIPELINE_STEPS: Dict[str, Callable[[UserGenContext], None]] = {
    "health": pipeline_step_health,
    "sleep_events": pipeline_step_sleep_events,
    "fitness": pipeline_step_fitness,
    "environment": pipeline_step_environment,
    "event_feedback": pipeline_step_event_feedback,
    "vitals": pipeline_step_vitals,
    "schedule": pipeline_step_schedule,
    "ai_analysis": pipeline_step_ai_analysis,
    "sleep_report": pipeline_step_sleep_report,
    "report_audios": pipeline_step_report_audios,
}


def run_user_data_pipeline(
    ctx: UserGenContext, steps: Optional[Sequence[str]] = None
) -> None:
    """按顺序执行流水线。steps 为 None 时跑全部；否则只跑给定步骤（顺序仍遵循 USER_DATA_PIPELINE_ORDER）。"""
    if steps is None:
        order = USER_DATA_PIPELINE_ORDER
    else:
        allowed = set(USER_DATA_PIPELINE_ORDER)
        unknown = set(steps) - allowed
        if unknown:
            raise ValueError(
                f"未知 pipeline 步骤 {sorted(unknown)}；可选: {list(USER_DATA_PIPELINE_ORDER)}"
            )
        want = set(steps)
        order = tuple(s for s in USER_DATA_PIPELINE_ORDER if s in want)
    for name in order:
        USER_DATA_PIPELINE_STEPS[name](ctx)


def parse_steps_arg(steps_csv: Optional[str]) -> Optional[Tuple[str, ...]]:
    if not steps_csv or not str(steps_csv).strip():
        return None
    parts = [p.strip() for p in str(steps_csv).split(",") if p.strip()]
    return tuple(parts) if parts else None
