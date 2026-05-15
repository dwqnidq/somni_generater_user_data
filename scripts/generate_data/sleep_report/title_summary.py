"""主标题选择 + 本地摘要。"""

from __future__ import annotations

from .shared import _variant_pick
from .sleep_helpers import night_wake_episodes_for_prompts
from .sleep_score import sleep_report_score_from_sleep_data
from .notice import get_image_url_by_name


def build_main_local_summary(
    main_title,
    deep_percent,
    light_percent,
    rem_percent,
    sleep_latency,
    night_wake_episodes,
    total_sleep_minutes=None,
    sleep_efficiency=None,
    awake_percent=None,
    record_date="",
):
    """根据称号与当晚指标生成本地可执行 summary（不依赖大模型）。"""
    rd = record_date or ""
    tst = int(total_sleep_minutes or 0)
    eff = int(sleep_efficiency or 0)
    awake_p = int(awake_percent or 0)
    wake_eps = int(night_wake_episodes or 0)
    lat = int(sleep_latency or 0)
    deep_p = int(deep_percent or 0)

    energy_score = 0
    if tst >= 420:
        energy_score += 1
    if deep_p >= 20:
        energy_score += 1
    if eff >= 88:
        energy_score += 1
    if awake_p <= 10:
        energy_score += 1
    if lat <= 20:
        energy_score += 1
    if wake_eps <= 2:
        energy_score += 1

    energy_text = _variant_pick(
        rd,
        f"energy_text|{energy_score}",
        {
            0: ["今天精力大概率偏低，建议把节奏放慢一些。"],
            1: ["今天精力偏弱，上午尽量先做最关键的一件事。"],
            2: ["今天精力一般，适合稳步推进，不建议连轴高压。"],
            3: ["今天精力中等偏稳，可以正常推进重点任务。"],
            4: ["今天精力状态不错，适合安排中高优先级任务。"],
            5: ["今天精力较好，脑力和专注启动会更顺。"],
            6: ["今天精力在线，恢复质量整体在理想区间。"],
        }.get(energy_score, ["今天精力状态可控，建议按计划推进。"]),
    )

    actions = []
    if lat > 20:
        actions.append("今晚把睡前 45 分钟留给降速流程，先停高刺激内容，再做 10 分钟慢呼吸")
    if deep_p < 18:
        actions.append("今晚把卧室温度尽量控在 19-22℃，并把最后一杯含咖啡因饮品提前到 14:00 前")
    if wake_eps > 2 or awake_p > 12:
        actions.append("睡前 2 小时减少饮水，夜间噪音尽量压到 40dB 以下，减少中段清醒")
    if eff < 85:
        actions.append("有困意再上床，若 20 分钟还没睡着先起身到弱光区放松，再回床")
    if tst < 390:
        actions.append("未来 3 晚把上床时间提前 15 分钟，先把总睡眠补回到 6.5 小时以上")
    if not actions:
        actions.append("今晚继续保持固定起床时间，睡前 1 小时不加新任务，稳定住当前节律")

    opener = _variant_pick(
        rd,
        f"main_opener|{main_title}",
        [
            f"你昨晚拿到「{main_title}」这个标签，核心信号很明确。",
            f"从昨晚这份睡眠来看，「{main_title}」这个判断是成立的。",
            f"昨晚的睡眠结构和「{main_title}」比较匹配，重点我直接说。"
        ],
    )
    metric_sentence = (
        f"净睡 {tst} 分钟，深睡 {int(deep_percent)}%，入睡约 {lat} 分钟，夜间中段清醒 {wake_eps} 次。"
    )
    return f"{opener}{metric_sentence}{energy_text}今晚先做这一条：{actions[0]}。"


def _pick_main_title_prefer_adjacent(ordered_candidates, recent_titles):
    """
    在有序候选中选一个：优先与 recent_titles（相邻已生成日，由调用方维护）不同；
    若全部与近期重复则取 ordered_candidates[0]（不得已与邻近日相同）。
    """
    if not ordered_candidates:
        return None
    recent_set = set(recent_titles or [])
    for t in ordered_candidates:
        if t not in recent_set:
            return t
    return ordered_candidates[0]


def pick_main_title(
    personality_type,
    sleep_data,
    light_percent,
    deep_percent,
    rem_percent,
    sleep_latency,
    sleep_efficiency,
    apnea_count,
    recent_titles=None,
    report_score=None,
):
    """
    先根据当晚指标**分别收集**命中的所有正向、负向称号，再结合**报告分**与邻近日标题决策。

    报告分 report_score：与睡眠报告中 body_battery 一致（0–100），默认由
    sleep_report_score_from_sleep_data(sleep_data) 计算；也可由调用方传入。

    - **报告分 < 75**：优先只在**负向命中集合**中选（尽量与 recent_titles 不同）；
      若当晚无任何负向命中，则退到**正向集合**（同样尽量与邻近日不同），再不行兜底「深睡守护者」。
    - **报告分 ≥ 75**：优先**正向**命中集合；无正向再在负向中选；正负皆无则兜底「深睡守护者」。
    各子集内多候选时：按固定顺序，**优先选与 recent_titles（相邻已生成日）不同的**；全重复则取顺序首项。

    负向（各自独立判定，可同时命中多项）：
    - 夜眠不安：呼吸暂停 ≥ 5 且 夜间中段清醒次数 > 2
    - 眠质不良：深睡 < 15% 或 REM < 15%
    - 浅眠易醒：浅睡 > 55%

    正向（各自独立判定）：
    - 深睡守护者：深睡 > 15%
    - 秒睡王者：入睡潜伏期 < 20 分钟
    - 抗扰宗师：人格 M-L-R / E-L-R

    sleep_efficiency 保留兼容调用方，本函数不再使用。

    recent_titles: 相邻近期已用标签（如最近 2 条），用于「尽量与邻近日不同」。
    """
    _ = sleep_efficiency
    wake_n = night_wake_episodes_for_prompts(sleep_data)
    try:
        score = (
            int(report_score)
            if report_score is not None
            else int(sleep_report_score_from_sleep_data(sleep_data))
        )
    except (TypeError, ValueError):
        score = int(sleep_report_score_from_sleep_data(sleep_data))

    good_ordered = []
    if deep_percent > 15:
        good_ordered.append("深睡守护者")
    if sleep_latency < 20:
        good_ordered.append("秒睡王者")
    if personality_type in ("M-L-R", "E-L-R"):
        good_ordered.append("抗扰宗师")

    bad_ordered = []
    if apnea_count >= 5 and wake_n > 2:
        bad_ordered.append("夜眠不安")
    if deep_percent < 15 or rem_percent < 15:
        bad_ordered.append("眠质不良")
    if light_percent > 55:
        bad_ordered.append("浅眠易醒")

    if score < 75:
        if bad_ordered:
            return _pick_main_title_prefer_adjacent(bad_ordered, recent_titles)
        if good_ordered:
            return _pick_main_title_prefer_adjacent(good_ordered, recent_titles)
        return "深睡守护者"

    if good_ordered:
        return _pick_main_title_prefer_adjacent(good_ordered, recent_titles)
    if bad_ordered:
        return _pick_main_title_prefer_adjacent(bad_ordered, recent_titles)
    return "深睡守护者"


def get_main_title_image_url(main_title):
    if main_title == "秒睡王者":
        return get_image_url_by_name("秒睡王者") or get_image_url_by_name("秒睡宗师")
    return get_image_url_by_name(main_title)
