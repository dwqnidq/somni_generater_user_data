"""近 14 日睡眠趋势 AI 分析（自 generate_health_data 迁出，供 generate_ai_analysis_14d 使用）。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import List, Optional

from generate_ai import llm_client
from generate_ai.runtime import PROJECT_ROOT
from sleep_report.shared import _variant_pick
from utils import atomic_write_json


def _build_local_ai_analysis(record_date_str: str, sleep_seg: list, sched_seg: list):
    avg_latency = sum(r["raw_data"].get("sleep_latency", 0) for r in sleep_seg) / len(sleep_seg)
    avg_deep = sum(r["raw_data"].get("deep_sleep_ratio", 0) for r in sleep_seg) / len(sleep_seg)
    avg_total = sum(r["raw_data"].get("total_sleep_minutes", 0) for r in sleep_seg) / len(sleep_seg)
    avg_efficiency = sum(r["raw_data"].get("sleep_efficiency", 0) for r in sleep_seg) / len(sleep_seg)
    avg_awake = sum(r["raw_data"].get("awake_ratio", 0) for r in sleep_seg) / len(sleep_seg)

    if avg_efficiency >= 90 and avg_deep >= 20:
        title_pool = ["恢复效率高位", "深睡修复充沛", "夜间节律在线"]
    elif avg_efficiency >= 85:
        title_pool = ["睡眠状态平稳", "节律保持住了", "恢复曲线向好"]
    else:
        title_pool = ["睡眠修复待补", "节律需要微调", "入睡流程待优化"]

    seed = int(record_date_str.replace("-", "")) % 7
    title = title_pool[seed % len(title_pool)]

    sleep_actions = []
    if avg_latency > 25:
        sleep_actions.append("睡前 45 分钟关闭高刺激内容，改为 10 分钟呼吸放松 + 10 分钟低负荷阅读")
    if avg_deep < 16:
        sleep_actions.append("将卧室温度稳定在 19-22℃，并把最后一次含咖啡因饮品提前到 14:00 前")
    if avg_awake > 12:
        sleep_actions.append("睡前 2 小时减少饮水，夜间环境噪音尽量压到 40dB 以下")
    if avg_total < 390:
        sleep_actions.append("未来 7 天把上床时间提前 15 分钟，优先补足总睡眠时长")
    if avg_efficiency < 85:
        sleep_actions.append("仅在有困意时上床；若 20 分钟未入睡，起身到弱光区放松后再回床")
    if not sleep_actions:
        sleep_actions.append("保持当前作息，同时继续固定起床时间，巩固稳定节律")

    energy_bucket = sum([
        avg_total >= 420,
        avg_deep >= 20,
        avg_efficiency >= 88,
        avg_awake <= 10,
        avg_latency <= 20,
    ])
    energy_hint = _variant_pick(
        record_date_str,
        f"ai14d_energy|{energy_bucket}",
        {
            0: ["这段时间白天精力可能明显打折，先保睡眠再提效率。"],
            1: ["这段时间精力偏弱，建议先做减负和作息回稳。"],
            2: ["这段时间精力中低，尽量把高强度任务前置到上午。"],
            3: ["这段时间精力中等，按节奏推进会更稳。"],
            4: ["这段时间精力状态不错，恢复趋势是向上的。"],
            5: ["这段时间精力在线，可以承担更高优先级任务。"],
        }.get(energy_bucket, ["这段时间精力状态可控，建议稳步推进。"]),
    )
    sleep_opening = _variant_pick(
        record_date_str,
        "ai14d_sleep_opening",
        ["把 14 天窗口拉通看，", "看最近 14 天的睡眠趋势，", "从这 14 天的数据看，"],
    )
    sleep_insight = (
        f"{sleep_opening}平均入睡 {avg_latency:.1f} 分钟、深睡 {avg_deep:.1f}%、效率 {avg_efficiency:.1f}%，"
        f"净睡 {avg_total:.0f} 分钟、清醒占比 {avg_awake:.1f}%。{energy_hint}"
        f"优先执行：{sleep_actions[0]}。"
    )
    if len(sleep_actions) > 1:
        sleep_insight += f" 次优先：{sleep_actions[1]}。"

    if sched_seg:
        total_events = len(sched_seg)
        total_duration = sum(int(r.get("duration_minutes", 0) or 0) for r in sched_seg)
        late_events = sum(
            1 for r in sched_seg
            if isinstance(r.get("start_time"), str) and ":" in r["start_time"]
            and int(r["start_time"].strip().split(":")[0]) >= 21
        )
        high_load_events = sum(1 for r in sched_seg if int(r.get("duration_minutes", 0) or 0) >= 90)
        type_counter: dict = {}
        for r in sched_seg:
            et = str(r.get("event_type", "other") or "other")
            type_counter[et] = type_counter.get(et, 0) + 1
        top_types = sorted(type_counter.items(), key=lambda x: x[1], reverse=True)[:3]
        type_str = "、".join(f"{k}{v}次" for k, v in top_types) if top_types else "常规活动"
        schedule_actions = []
        if late_events >= 2:
            schedule_actions.append("把 21:00 后的事务前移到 19:30 前，至少为睡前保留 60 分钟降速区")
        if high_load_events >= 3:
            schedule_actions.append("连续高强度任务后插入 15-20 分钟低负荷缓冲，避免带着兴奋态上床")
        if total_events >= 12:
            schedule_actions.append("将明日任务收敛为 3 个必做项 + 2 个可选项，降低晚间决策负荷")
        if not schedule_actions:
            schedule_actions.append("维持当前日程密度，重点守住“固定起床时间 + 睡前 1 小时不加新任务”")
        schedule_opening = _variant_pick(
            record_date_str,
            "ai14d_schedule_opening",
            ["再看日程侧，", "日程节奏这边，", "活动安排层面，"],
        )
        schedule_insight = (
            f"{schedule_opening}14 天窗口共 {total_events} 条日程、累计 {total_duration} 分钟，主要类型：{type_str}。"
            f"其中 21:00 后安排 {late_events} 条、长时任务(>=90分钟) {high_load_events} 条。"
            f"建议：{schedule_actions[0]}。"
        )
        if len(schedule_actions) > 1:
            schedule_insight += f" 补充：{schedule_actions[1]}。"
    else:
        schedule_insight = (
            "14天窗口缺少日程数据。建议先建立最小可执行节律：固定起床时间、固定晚餐窗口、"
            "睡前 1 小时不新增任务，并连续记录 7 天。"
        )
    return title, sleep_insight, schedule_insight


def _normalize_text_key(text: str) -> str:
    if not text:
        return ""
    return "".join(str(text).strip().lower().split())


def _make_title_candidates(record_date_str: str, avg_efficiency: float, avg_deep: float, avg_latency: float):
    if avg_efficiency >= 90:
        pool = ["深睡节律在线", "恢复效率高位", "睡眠表现亮眼", "夜间修复充足"]
    elif avg_efficiency >= 80:
        pool = ["节律维持稳定", "睡眠状态平稳", "恢复曲线向好", "夜间修复达标"]
    else:
        pool = ["睡眠修复待补", "节律需要微调", "恢复效率偏弱", "夜间质量待升"]
    if avg_latency > 30:
        pool += ["入睡节律偏慢", "睡前降速不足"]
    elif avg_latency <= 15:
        pool += ["入睡启动顺畅", "入睡效率较高"]
    if avg_deep < 15:
        pool += ["深睡储备不足", "深睡比例待提"]
    elif avg_deep >= 20:
        pool += ["深睡修复充沛", "深睡窗口充足"]
    shift = int(record_date_str.replace("-", "")) % len(pool)
    return pool[shift:] + pool[:shift]


def _dedupe_ai_text_with_memory(
    record_date_str, title, sleep_insight, schedule_insight,
    avg_efficiency, avg_deep, avg_latency,
    recent_title_keys, recent_text_keys, memory_size=7,
):
    title_key = _normalize_text_key(title)
    if title_key in recent_title_keys:
        for cand in _make_title_candidates(record_date_str, avg_efficiency, avg_deep, avg_latency):
            cand_key = _normalize_text_key(cand)
            if cand_key not in recent_title_keys:
                title = cand
                title_key = cand_key
                break
    pair_key = _normalize_text_key(f"{sleep_insight}|{schedule_insight}")
    if pair_key in recent_text_keys:
        sleep_insight = f"从本周期（含当天起14天）看，{sleep_insight}"
        schedule_insight = f"结合日程节奏，{schedule_insight}"
        pair_key = _normalize_text_key(f"{sleep_insight}|{schedule_insight}")
        if pair_key in recent_text_keys:
            schedule_insight = f"{schedule_insight} 建议以小步调整替代一次性大改。"
            pair_key = _normalize_text_key(f"{sleep_insight}|{schedule_insight}")
    recent_title_keys.append(title_key)
    recent_text_keys.append(pair_key)
    if len(recent_title_keys) > memory_size:
        recent_title_keys.pop(0)
    if len(recent_text_keys) > memory_size:
        recent_text_keys.pop(0)
    return title, sleep_insight, schedule_insight


def compact_sleep_records_for_trend_14d_prompt(sleep_seg: list) -> list:
    out = []
    for r in sorted(sleep_seg or [], key=lambda x: str(x.get("record_date") or "")):
        if not isinstance(r, dict):
            continue
        rd = r.get("raw_data") or {}
        out.append({
            "record_date": r.get("record_date"),
            "apnea_count": rd.get("apnea_count"),
            "average_heartbeat": rd.get("average_heartbeat"),
            "average_respiration": rd.get("average_respiration"),
            "awake_ratio": rd.get("awake_ratio"),
            "deep_sleep_ratio": rd.get("deep_sleep_ratio"),
            "light_sleep_ratio": rd.get("light_sleep_ratio"),
            "rem_ratio": rd.get("rem_ratio"),
            "sleep_score": rd.get("sleep_score"),
            "total_sleep_minutes": rd.get("total_sleep_minutes"),
            "sleep_time": rd.get("sleep_time"),
            "wake_time": rd.get("wake_time"),
            "sleep_latency": rd.get("sleep_latency"),
            "sleep_efficiency": rd.get("sleep_efficiency"),
        })
    return out


def compact_schedule_records_for_trend_14d_prompt(sched_seg: list) -> list:
    out = []
    for r in sorted(sched_seg or [], key=lambda x: str(x.get("event_date") or "")):
        if not isinstance(r, dict):
            continue
        out.append({
            "event_date": r.get("event_date"),
            "event_type": r.get("event_type"),
            "event_name": r.get("event_name"),
            "start_time": r.get("start_time"),
            "end_time": r.get("end_time"),
            "duration_minutes": r.get("duration_minutes"),
        })
    return out


def generate_ai_analysis(
    user_id: str,
    use_doubao: bool = False,
    output_dir: str = "output",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    stream_output_file: Optional[str] = None,
) -> List[dict]:
    def _append_stream_row(row: dict) -> None:
        if not stream_output_file:
            return
        rows: list = []
        if os.path.exists(stream_output_file):
            try:
                with open(stream_output_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, list):
                    rows = loaded
            except Exception:
                rows = []
        rows.append(row)
        atomic_write_json(stream_output_file, rows)

    health_file = os.path.join(output_dir, f"{user_id}_health_data.json")
    calendar_file = os.path.join(output_dir, f"{user_id}_calendar_events.json")
    schedule_file_legacy = os.path.join(output_dir, f"{user_id}_schedule_data.json")

    sleep_records: list = []
    if os.path.exists(health_file):
        with open(health_file, "r", encoding="utf-8") as f:
            sleep_records = json.load(f)
    schedule_records: list = []
    if os.path.exists(calendar_file):
        with open(calendar_file, "r", encoding="utf-8") as f:
            schedule_records = json.load(f)
    elif os.path.exists(schedule_file_legacy):
        with open(schedule_file_legacy, "r", encoding="utf-8") as f:
            schedule_records = json.load(f)
    if isinstance(schedule_records, list):
        schedule_records = [r for r in schedule_records if isinstance(r, dict)]
    else:
        schedule_records = []

    if not sleep_records:
        return []

    start_dt = end_dt = None
    if start_date:
        start_dt = datetime.strptime(str(start_date)[:10], "%Y-%m-%d")
    if end_date:
        end_dt = datetime.strptime(str(end_date)[:10], "%Y-%m-%d")

    sleep_records.sort(key=lambda x: x.get("record_date", ""))
    all_sleep_dates = sorted({r["record_date"] for r in sleep_records})
    if start_dt or end_dt:
        filtered = []
        for d in all_sleep_dates:
            try:
                d_dt = datetime.strptime(str(d)[:10], "%Y-%m-%d")
            except ValueError:
                continue
            if start_dt and d_dt < start_dt:
                continue
            if end_dt and d_dt > end_dt:
                continue
            filtered.append(d)
        all_sleep_dates = filtered
        if not all_sleep_dates:
            return []

    sleep_by_date: dict = {}
    for r in sleep_records:
        sleep_by_date.setdefault(r["record_date"], []).append(r)
    schedule_by_date: dict = {}
    for r in schedule_records:
        d = r.get("event_date", "")
        if d:
            schedule_by_date.setdefault(d, []).append(r)

    results: list = []
    recent_title_keys: list = []
    recent_text_keys: list = []

    for record_date_str in all_sleep_dates:
        record_date = datetime.strptime(record_date_str, "%Y-%m-%d")
        window_dates = {
            (record_date - timedelta(days=offset)).strftime("%Y-%m-%d")
            for offset in range(14)
        }
        sleep_seg = [r for d in window_dates for r in sleep_by_date.get(d, [])]
        sched_seg = [r for d in window_dates for r in schedule_by_date.get(d, [])]
        if not sleep_seg or len(sleep_seg) < 14:
            continue

        avg_latency = sum(r["raw_data"].get("sleep_latency", 0) for r in sleep_seg) / len(sleep_seg)
        avg_deep = sum(r["raw_data"].get("deep_sleep_ratio", 0) for r in sleep_seg) / len(sleep_seg)
        avg_total = sum(r["raw_data"].get("total_sleep_minutes", 0) for r in sleep_seg) / len(sleep_seg)
        avg_efficiency = sum(r["raw_data"].get("sleep_efficiency", 0) for r in sleep_seg) / len(sleep_seg)
        avg_awake = sum(r["raw_data"].get("awake_ratio", 0) for r in sleep_seg) / len(sleep_seg)

        if use_doubao:
            period_start = (record_date - timedelta(days=13)).strftime("%Y-%m-%d")
            title, sleep_insight, schedule_insight = _build_local_ai_analysis(
                record_date_str, sleep_seg, sched_seg
            )
            template_path = os.path.join(PROJECT_ROOT, "prompt", "sleep_trend_14d_analysis.md")
            instruction = llm_client.load_prompt_instruction("sleep_trend_14d_analysis.md")
            if instruction and llm_client.has_api_key():
                payload = {
                    "anchor_record_date": record_date_str,
                    "analysis_period": {"start": period_start, "end": record_date_str},
                    "sleep_records_14d": compact_sleep_records_for_trend_14d_prompt(sleep_seg),
                    "schedule_records_14d": compact_schedule_records_for_trend_14d_prompt(sched_seg),
                }
                prompt = (
                    "以下为真实输入数据（JSON）。请仅依据这些数据输出 JSON 对象，"
                    "字段必须为 `title`、`sleep_insight`、`schedule_insight`，不要附加解释文本。"
                    "不要照抄文档中的示例数值与文案。\n\n"
                    + json.dumps(payload, ensure_ascii=False)
                )
                insight_temp = float(
                    os.getenv("QWEN_AI_ANALYSIS_TEMPERATURE", os.getenv("DOUBAO_AI_ANALYSIS_TEMPERATURE", "0.82"))
                )
                api_result = llm_client.call_qwen_api(
                    prompt,
                    system_prompt=instruction,
                    max_tokens=2048,
                    temperature=min(1.0, max(0.0, insight_temp)),
                )
                if api_result:
                    try:
                        parsed = llm_client.parse_json_from_response(api_result)
                        if isinstance(parsed, dict):
                            title = str(parsed.get("title", title) or title).strip() or title
                            sleep_insight = str(parsed.get("sleep_insight", sleep_insight) or sleep_insight).strip() or sleep_insight
                            schedule_insight = str(
                                parsed.get("schedule_insight", schedule_insight) or schedule_insight
                            ).strip() or schedule_insight
                    except Exception as e:
                        print(f"  解析模型返回内容失败: {e}，使用本地 AI 分析结果")
        else:
            title, sleep_insight, schedule_insight = _build_local_ai_analysis(
                record_date_str, sleep_seg, sched_seg
            )

        title, sleep_insight, schedule_insight = _dedupe_ai_text_with_memory(
            record_date_str, title, sleep_insight, schedule_insight,
            avg_efficiency, avg_deep, avg_latency,
            recent_title_keys, recent_text_keys,
        )
        now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        row = {
            "uid": user_id,
            "record_date": record_date_str,
            "title": title,
            "sleep_insight": sleep_insight,
            "schedule_insight": schedule_insight,
            "create_time": now_iso,
            "update_time": now_iso,
            "language": "zh",
        }
        results.append(row)
        _append_stream_row(row)
    return results
