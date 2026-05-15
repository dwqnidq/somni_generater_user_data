"""本地通知生成。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from .shared import _variant_pick
from .auditory import _sleep_metrics_for_auditory_prompt

# notice.py 位于 …/scripts/generate_data/sleep_report/，向上 3 级为仓库根（与 generate.py 一致）
_REPO_ROOT = Path(__file__).resolve().parents[3]
_UPLOADED_IMAGES_JSON = _REPO_ROOT / "qiniu" / "uploaded_images.json"
_LEGACY_UPLOADED_IMAGES_JSON = Path(__file__).resolve().parent / "qiniu" / "uploaded_images.json"


def generate_ai_evidence(sleep_data):
    """根据睡眠数据生成AI建议"""
    # 直接使用默认值，不调用API
    description = "系统监测到睡眠状态良好，建议保持当前作息习惯。"

    return [{
        "title": "Bio-OS 算法已进化",
        "description": description,
        "action_text": "一键应用并期待今晚"
    }]


def get_image_url_by_name(name):
    """根据称号从 uploaded_images.json 取 URL；配置里多为「称号.png」，兼容无后缀匹配。"""
    if not name:
        return ""
    if _UPLOADED_IMAGES_JSON.is_file():
        json_path = _UPLOADED_IMAGES_JSON
    elif _LEGACY_UPLOADED_IMAGES_JSON.is_file():
        json_path = _LEGACY_UPLOADED_IMAGES_JSON
    else:
        json_path = _UPLOADED_IMAGES_JSON
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            images = json.load(f)
        candidates = [name]
        if not name.endswith('.png'):
            candidates.append(f'{name}.png')
        for item in images:
            fn = (item.get('file') or '').strip()
            if not fn:
                continue
            base = fn.rsplit('.', 1)[0] if '.' in fn else fn
            if fn in candidates or base == name:
                return item.get('url', '') or ''
    except Exception:
        pass
    return ''


def generate_notice(
    total_sleep_minutes,
    deep_percent,
    sleep_latency,
    awake_percent,
    sleep_efficiency,
    personality_type,
    record_date="",
):
    """生成固定标题下的 notice.content：口语化、可执行、基于当晚数据。"""
    rd = record_date or ""
    need_sleep_aid = bool(
        sleep_latency > 20
        or deep_percent < 16
        or sleep_efficiency < 85
        or awake_percent > 12
        or total_sleep_minutes < 390
    )

    sleep_audio = _variant_pick(
        rd,
        "notice_sound_sleep",
        [
            ("静域阿尔法", "阿尔法波 8-10Hz"),
            ("月汐白噪", "粉噪 / 白噪"),
            ("林间雨幕", "轻雨与树叶环境音"),
            ("安澜海潮", "低频海浪声"),
        ],
    )
    wake_audio = _variant_pick(
        rd,
        "notice_sound_wake",
        [
            ("唤醒晨钟", "教堂钟声"),
            ("曦光晨鸟", "晨鸟鸣叫"),
            ("山谷清铃", "清脆风铃"),
            ("晨练节拍", "轻快节律音"),
        ],
    )

    sleep_light = _variant_pick(
        rd,
        "notice_light_sleep",
        [
            ("极暗红光", "1800K"),
            ("琥珀夜灯", "2200K"),
            ("暖金落日光", "2700K"),
        ],
    )
    wake_light = _variant_pick(
        rd,
        "notice_light_wake",
        [
            ("晨曦冷白", "6500K"),
            ("高空日光", "6800K"),
            ("清醒天光", "7000K"),
        ],
    )

    sleep_scent = _variant_pick(
        rd,
        "notice_scent_sleep",
        [
            ("宁夜薰衣", "薰衣草"),
            ("静林雪松", "雪松"),
            ("晚风洋甘", "洋甘菊"),
            ("柔雾檀香", "檀香"),
            ("晚安佛手", "佛手柑"),
        ],
    )
    wake_scent = _variant_pick(
        rd,
        "notice_scent_wake",
        [
            ("活力薄荷", "薄荷"),
            ("晨醒柠光", "柠檬"),
            ("晴空迷迭", "迷迭香"),
            ("清新葡橙", "葡萄柚"),
            ("暖阳甜橙", "甜橙"),
        ],
    )

    pre_sleep_min = 35 if need_sleep_aid else 25
    wake_after_min = 5 if need_sleep_aid else 8
    # 统一生成可执行动作，不再使用"声/光/味："设备日志式表达。
    sound_action = (
        f"睡前先放 {sleep_audio[0]}（{sleep_audio[1]}）{pre_sleep_min} 分钟，音量压在 35-45dB；"
        f"起床前后再切到 {wake_audio[0]}（{wake_audio[1]}）{wake_after_min} 分钟，帮助清醒过渡"
    )
    light_action = (
        f"睡前 40 分钟把主灯调成 {sleep_light[0]}（{sleep_light[1]}，15-30 lux），"
        f"起床后 10 分钟内开 {wake_light[0]}（{wake_light[1]}，350-500 lux）维持 15 分钟"
    )
    scent_action = (
        f"入睡阶段用 {sleep_scent[0]}（{sleep_scent[1]}）扩香 20 分钟，"
        f"晨起洗漱前补一轮 {wake_scent[0]}（{wake_scent[1]}）10 分钟"
    )
    openings = _variant_pick(
        rd,
        "notice_opening",
        [
            "昨晚这组数据我先帮你划重点：",
            "你昨晚的睡眠表现我看过了，重点在这里：",
            "先说结论，昨晚睡眠有两个关键信号：",
        ],
    )
    metric_line = (
        f"净睡 {int(total_sleep_minutes)} 分钟，深睡 {int(deep_percent)}%，效率 {int(sleep_efficiency)}%。"
    )
    guidance_lead = _variant_pick(
        rd,
        "notice_guidance_lead",
        [
            "今晚直接按这 3 步做就行：",
            "今晚你可以这样落地调整：",
            "今晚先别求复杂，按下面三步执行：",
        ],
    )
    content = f"{openings}{metric_line}{guidance_lead}{sound_action}；{light_action}；{scent_action}。"
    return {"title": "Bio-OS 算法已进化", "content": content}


def _prev_calendar_date_str(record_date_str):
    """将 YYYY-MM-DD 的 record_date 转为前一自然日字符串；解析失败返回 None。"""
    if not record_date_str or not isinstance(record_date_str, str):
        return None
    s = record_date_str.strip()[:10]
    if len(s) != 10:
        return None
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (d - timedelta(days=1)).isoformat()


def _health_row_for_record_date(user_id, record_date, output_dir="output"):
    """从 output 下 health_data 列表中取出指定 record_date 的一行；不存在则 None。"""
    if not user_id or not record_date:
        return None
    od = output_dir or "output"
    path = os.path.join(od, f"{user_id}_health_data.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)
    except Exception:
        return None
    if not isinstance(rows, list):
        return None
    target = str(record_date)
    for r in rows:
        if isinstance(r, dict) and str(r.get("record_date")) == target:
            return r
    return None


def build_notice_yesterday_sleep_block(user_id, record_date, output_dir="output"):
    """
    按 record_date 的**前一自然日**在 health 文件中查找该用户昨日睡眠行；
    找到则返回可嵌入 notice 模板的若干行（含 yesterday_sleep_metrics），否则返回空串。
    """
    prev_rd = _prev_calendar_date_str(record_date or "")
    if not prev_rd:
        return ""
    row = _health_row_for_record_date(user_id, prev_rd, output_dir)
    if not row:
        return ""
    ym = _sleep_metrics_for_auditory_prompt(row)
    return (
        f"- yesterday_record_date（用户上一自然日的睡眠记录日期）: {prev_rd}\n"
        f"- yesterday_sleep_metrics（昨日睡眠指标）: {json.dumps(ym, ensure_ascii=False)}\n"
    )
