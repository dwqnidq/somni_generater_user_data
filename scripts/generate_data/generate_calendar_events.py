"""
根据各人格日程设定，生成每位用户的日历事件数据（2026-03-01 ~ 2026-03-31）。
- 第一天对应 2026-03-01，第二天对应 2026-03-02，之后循环交替。
- 输出到 output/{user_id}_calendar_events.json
"""

import json
import os
from datetime import date, timedelta, timezone, datetime

# ── 日程数据（直接从 docs/personas/各人格日程设定.md 提取） ──────────────────

SCHEDULES = {
    "完美主义百灵鸟": {
        "user_id": "69aea593af5e6cbf08027964",
        "days": [
            [  # 第一天
                {"start": "06:10", "end": "06:40", "name": "晨跑 5km", "type": "exercise"},
                {"start": "07:00", "end": "07:30", "name": "浏览竞品大促动态，提炼差异化核心卖点备注", "type": "work"},
                {"start": "09:00", "end": "10:30", "name": "与设计团队对齐 Q2 大促主视觉方案", "type": "meeting"},
                {"start": "11:00", "end": "12:30", "name": "撰写大促活动执行方案文档初稿", "type": "work"},
                {"start": "14:00", "end": "15:30", "name": "跨部门进度同步会议：核对各模块交付节点", "type": "meeting"},
                {"start": "16:00", "end": "17:30", "name": "复盘上周活动 ROI 数据，输出分析报告", "type": "work"},
                {"start": "20:00", "end": "21:00", "name": "整理次日提案材料，预演发言逻辑", "type": "work"},
            ],
            [  # 第二天
                {"start": "06:10", "end": "06:40", "name": "晨跑 + 正念呼吸练习", "type": "exercise"},
                {"start": "07:00", "end": "07:30", "name": "整理昨日会议纪要，同步至各部门负责人", "type": "work"},
                {"start": "09:30", "end": "11:00", "name": "评审设计团队提交的 3 套大促落地视觉稿", "type": "meeting"},
                {"start": "11:30", "end": "12:30", "name": "参加品牌方 BD 沟通会，推进合作框架确认", "type": "meeting"},
                {"start": "14:00", "end": "15:30", "name": "与客服团队对接活动 FAQ 文档修订", "type": "meeting"},
                {"start": "16:00", "end": "17:30", "name": "完成大促节点甘特图更新，标注关键风险项", "type": "work"},
            ],
        ],
    },
    "敏感的晨间鹿": {
        "user_id": "69aea63eaf5e6cbf08027965",
        "days": [
            [  # 第一天
                {"start": "06:00", "end": "06:25", "name": "轻缓晨练（瑜伽拉伸）", "type": "exercise"},
                {"start": "09:00", "end": "12:00", "name": "审读《城市记忆》书稿第三章，完成修改批注", "type": "work"},
                {"start": "14:30", "end": "16:00", "name": "与作者通话：确认第三章修改意见，对齐下一稿方向", "type": "meeting"},
            ],
            [  # 第二天
                {"start": "06:00", "end": "06:20", "name": "轻缓晨练 + 散步", "type": "exercise"},
                {"start": "09:30", "end": "11:30", "name": "完成《远行者》书稿三校，提交校对意见单", "type": "work"},
                {"start": "14:00", "end": "15:30", "name": "参与编辑部月度选题评审会议", "type": "meeting"},
            ],
        ],
    },
    "效率至上考拉": {
        "user_id": "69aea6d8af5e6cbf08027966",
        "days": [
            [  # 第一天
                {"start": "05:50", "end": "06:15", "name": "晨跑", "type": "exercise"},
                {"start": "06:50", "end": "07:20", "name": "查阅昨日仓储数据，标记异常节点并发邮件追踪", "type": "work"},
                {"start": "07:30", "end": "08:00", "name": "核查昨日物流数据报表，回复供应商紧急邮件", "type": "work"},
                {"start": "08:30", "end": "10:00", "name": "跨部门物流排期对齐会议", "type": "meeting"},
                {"start": "10:00", "end": "11:30", "name": "推进仓配自动化系统上线方案评审", "type": "meeting"},
                {"start": "13:00", "end": "15:00", "name": "供应商谈判：确认下季原材料备货数量与交期", "type": "meeting"},
                {"start": "15:00", "end": "16:30", "name": "更新供应链风险预案文档，输出本周版本", "type": "work"},
                {"start": "17:00", "end": "18:00", "name": "向 VP 汇报本周供应链核心进展与风险", "type": "meeting"},
            ],
            [  # 第二天
                {"start": "05:50", "end": "06:15", "name": "晨练（HIIT 15 分钟）", "type": "exercise"},
                {"start": "06:50", "end": "07:20", "name": "复盘上周订单履行率，标记关键差异点", "type": "work"},
                {"start": "07:30", "end": "08:00", "name": "拟定今日供应商谈判的底价预案与让步边界", "type": "work"},
                {"start": "08:30", "end": "10:00", "name": "参加集团季度供应链战略会议", "type": "meeting"},
                {"start": "10:00", "end": "11:30", "name": "完成 Q2 运营报告初稿撰写", "type": "work"},
                {"start": "13:00", "end": "14:30", "name": "推进仓储 ERP 系统迁移节点确认", "type": "meeting"},
                {"start": "14:30", "end": "16:00", "name": "团队周例会：同步各线进展与本周重点", "type": "meeting"},
                {"start": "19:00", "end": "20:00", "name": "处理供应商突发延期，协调替代方案落地", "type": "work"},
            ],
        ],
    },
    "阳光漫步者": {
        "user_id": "69aea6e3af5e6cbf08027967",
        "days": [
            [  # 第一天
                {"start": "06:10", "end": "06:35", "name": "晨练（慢跑 + 拉伸）", "type": "exercise"},
                {"start": "08:30", "end": "10:05", "name": "上语文课第一节：讲解《落花生》，完成课堂教学目标", "type": "work"},
                {"start": "10:30", "end": "12:00", "name": "批改三年级作文作业，完成全班评语", "type": "work"},
                {"start": "14:00", "end": "15:30", "name": "上语文课第三节：古诗鉴赏教学", "type": "work"},
            ],
            [  # 第二天
                {"start": "06:15", "end": "06:40", "name": "晨走 + 整理当日备课笔记", "type": "exercise"},
                {"start": "08:30", "end": "10:00", "name": "备课：准备下周《白鹭》精读课教学设计文档", "type": "work"},
                {"start": "10:30", "end": "12:00", "name": "参加年级语文备课组组会，确认下月课程安排", "type": "meeting"},
                {"start": "14:00", "end": "15:30", "name": "辅导语文竞赛小组，完成命题作文初稿", "type": "work"},
            ],
        ],
    },
    "深夜灵感守望者": {
        "user_id": "69aea6e8af5e6cbf08027968",
        "days": [
            [  # 第一天
                {"start": "10:00", "end": "11:00", "name": "整理昨夜关卡草图，形成关卡设计文档初稿", "type": "work"},
                {"start": "13:00", "end": "15:00", "name": "与音效团队对接关卡背景音乐风格", "type": "meeting"},
                {"start": "15:30", "end": "17:30", "name": "撰写关卡设计文档 v2，补充交互逻辑说明", "type": "work"},
                {"start": "19:30", "end": "21:30", "name": "进行关卡原型测试，记录 BUG 与体验问题", "type": "work"},
                {"start": "22:30", "end": "01:00", "name": "高强度关卡脚本创作：第 5 关 BOSS 战设计", "type": "work"},
            ],
            [  # 第二天
                {"start": "09:30", "end": "10:30", "name": "整理昨晚测试 BUG，产出缺陷报告并同步至主程团队", "type": "work"},
                {"start": "13:00", "end": "15:00", "name": "与原画师对齐第 4 关场景氛围图风格", "type": "meeting"},
                {"start": "16:00", "end": "18:00", "name": "撰写关卡叙事脚本第 3 关完整版", "type": "work"},
                {"start": "20:00", "end": "22:00", "name": "参加游戏策划线上 workshop，提交关卡设计案例", "type": "meeting"},
                {"start": "23:00", "end": "01:00", "name": "修改关卡交互逻辑，解决测试中的卡关问题", "type": "work"},
            ],
        ],
    },
    "深海独奏家": {
        "user_id": "69aea6eeaf5e6cbf08027969",
        "days": [
            [  # 第一天
                {"start": "09:30", "end": "11:00", "name": "在实验室完成春季新品前调配方初次测试，记录嗅觉反应数据", "type": "work"},
                {"start": "14:30", "end": "17:30", "name": "调配并记录「海盐木质」系列中调香料比例实验", "type": "work"},
                {"start": "20:30", "end": "22:30", "name": "整理本季香型方向报告初稿，完成第一部分", "type": "work"},
            ],
            [  # 第二天
                {"start": "10:00", "end": "11:30", "name": "与品牌方线上沟通，确认新品香型故事方向", "type": "meeting"},
                {"start": "14:00", "end": "17:00", "name": "香料留香实验：对比 3 款基底香料留香时长数据", "type": "work"},
                {"start": "21:00", "end": "23:00", "name": "完成香水产品介绍文案撰写，提交至文案团队", "type": "work"},
            ],
        ],
    },
    "创意夜猫子": {
        "user_id": "69aea6f3af5e6cbf0802796a",
        "days": [
            [  # 第一天
                {"start": "10:00", "end": "11:30", "name": "复盘昨日 3 条视频数据，提炼高完播率内容规律", "type": "work"},
                {"start": "13:00", "end": "14:30", "name": "与 3 位达人分别对接下周视频选题方向", "type": "meeting"},
                {"start": "14:30", "end": "16:30", "name": "撰写本周爆款选题策划方案（含 5 个备选方向）", "type": "work"},
                {"start": "16:30", "end": "18:30", "name": "审核本周待发布视频，完成 5 条审片记录", "type": "work"},
                {"start": "19:00", "end": "21:00", "name": "参与公司内容创作部门周会", "type": "meeting"},
                {"start": "21:30", "end": "00:00", "name": "追热点，完成明日突发选题策划初稿", "type": "work"},
            ],
            [  # 第二天
                {"start": "10:30", "end": "12:00", "name": "对接投放团队，推进下月合作达人池确认", "type": "meeting"},
                {"start": "13:00", "end": "14:30", "name": "撰写 Q3 季度内容创作方向报告", "type": "work"},
                {"start": "14:30", "end": "16:30", "name": "评审达人提交的下期内容初稿，逐条提供修改意见", "type": "meeting"},
                {"start": "16:30", "end": "18:00", "name": "参加行业 MCN 创作者大会，对接潜在达人资源", "type": "meeting"},
                {"start": "19:30", "end": "21:00", "name": "更新月度达人履约情况追踪表，标记高风险项", "type": "work"},
                {"start": "21:30", "end": "23:30", "name": "完成下季平台方合作提案初稿", "type": "work"},
            ],
        ],
    },
    "月光冲浪者": {
        "user_id": "69aea6f8af5e6cbf0802796b",
        "days": [
            [  # 第一天
                {"start": "10:00", "end": "11:30", "name": "整理上周拍摄素材，完成客户精选照片初剪，输出 30 张候选图", "type": "work"},
                {"start": "14:00", "end": "16:30", "name": "外出拍摄：城市街景专题，完成 200 张有效素材", "type": "work"},
                {"start": "20:00", "end": "22:00", "name": "完成本月杂志投稿组图最终修图，导出提交版", "type": "work"},
            ],
            [  # 第二天
                {"start": "10:30", "end": "12:00", "name": "与杂志编辑线上沟通本期专题排版方向，确认图组顺序", "type": "meeting"},
                {"start": "14:30", "end": "17:00", "name": "参加摄影工作坊：评审学员作品，提供书面反馈意见", "type": "meeting"},
                {"start": "21:00", "end": "22:30", "name": "整理下季个人展览创意草案，完成主题方向文档", "type": "work"},
            ],
        ],
    },
}

DATE_START = date(2026, 3, 1)
DATE_END   = date(2026, 4, 1)   # 不含，即到 3-31

CREATE_TIME = "2026-05-06T06:37:00.000Z"


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


def main():
    output_dir = os.path.join(
        os.path.dirname(__file__), "..", "..", "output"
    )
    os.makedirs(output_dir, exist_ok=True)

    total_days = (DATE_END - DATE_START).days   # 31

    for persona_name, info in SCHEDULES.items():
        uid   = info["user_id"]
        days  = info["days"]       # [day1_events, day2_events]
        events = []

        for i in range(total_days):
            current_date = DATE_START + timedelta(days=i)
            date_str     = current_date.strftime("%Y-%m-%d")
            day_schedule = days[i % 2]          # 交替使用第一天/第二天

            for ev in day_schedule:
                duration = calc_duration(ev["start"], ev["end"])
                record = {
                    "uid":              uid,
                    "event_date":       date_str,
                    "event_type":       ev["type"],
                    "event_name":       ev["name"],
                    "start_time":       ev["start"],
                    "end_time":         ev["end"],
                    "duration_minutes": duration,
                    "create_time":      CREATE_TIME,
                    "update_time":      CREATE_TIME,
                    "language":         "zh",
                }
                events.append(record)

        out_path = os.path.join(output_dir, f"{uid}_calendar_events.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)

        print(f"✓ {persona_name} ({uid})  →  {len(events)} 条事件  →  {out_path}")


if __name__ == "__main__":
    main()
