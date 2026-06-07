#!/usr/bin/env python3
"""导出 somni_schedules 集合中指定日期的八人格日程到 Markdown 文档。

用法（项目根目录）：
  .venv/bin/python scripts/export_schedules_md.py
  .venv/bin/python scripts/export_schedules_md.py --dates 2026-05-29 2026-05-30
  .venv/bin/python scripts/export_schedules_md.py --output output/my_schedules.md
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from typing import Any

from pymongo import MongoClient

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
INSERT_DATA_DIR = os.path.join(SCRIPT_DIR, "insert_data")
if INSERT_DATA_DIR not in sys.path:
    sys.path.insert(0, INSERT_DATA_DIR)

from mongo_persona_output_specs import (  # noqa: E402
    load_persona_uids,
    normalize_doc,
    resolve_mongo_uri,
)

COLLECTION_NAME = "somni_schedules"
DEFAULT_DATES = ["2026-05-29", "2026-05-30"]
DEFAULT_OUTPUT = os.path.join(PROJECT_ROOT, "output", "schedules_report.md")

# 八人格元信息：uid -> (name, code, identity_type)
PERSONA_META: dict[str, dict[str, str]] = {
    "69aea593af5e6cbf08027964": {"name": "完美主义百灵鸟", "code": "M-H-R", "identity_type": "策划者"},
    "69aea63eaf5e6cbf08027965": {"name": "敏感的晨间鹿", "code": "M-H-C", "identity_type": "调音师"},
    "69aea6d8af5e6cbf08027966": {"name": "效率至上考拉", "code": "M-L-R", "identity_type": "执行师"},
    "69aea6e3af5e6cbf08027967": {"name": "阳光漫步者", "code": "M-L-C", "identity_type": "漫步者"},
    "69aea6e8af5e6cbf08027968": {"name": "深夜灵感守望者", "code": "E-H-R", "identity_type": "创作者"},
    "69aea6eeaf5e6cbf08027969": {"name": "深海独奏家", "code": "E-H-C", "identity_type": "独奏家"},
    "69aea6f3af5e6cbf0802796a": {"name": "创意夜猫子", "code": "E-L-R", "identity_type": "探险家"},
    "69aea6f8af5e6cbf0802796b": {"name": "月光冲浪者", "code": "E-L-C", "identity_type": "享乐家"},
}

# 人格 -> 职业推断（基于 somni_schedules 实际日程事件分析）
PERSONA_PROFESSION: dict[str, str] = {
    "69aea593af5e6cbf08027964": "品牌策划 / 项目管理 — 日程以品牌对齐、版权确认、供应商条款签署、排期管理、发票核对为主，属于创意行业中的统筹策划角色",
    "69aea63eaf5e6cbf08027965": "内容编辑 / 产品企划 — 日程涉及样品签收、选题讨论、合同条款沟通、问题清单输出、意见回复，偏向内容运营与产品企划岗位",
    "69aea6d8af5e6cbf08027966": "供应链管理 / 运营管理 — 日程以货物报关、库存审批、补件材料、停产备案、运营数据提交、整改报告为主，属于供应链与执行管理岗",
    "69aea6e3af5e6cbf08027967": "QA / 测试工程师 — 日程围绕告警值班交接、测试报告、脚本变更、质量复盘、代码合并评审、技术方案评审，属于质量保障与测试岗位",
    "69aea6e8af5e6cbf08027968": "游戏策划 / 游戏开发 — 日程涉及外包交付验收、物料截稿、测试服发布、数据字段对齐、制定关卡方案、多语言文案校对、崩溃日志复盘，属于游戏研发团队",
    "69aea6eeaf5e6cbf08027969": "香水 / 日化研发工程师 — 日程以实验取样、仪器校准、竞品香型整理、香水评价反馈、样品寄送、专利清单签署为核心，属于日化产品研发岗位",
    "69aea6f3af5e6cbf0802796a": "MCN / 内容运营 — 日程围绕内容运营晨会、脚本终审、品牌需求变更、内容排期、音乐版权整理、爆款内容复盘、合作提案优化，属于MCN或内容运营岗",
    "69aea6f8af5e6cbf0802796b": "摄影师 / 视觉内容创作者 — 日程涉及场地租赁合同、相机保养、成片筛选、稿件提交、作品规格核对、主题构思，属于视觉内容创作与摄影岗位",
}


def normalize_event_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if "T" in text:
        return text.split("T", 1)[0]
    return text[:10] if len(text) >= 10 else text


def normalize_clock_time(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    text = str(value).strip()
    if "T" in text:
        part = text.split("T", 1)[1]
        return part[:5] if len(part) >= 5 else part
    if len(text) >= 5 and text[2] == ":":
        return text[:5]
    return text


def query_schedules(
    collection, uids: list[str], dates: list[str]
) -> dict[str, dict[str, list[dict]]]:
    """返回 {uid: {date: [event, ...]}} 的嵌套字典。"""
    query = {"uid": {"$in": uids}, "event_date": {"$in": dates}}
    cursor = collection.find(query).sort(
        [("uid", 1), ("event_date", 1), ("start_time", 1)]
    )

    result: dict[str, dict[str, list[dict]]] = {}
    for doc in cursor:
        normalized = normalize_doc(doc)
        uid = str(normalized.get("uid", ""))
        event_date = normalize_event_date(normalized.get("event_date"))
        start_time = normalize_clock_time(normalized.get("start_time"))
        end_time = normalize_clock_time(normalized.get("end_time"))

        event = {
            "event_name": str(normalized.get("event_name", "")),
            "event_type": str(normalized.get("event_type", "")),
            "start_time": start_time,
            "end_time": end_time,
            "duration_minutes": normalized.get("duration_minutes"),
        }

        result.setdefault(uid, {}).setdefault(event_date, []).append(event)

    # 按 start_time 排序
    for uid in result:
        for date in result[uid]:
            result[uid][date].sort(key=lambda e: e["start_time"])

    return result


def generate_markdown(
    data: dict[str, dict[str, list[dict]]],
    dates: list[str],
) -> str:
    """生成 Markdown 文档。"""
    lines: list[str] = []
    lines.append("# Somni 八人格日程报告")
    lines.append("")
    lines.append(f"> 数据来源：`somni_schedules` 集合")
    lines.append(f"> 查询日期：{', '.join(dates)}")
    lines.append(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 目录
    lines.append("## 目录")
    lines.append("")
    ordered_uids = list(PERSONA_META.keys())
    for i, uid in enumerate(ordered_uids, 1):
        meta = PERSONA_META[uid]
        anchor = meta["name"].replace(" ", "-").lower()
        lines.append(
            f"{i}. [{meta['name']}（{meta['code']} / {meta['identity_type']}）](#{anchor})"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    # 每个人格
    for uid in ordered_uids:
        meta = PERSONA_META[uid]
        profession = PERSONA_PROFESSION.get(uid, "未知")
        persona_data = data.get(uid, {})

        lines.append(f"## {meta['name']}")
        lines.append("")
        lines.append(f"| 属性 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| 人格编码 | `{meta['code']}` |")
        lines.append(f"| 身份类型 | {meta['identity_type']} |")
        lines.append(f"| UID | `{uid}` |")
        lines.append(f"| **推测职业** | **{profession}** |")
        lines.append("")

        for date in dates:
            events = persona_data.get(date, [])
            lines.append(f"### {date}")
            lines.append("")

            if not events:
                lines.append("*该日期无日程数据*")
                lines.append("")
                continue

            # 统计
            total_events = len(events)
            total_minutes = sum(
                e["duration_minutes"]
                for e in events
                if e["duration_minutes"] is not None
            )
            hours = total_minutes // 60
            mins = total_minutes % 60

            lines.append(f"**共 {total_events} 个日程，合计 {hours}h{mins}min**")
            lines.append("")
            lines.append("| 序号 | 时间段 | 类型 | 事件名称 | 时长 |")
            lines.append("|:----:|--------|------|----------|------|")

            for idx, event in enumerate(events, 1):
                time_range = f"{event['start_time']}–{event['end_time']}"
                dur = event["duration_minutes"]
                dur_str = f"{dur}min" if dur is not None else "-"
                lines.append(
                    f"| {idx} | {time_range} | {event['event_type']} | {event['event_name']} | {dur_str} |"
                )

            lines.append("")

        lines.append("---")
        lines.append("")

    # 汇总对比表
    lines.append("## 八人格日程对比总览")
    lines.append("")
    header = "| 人格 | 身份 | 推测职业 |"
    sep = "|------|------|----------|"
    for date in dates:
        header += f" {date} 事件数 |"
        sep += "------|"
    lines.append(header)
    lines.append(sep)

    for uid in ordered_uids:
        meta = PERSONA_META[uid]
        profession = PERSONA_PROFESSION.get(uid, "未知")
        row = f"| {meta['name']} | {meta['identity_type']} | {profession[:15]}… |"
        for date in dates:
            events = data.get(uid, {}).get(date, [])
            row += f" {len(events)} |"
        lines.append(row)

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="导出 somni_schedules 八人格日程到 Markdown"
    )
    parser.add_argument(
        "--dates",
        nargs="+",
        default=DEFAULT_DATES,
        help=f"查询日期列表，默认 {' '.join(DEFAULT_DATES)}",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"输出 Markdown 文件路径，默认 {DEFAULT_OUTPUT}",
    )
    args = parser.parse_args()

    # 加载人格 UID
    uids = load_persona_uids()

    # 连接数据库
    try:
        uri, db_name = resolve_mongo_uri()
    except RuntimeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    client = MongoClient(uri, serverSelectionTimeoutMS=15000)
    try:
        client.admin.command("ping")
        db = client[db_name]
        collection = db[COLLECTION_NAME]
    except Exception as exc:
        print(f"MongoDB 连接失败: {exc}", file=sys.stderr)
        return 2

    # 查询数据
    print(f"查询日期: {args.dates}")
    print(f"人格数: {len(uids)}")
    data = query_schedules(collection, uids, args.dates)

    # 统计
    total_events = sum(
        len(events)
        for persona in data.values()
        for events in persona.values()
    )
    print(f"共查询到 {total_events} 条日程")

    client.close()

    # 生成 Markdown
    md = generate_markdown(data, args.dates)

    # 写入文件
    output_path = args.output
    if not os.path.isabs(output_path):
        output_path = os.path.join(PROJECT_ROOT, output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"\n已导出到: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
