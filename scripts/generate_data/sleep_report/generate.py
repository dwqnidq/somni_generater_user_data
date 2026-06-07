"""睡眠报告主编排文件：调用各子模块组装完整报告数据（纯数据，不含 LLM）。

直接运行: python scripts/generate_data/sleep_report/generate.py
"""

from __future__ import annotations

import glob
import json
import os
import random
import sys

# 支持直接运行: python scripts/generate_data/sleep_report/generate.py
if __name__ == "__main__" and __package__ is None:
    _this_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(os.path.dirname(os.path.dirname(_this_dir)))
    sys.path.insert(0, _project_root)
    __package__ = "scripts.generate_data.sleep_report"

from .time_utils import (
    calculate_duration,
    utc_to_local,
    format_time_to_hhmm,
    generate_iso_date,
)
from .sleep_score import (
    build_sleep_structure_metrics,
    get_stage_status as _get_stage_status,
    sleep_report_structure_minutes_and_percents,
)
from .body_battery import generate_body_battery, get_body_battery_status
from .sleep_helpers import build_sleep_events_index, night_wake_episodes_for_prompts
from .auditory import (
    generate_auditory,
    build_auditory_snore_module,
    _flatten_audio_groups,
)
from .environment_summary import generate_environment_summary
from .notice import generate_notice
from .title_summary import (
    pick_main_title,
    build_main_local_summary,
    get_main_title_image_url,
)


# ---------------------------------------------------------------------------
# 单条睡眠报告生成
# ---------------------------------------------------------------------------

def generate_sleep_report(
    sleep_data,
    user_id,
    session_id,
    sleep_standard,
    personality_type="M-L-C",
    sleep_events_index=None,
    recent_titles=None,
    output_dir="output",
):
    """根据睡眠数据生成单条睡眠报告（纯数据，不调用 LLM）。"""
    raw_data = sleep_data["raw_data"]
    record_date = sleep_data.get("record_date", "")

    # 1. 睡眠结构：percent=health 占比，minutes=TIB×占比
    (
        sleep_structure,
        deep_sleep_minutes,
        aw_pct_tib,
        deep_pct_tib,
        light_pct_tib,
        rem_pct_tib,
    ) = build_sleep_structure_metrics(sleep_data, sleep_standard)
    total_sleep_minutes = raw_data.get("total_sleep_minutes", 0)

    # 2. 时间计算
    bed_time = raw_data.get("bed_time", "")
    wake_up_time = raw_data.get("wake_up_time", "")
    sleep_latency = raw_data.get("sleep_latency", 0)
    sleep_efficiency = raw_data.get("sleep_efficiency", 0)
    wake_time = raw_data.get("wake_time", "")
    apnea_count = int(raw_data.get("apnea_count", 0) or 0)

    time_in_bed_minutes = calculate_duration(bed_time, wake_up_time, record_date or None)
    awake_after_onset_minutes = calculate_duration(wake_time, wake_up_time, record_date or None)

    bed_time_local = utc_to_local(bed_time)
    wake_up_time_local = utc_to_local(wake_up_time)
    bedtime_str = format_time_to_hhmm(bed_time_local)
    wake_up_time_str = format_time_to_hhmm(wake_up_time_local)

    # 4. 身体电量
    body_battery = generate_body_battery(sleep_data, personality_type=personality_type)
    body_battery_status = get_body_battery_status(body_battery)

    # 5. 睡眠事件查找
    if sleep_events_index is not None:
        date_sleep_events = sleep_events_index.get(record_date, [])
    else:
        date_sleep_events = []
        sleep_events_file = os.path.join("output", f"{user_id}_sleep_events.json")
        if os.path.exists(sleep_events_file):
            try:
                with open(sleep_events_file, "r", encoding="utf-8") as f:
                    all_events = json.load(f)
                date_sleep_events = [
                    e
                    for e in all_events
                    if e.get("record_date") == record_date and (not user_id or e.get("uid") == user_id)
                ]
            except Exception:
                date_sleep_events = []

    # 6. 听觉模块
    auditory = generate_auditory(
        sleep_data,
        user_id=user_id,
        sleep_events_index=sleep_events_index,
        output_dir=output_dir,
    )
    auditory_snore_module = build_auditory_snore_module(auditory.get("audios", []), record_date)

    # 7. 环境摘要
    environment_summary = generate_environment_summary(user_id, record_date, output_dir=output_dir)

    # 8. LLM 步骤跳过 → 空模块
    final_pain_module = []
    final_quality_module = []

    # 9. 主标题 + 本地摘要
    night_wake_eps = night_wake_episodes_for_prompts(sleep_data)
    main_title = pick_main_title(
        personality_type,
        sleep_data,
        light_pct_tib,
        deep_pct_tib,
        rem_pct_tib,
        sleep_latency,
        sleep_efficiency,
        apnea_count,
        recent_titles=recent_titles,
        report_score=body_battery,
    )
    main_summary = build_main_local_summary(
        main_title,
        deep_pct_tib,
        light_pct_tib,
        rem_pct_tib,
        sleep_latency,
        night_wake_eps,
        total_sleep_minutes=total_sleep_minutes,
        sleep_efficiency=sleep_efficiency,
        awake_percent=aw_pct_tib,
        record_date=record_date,
    )
    main_url = get_main_title_image_url(main_title)

    # 10. 组装报告 dict
    report = {
        "uid": user_id,
        "record_date": record_date,
        "main": {
            "title": main_title,
            "url": main_url,
            "summary": main_summary,
        },
        "sleep_summary": {
            "body_battery": body_battery,
            "body_battery_status": body_battery_status,
            "total_minutes": total_sleep_minutes,
            "deep_sleep_minutes": deep_sleep_minutes,
            "avg_heart_rate": raw_data.get("average_heartbeat", 0),
            "avg_respiratory_rate": raw_data.get("average_respiration", 0),
        },
        "pain_point_analysis": {
            "module": final_pain_module,
            "environment_summary": environment_summary,
        },
        "quality_analysis": {
            "module": final_quality_module,
            "sleep_structure": sleep_structure,
            "sleep_quality": {
                "time_in_bed_minutes": time_in_bed_minutes,
                "sleep_onset_latency_minutes": sleep_latency,
                "sleep_efficiency": sleep_efficiency,
                "bedtime": bedtime_str,
                "wake_up_time": wake_up_time_str,
                "awake_after_onset_minutes": awake_after_onset_minutes,
            },
            "auditory": {
                "module": auditory_snore_module,
                "target": auditory.get("target", ""),
                "risk_alert": auditory.get("risk_alert", ""),
                "snoring_analysis": auditory.get("snoring_analysis", {"data_points": []}),
                "audios": auditory.get("audios", []),
            },
        },
        "create_time": generate_iso_date(),
        "update_time": generate_iso_date(),
        "language": "zh",
    }

    # 11. 通知（本地版）
    notice = generate_notice(
        total_sleep_minutes,
        deep_pct_tib,
        sleep_latency,
        aw_pct_tib,
        sleep_efficiency,
        personality_type,
        record_date=record_date,
    )
    if not isinstance(notice, dict):
        notice = {"title": "Bio-OS 算法已进化", "content": ""}
    notice["title"] = "Bio-OS 算法已进化"
    report["notice"] = notice

    return report


# ---------------------------------------------------------------------------
# sleep_talk 去重后处理
# ---------------------------------------------------------------------------

def find_replacement_audio(item, audio_data, used_urls):
    """从同类型音频中找替换。"""
    url = item.get("url", "")

    audio_type = None
    if url in [a.get("url", "") for a in _flatten_audio_groups(audio_data.get("audioUrl", []))]:
        audio_type = "audioUrl"
    elif url in [a.get("url", "") for a in _flatten_audio_groups(audio_data.get("sleepTalkingUrl", []))]:
        audio_type = "sleepTalkingUrl"
    elif url in [a.get("url", "") for a in _flatten_audio_groups(audio_data.get("coughUrl", []))]:
        audio_type = "coughUrl"

    if not audio_type:
        all_audio = []
        all_audio.extend(_flatten_audio_groups(audio_data.get("audioUrl", [])))
        all_audio.extend(_flatten_audio_groups(audio_data.get("sleepTalkingUrl", [])))
        all_audio.extend(_flatten_audio_groups(audio_data.get("coughUrl", [])))
        valid_audio = [a for a in all_audio if (a.get("duration_sec", 0) or 0) > 0 and a.get("url", "") not in used_urls]
        if valid_audio:
            return random.choice(valid_audio)
        return None

    audio_list = _flatten_audio_groups(audio_data.get(audio_type, []))
    valid_audio = [a for a in audio_list if (a.get("duration_sec", 0) or 0) > 0 and a.get("url", "") not in used_urls]
    if valid_audio:
        return random.choice(valid_audio)

    other_audio = []
    for key in ["audioUrl", "sleepTalkingUrl", "coughUrl"]:
        if key != audio_type:
            other_audio.extend(_flatten_audio_groups(audio_data.get(key, [])))
    valid_other = [a for a in other_audio if (a.get("duration_sec", 0) or 0) > 0 and a.get("url", "") not in used_urls]
    if valid_other:
        return random.choice(valid_other)

    return None


def check_and_process_sleep_talk(reports):
    """检查和处理睡眠报告中的 sleep_talk 数据重复问题。"""
    audio_file = "qiniu/audio.json"
    if not os.path.exists(audio_file):
        return reports

    try:
        with open(audio_file, "r", encoding="utf-8") as f:
            audio_data = json.load(f)
    except Exception as e:
        print(f"读取audio.json文件时出错: {str(e)}")
        return reports

    # 按 14 天分组
    groups = []
    for i in range(0, len(reports), 14):
        groups.append(reports[i : i + 14])

    for group in groups:
        used_urls = set()
        for report in group:
            sleep_talk = report.get("quality_analysis", {}).get("auditory", {}).get("sleep_talk", [])
            for item in sleep_talk:
                used_urls.add(item.get("url", ""))

        for report in group:
            sleep_talk = report.get("quality_analysis", {}).get("auditory", {}).get("sleep_talk", [])
            new_sleep_talk = []
            for item in sleep_talk:
                url = item.get("url", "")
                if url in [t.get("url", "") for t in new_sleep_talk]:
                    new_item = find_replacement_audio(item, audio_data, used_urls)
                    if new_item:
                        new_sleep_talk.append(new_item)
                        used_urls.add(new_item.get("url", ""))
                elif url in used_urls:
                    new_item = find_replacement_audio(item, audio_data, used_urls)
                    if new_item:
                        new_sleep_talk.append(new_item)
                        used_urls.add(new_item.get("url", ""))
                else:
                    new_sleep_talk.append(item)
                    used_urls.add(url)

            if "quality_analysis" in report:
                if "auditory" in report["quality_analysis"]:
                    report["quality_analysis"]["auditory"]["sleep_talk"] = new_sleep_talk
                else:
                    report["quality_analysis"]["auditory"] = {"sleep_talk": new_sleep_talk}
            else:
                report["quality_analysis"] = {"auditory": {"sleep_talk": new_sleep_talk}}

    # 全局二次去重
    all_used_urls = set()
    for report in reports:
        sleep_talk = report.get("quality_analysis", {}).get("auditory", {}).get("sleep_talk", [])
        for i, item in enumerate(sleep_talk):
            url = item.get("url", "")
            if url in all_used_urls:
                new_item = find_replacement_audio(item, audio_data, all_used_urls)
                if new_item:
                    sleep_talk[i] = new_item
                    all_used_urls.add(new_item.get("url", ""))
            else:
                all_used_urls.add(url)

    return reports


# ---------------------------------------------------------------------------
# 批量入口
# ---------------------------------------------------------------------------

def generate_sleep_reports(config_file="config/config.json", output_dir="output", force=False):
    """批量生成睡眠报告：加载配置 → 遍历用户 health_data → 逐日生成 → 保存 JSON。"""
    if not os.path.exists(config_file):
        print(f"配置文件 {config_file} 不存在")
        return

    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    sleep_standard = config.get(
        "sleepStandard",
        {"deep": [15, 25], "light": [45, 60], "rem": [20, 25], "awake": 10},
    )

    health_data_files = glob.glob(os.path.join(output_dir, "*_health_data.json"))
    print(f"找到 {len(health_data_files)} 个健康数据文件")
    for file in health_data_files:
        print(f"  - {file}")

    if not health_data_files:
        print("未找到健康数据文件")
        return

    for i, health_data_file in enumerate(health_data_files):
        print(f"处理文件 {i+1}/{len(health_data_files)}: {health_data_file}")
        if not os.path.exists(health_data_file):
            print(f"睡眠数据文件 {health_data_file} 不存在")
            continue

        user_id = os.path.basename(health_data_file).replace("_health_data.json", "")

        output_file = os.path.join(output_dir, f"{user_id}_sleep_report.json")
        if not force and os.path.exists(output_file):
            print(f"用户 {user_id} 的睡眠报告已生成，跳过")
            continue

        try:
            with open(health_data_file, "r", encoding="utf-8") as f:
                sleep_data_list = json.load(f)

            if not sleep_data_list:
                print(f"文件 {health_data_file} 中没有睡眠数据")
                continue
            sleep_data_list.sort(key=lambda row: str(row.get("record_date") or ""))

            reports = []
            print(f"  用户ID: {user_id}")

            session_id = ""
            personality_type = "M-L-C"
            for user in config.get("user_profiles", []):
                if user.get("user_id") == user_id:
                    session_id = user.get("session_id", "")
                    personality_type = user.get("personalInformation", {}).get("type", "M-L-C")
                    break
            print(f"  Session ID: {session_id}")
            sleep_events_index = build_sleep_events_index(user_id, output_dir=output_dir)

            recent_titles_window = []

            for j, sleep_data in enumerate(sleep_data_list):
                if j % 5 == 0:
                    print(f"  处理第 {j+1} 条睡眠数据")
                try:
                    report = generate_sleep_report(
                        sleep_data,
                        user_id,
                        session_id,
                        sleep_standard,
                        personality_type,
                        sleep_events_index,
                        recent_titles=recent_titles_window,
                    )
                    reports.append(report)
                    used_title = report.get("main", {}).get("title")
                    if used_title:
                        recent_titles_window.append(used_title)
                        if len(recent_titles_window) > 2:
                            recent_titles_window.pop(0)
                except Exception as e:
                    print(f"    处理第 {j+1} 条数据时出错: {str(e)}")
                    continue

            reports = check_and_process_sleep_talk(reports)

            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(reports, f, ensure_ascii=False, indent=2)

            print(f"  睡眠报告已生成并处理，保存到 {output_file}")
        except Exception as e:
            print(f"  处理文件 {health_data_file} 时出错: {str(e)}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    generate_sleep_reports()
