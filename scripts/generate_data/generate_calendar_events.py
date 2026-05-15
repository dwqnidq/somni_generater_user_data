"""
根据各人格日程设定，生成每位用户的日历事件数据。
- 日期范围取自 health_data_personas_config.json 中该人格的 date_range（end 含当天），
  与 generate_persona_health_data 一致；可选 start_date_override / end_date_override 再收窄。
- 区间内第一天用「日程模板第一天」，之后与次日交替循环；并非每个自然日都会生成事件
  （首日固定有事件，其余日按 user_id+日期 确定性稀疏，约七成有日程）。
- 输出到 output/{user_id}_calendar_events.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timedelta
from typing import Any

# ── 日程数据（直接从 docs/personas/各人格日程设定.md 提取） ──────────────────

SCHEDULES = {
    "完美主义百灵鸟": {
        "user_id": "69aea593af5e6cbf08027964",
        "days": [
            [  # 第一天
                {"start": "06:15", "end": "06:45", "name": "晨跑", "type": "exercise"},
                {"start": "07:00", "end": "07:25", "name": "竞品信息速览与差异点记录", "type": "work"},
                {"start": "09:00", "end": "10:15", "name": "主视觉方向对齐会议", "type": "meeting"},
                {"start": "10:45", "end": "12:00", "name": "大促执行方案初稿输出", "type": "work"},
                {"start": "14:00", "end": "15:15", "name": "交付节点与风险对齐会议", "type": "meeting"},
                {"start": "15:45", "end": "17:00", "name": "活动 ROI 简报整理与汇报", "type": "work"},
                {"start": "17:30", "end": "18:15", "name": "跨组日报收敛与明日优先级", "type": "work"},
                {"start": "20:30", "end": "21:15", "name": "晚间刺激清单勾选（屏息/消息）", "type": "work"},
            ],
            [  # 第二天
                {"start": "06:15", "end": "06:45", "name": "快走", "type": "exercise"},
                {"start": "07:00", "end": "07:30", "name": "会议纪要分发与责任人确认", "type": "work"},
                {"start": "09:30", "end": "10:45", "name": "落地视觉评审与意见定稿", "type": "meeting"},
                {"start": "11:15", "end": "12:15", "name": "合作框架沟通与边界确认", "type": "meeting"},
                {"start": "14:00", "end": "15:15", "name": "活动 FAQ 评审与客服口径定稿", "type": "meeting"},
                {"start": "15:45", "end": "17:00", "name": "甘特图更新与风险项标注", "type": "work"},
                {"start": "17:30", "end": "18:00", "name": "法务口径邮件确认与归档", "type": "work"},
                {"start": "20:00", "end": "20:40", "name": "明日会议材料包整理", "type": "work"},
            ],
        ],
    },
    "敏感的晨间鹿": {
        "user_id": "69aea63eaf5e6cbf08027965",
        "days": [
            [  # 第一天
                {"start": "06:00", "end": "06:20", "name": "伸展操", "type": "exercise"},
                {"start": "09:00", "end": "10:45", "name": "章节审稿批注（上半场）", "type": "work"},
                {"start": "11:00", "end": "11:35", "name": "修订意见清单与回传节点", "type": "work"},
                {"start": "14:30", "end": "15:30", "name": "作者沟通与修改方向确认", "type": "meeting"},
                {"start": "15:45", "end": "16:30", "name": "版权页与引用格式抽检闭环", "type": "work"},
            ],
            [  # 第二天
                {"start": "06:00", "end": "06:20", "name": "跳绳", "type": "exercise"},
                {"start": "09:30", "end": "11:00", "name": "三校意见整合与问题清单输出", "type": "work"},
                {"start": "11:10", "end": "11:40", "name": "印厂进度电话与备忘邮件", "type": "meeting"},
                {"start": "14:00", "end": "15:15", "name": "月度选题讨论与方向收敛", "type": "meeting"},
                {"start": "15:30", "end": "16:15", "name": "目录层级与链接一致性复核", "type": "work"},
            ],
        ],
    },
    "效率至上考拉": {
        "user_id": "69aea6d8af5e6cbf08027966",
        "days": [
            [  # 第一天
                {"start": "06:00", "end": "06:25", "name": "瑜伽", "type": "exercise"},
                {"start": "07:30", "end": "08:45", "name": "物流日报复核与排期冲突消解", "type": "work"},
                {"start": "09:00", "end": "10:30", "name": "仓配自动化方案评审定版", "type": "meeting"},
                {"start": "13:00", "end": "14:30", "name": "备料计划评审与交期谈判", "type": "meeting"},
                {"start": "14:45", "end": "16:00", "name": "供应链风险预案更新", "type": "work"},
                {"start": "16:30", "end": "17:30", "name": "周风险汇报与决策建议", "type": "meeting"},
            ],
            [  # 第二天
                {"start": "06:00", "end": "06:25", "name": "慢跑", "type": "exercise"},
                {"start": "08:30", "end": "10:15", "name": "集团季会对齐与行动项拆解", "type": "meeting"},
                {"start": "10:30", "end": "12:00", "name": "Q2 运营报告撰写与定稿", "type": "work"},
                {"start": "13:00", "end": "14:15", "name": "ERP 迁移节点复核与确认", "type": "work"},
                {"start": "14:30", "end": "16:00", "name": "团队周例会与任务拆解", "type": "meeting"},
                {"start": "19:00", "end": "20:00", "name": "延期风险应急方案输出", "type": "work"},
            ],
        ],
    },
    "阳光漫步者": {
        "user_id": "69aea6e3af5e6cbf08027967",
        "days": [
            [  # 第一天
                {"start": "06:10", "end": "06:35", "name": "早操", "type": "exercise"},
                {"start": "08:40", "end": "09:20", "name": "课堂授课与互动引导", "type": "work"},
                {"start": "10:00", "end": "11:30", "name": "作文批改与共性问题整理", "type": "work"},
            ],
            [  # 第二天
                {"start": "06:15", "end": "06:40", "name": "公园散步", "type": "exercise"},
                {"start": "08:40", "end": "09:30", "name": "精读课备课与要点确认", "type": "work"},
                {"start": "10:00", "end": "11:00", "name": "备课组同步与教案对齐", "type": "meeting"},
            ],
        ],
    },
    "深夜灵感守望者": {
        "user_id": "69aea6e8af5e6cbf08027968",
        "days": [
            [  # 第一天
                {"start": "10:00", "end": "10:45", "name": "草图绘制", "type": "work"},
                {"start": "13:00", "end": "14:30", "name": "音效风格对齐与情绪确认", "type": "meeting"},
                {"start": "15:00", "end": "16:30", "name": "关卡交互说明完善与定稿", "type": "work"},
                {"start": "16:45", "end": "17:45", "name": "数值表初版校对与批注", "type": "work"},
                {"start": "19:00", "end": "20:15", "name": "原型试玩与问题清单沉淀", "type": "work"},
                {"start": "20:30", "end": "21:30", "name": "与程序接口字段对齐短会", "type": "meeting"},
                {"start": "22:00", "end": "23:30", "name": "BOSS 机制脚本撰写", "type": "work"},
                {"start": "23:45", "end": "00:30", "name": "自测记录与明日修复队列", "type": "work"},
            ],
            [  # 第二天
                {"start": "09:30", "end": "10:20", "name": "游戏问题汇总", "type": "work"},
                {"start": "13:00", "end": "14:30", "name": "场景氛围对齐与美术确认", "type": "meeting"},
                {"start": "15:00", "end": "16:45", "name": "叙事脚本完善与版本完稿", "type": "work"},
                {"start": "17:00", "end": "17:45", "name": "关卡白盒验收清单勾选", "type": "work"},
                {"start": "19:30", "end": "21:00", "name": "策划工作坊讨论与案例复盘", "type": "meeting"},
                {"start": "21:15", "end": "22:15", "name": "体验录像切片与标注", "type": "work"},
                {"start": "22:30", "end": "23:45", "name": "卡关交互修复", "type": "work"},
                {"start": "23:55", "end": "00:40", "name": "版本说明与提交备注", "type": "work"},
            ],
        ],
    },
    "深海独奏家": {
        "user_id": "69aea6eeaf5e6cbf08027969",
        "days": [
            [  # 第一天
                {"start": "09:30", "end": "10:30", "name": "香水试配", "type": "work"},
                {"start": "14:00", "end": "15:30", "name": "中调配比实验与参数校准", "type": "work"},
                {"start": "16:00", "end": "16:45", "name": "实验台清洁与批次标签", "type": "work"},
                {"start": "20:00", "end": "21:15", "name": "香型报告首节撰写", "type": "work"},
                {"start": "21:30", "end": "22:15", "name": "次日实验物料清单确认", "type": "work"},
            ],
            [  # 第二天
                {"start": "10:00", "end": "10:50", "name": "品牌沟通", "type": "meeting"},
                {"start": "14:00", "end": "15:45", "name": "基底留香对比记录", "type": "work"},
                {"start": "16:00", "end": "16:40", "name": "色谱数据归档与命名", "type": "work"},
                {"start": "20:30", "end": "21:45", "name": "产品文案润色", "type": "work"},
                {"start": "21:55", "end": "22:30", "name": "提交对接与收件确认", "type": "meeting"},
            ],
        ],
    },
    "创意夜猫子": {
        "user_id": "69aea6f3af5e6cbf0802796a",
        "days": [
            [  # 第一天
                {"start": "10:00", "end": "11:00", "name": "热点浏览", "type": "work"},
                {"start": "13:00", "end": "14:15", "name": "达人选题沟通与方向对齐", "type": "meeting"},
                {"start": "14:15", "end": "16:00", "name": "爆款方向筛选与优先级排序", "type": "work"},
                {"start": "16:00", "end": "17:45", "name": "待发布内容审片与意见汇总", "type": "work"},
                {"start": "19:00", "end": "20:30", "name": "内容周会同步与分工确认", "type": "meeting"},
                {"start": "21:00", "end": "23:30", "name": "突发选题草案撰写与定框", "type": "work"},
            ],
            [  # 第二天
                {"start": "10:30", "end": "11:30", "name": "合作达人近况梳理", "type": "meeting"},
                {"start": "13:00", "end": "14:30", "name": "Q3 内容方向梳理与一页稿输出", "type": "work"},
                {"start": "14:30", "end": "16:30", "name": "粗剪评审反馈与修改建议汇总", "type": "meeting"},
                {"start": "16:30", "end": "18:00", "name": "大会资源对接与档期确认", "type": "meeting"},
                {"start": "19:30", "end": "21:00", "name": "履约风险表复核与更新", "type": "work"},
                {"start": "21:30", "end": "23:00", "name": "平台合作提案撰写与初审", "type": "work"},
            ],
        ],
    },
    "月光冲浪者": {
        "user_id": "69aea6f8af5e6cbf0802796b",
        "days": [
            [  # 第一天
                {"start": "10:00", "end": "11:30", "name": "照片修图", "type": "work"},
                {"start": "14:30", "end": "17:00", "name": "街景外拍执行与素材整理", "type": "work"},
                {"start": "20:00", "end": "21:30", "name": "投稿组图定稿与导出交付", "type": "work"},
            ],
            [  # 第二天
                {"start": "10:30", "end": "11:30", "name": "照片编排", "type": "meeting"},
                {"start": "14:30", "end": "16:30", "name": "学员作业点评与修改建议", "type": "meeting"},
                {"start": "21:00", "end": "22:15", "name": "个展主题梳理与一页草案", "type": "work"},
            ],
        ],
    },
}

CREATE_TIME = "2026-05-06T06:37:00.000Z"

# 除 range 首日外，有日程的日历日占比（确定性：同一 uid+日期 结果稳定）
_CALENDAR_DAY_EMIT_THRESHOLD_PCT = 72


def _should_emit_events_for_calendar_day(uid: str, d: date, *, is_first_day_of_range: bool) -> bool:
    """首日必有事件；其余日按哈希稀疏，跳过日仍参与模板日 index 轮转（见 _build_events_for_uid）。"""
    if is_first_day_of_range:
        return True
    key = f"{uid}|{d.isoformat()}|calendar_sparse_v1".encode("utf-8")
    n = int.from_bytes(hashlib.sha256(key).digest()[:8], "big")
    return (n % 100) < _CALENDAR_DAY_EMIT_THRESHOLD_PCT


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
    start_dt = datetime.strptime(str(start_s), "%Y-%m-%d").date()
    end_dt = datetime.strptime(str(end_s), "%Y-%m-%d").date()
    if start_override:
        start_dt = max(start_dt, start_override)
    if end_override:
        end_dt = min(end_dt, end_override)
    if start_dt > end_dt:
        return None
    return start_dt, end_dt


def parse_time(t: str) -> tuple[int, int]:
    """解析 'HH:MM' 为 (hour, minute)。"""
    h, m = t.split(":")
    return int(h), int(m)


def calc_duration(start: str, end: str) -> int:
    """计算时长（分钟），支持跨午夜（如 22:30 → 01:00 = 150 分钟）。"""
    sh, sm = parse_time(start)
    eh, em = parse_time(end)
    s_total = sh * 60 + sm
    e_total = eh * 60 + em
    if e_total <= s_total:          # 跨午夜
        e_total += 24 * 60
    return e_total - s_total


def _build_events_for_uid(
    uid: str,
    days: list[list[dict[str, Any]]],
    range_start: date,
    range_end: date,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    offset = 0
    cur = range_start
    while cur <= range_end:
        date_str = cur.strftime("%Y-%m-%d")
        day_schedule = days[offset % 2]
        offset += 1
        emit = _should_emit_events_for_calendar_day(
            uid, cur, is_first_day_of_range=(cur == range_start)
        )
        if emit:
            for ev in day_schedule:
                duration = calc_duration(ev["start"], ev["end"])
                events.append(
                    {
                        "uid": uid,
                        "event_date": date_str,
                        "event_type": ev["type"],
                        "event_name": ev["name"],
                        "start_time": ev["start"],
                        "end_time": ev["end"],
                        "duration_minutes": duration,
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
) -> None:
    """仅为 personas 中出现的 user_id 且在内置 SCHEDULES 中有模板的用户写入日历 JSON。"""
    uids = {str(p.get("user_id")) for p in personas if p.get("user_id")}
    by_uid: dict[str, dict[str, Any]] = {
        str(p["user_id"]): p for p in personas if p.get("user_id")
    }
    os.makedirs(output_dir, exist_ok=True)

    for persona_name, info in SCHEDULES.items():
        uid = str(info["user_id"])
        if uid not in uids:
            continue
        persona_cfg = by_uid.get(uid)
        if not persona_cfg:
            continue
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
        events = _build_events_for_uid(uid, days, range_start, range_end)
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
    )


if __name__ == "__main__":
    main()
