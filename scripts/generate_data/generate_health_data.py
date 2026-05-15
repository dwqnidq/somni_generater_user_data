"""文件作用：用于 generate health data 相关的数据处理或流程支持。"""

import json
import os
import sys
import copy
import random
import re
import requests
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
from datetime import datetime, timedelta, time as dt_time
import math
from dotenv import load_dotenv
from pymongo import MongoClient
from bson import ObjectId
import hashlib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)
load_dotenv()

from clean_json_data import clean_json_files
from utils import (
    validate_sleep_data,
    generate_random_value,
    adjust_value_by_profile,
    generate_time_based_on_profile,
    atomic_write_json,
)
from personality_profile import (
    parse_personality_code,
    get_personality_profile,
    get_sleep_latency,
    get_total_sleep_minutes,
    get_turnover_count,
    get_apnea_count,
    get_avg_heartbeat,
    get_avg_respiration,
    get_leave_bed_info,
    get_wake_after_sleep,
    get_noise_range,
    get_hrv_adjustment,
    get_fatigue_index,
    get_stage_pattern,
)
somni_code = "somni_vip"
# 默认关闭模型调用；睡眠报告内大模型仅由 sleepReportAI 控制，其它能力仍受 USE_MODEL 等开关约束。
sleepReportAI = False
sleepAIInsights = False
sleepEventsAI = False  # 与 set_model_switch 同步；干预 action_taken 已固定为本地模板，不请求大模型
USE_MODEL = False
_CONFIG_WRITE_LOCK = threading.Lock()
_QWEN_MAX_CONCURRENCY = max(
    1, int(os.getenv("QWEN_MAX_CONCURRENCY", os.getenv("DOUBAO_MAX_CONCURRENCY", "12")))
)
_QWEN_RETRIES = max(1, int(os.getenv("QWEN_RETRIES", os.getenv("DOUBAO_RETRIES", "3"))))
_QWEN_TIMEOUT = max(
    10, int(os.getenv("QWEN_TIMEOUT_SEC", os.getenv("DOUBAO_TIMEOUT_SEC", "300")))
)
_QWEN_MAX_TOKENS = 10000
_QWEN_SEM = threading.BoundedSemaphore(_QWEN_MAX_CONCURRENCY)

# 睡眠事件：按 user 缓存体征/环境 JSON 的「按日索引」，避免管道按天循环时重复整文件解析（大文件会极慢）
_SLEEP_EVENTS_AUX_INDEX_CACHE = {}
# 每晚由环境噪声触发的「异常+AI」对数上限（成对计数，防止一晚插入过多）
SLEEP_EVENTS_MAX_ENV_NOISE_ABNORMAL = max(1, int(os.getenv("SLEEP_EVENTS_MAX_ENV_NOISE_ABNORMAL", "32")))
SLEEP_EVENTS_MAX_VITALS_ABNORMAL = max(1, int(os.getenv("SLEEP_EVENTS_MAX_VITALS_ABNORMAL", "16")))
# 每晚主事件（不含 AI 主动干预）上限：异常类与正常类分列（生成逻辑以二者为准）
SLEEP_EVENTS_MAX_ABNORMAL_PRIMARY = max(1, int(os.getenv("SLEEP_EVENTS_MAX_ABNORMAL_PRIMARY", "5")))
SLEEP_EVENTS_MAX_NORMAL_PRIMARY = max(1, int(os.getenv("SLEEP_EVENTS_MAX_NORMAL_PRIMARY", "8")))
# 兼容旧名：历史脚本/文档可能仍引用；默认 8，与 SLEEP_EVENTS_MAX_NORMAL_PRIMARY 默认一致
SLEEP_EVENTS_MAX_PRIMARY_EVENTS = max(
    4, int(os.getenv("SLEEP_EVENTS_MAX_PRIMARY_EVENTS", "8"))
)
# 打鼾锚点时刻列表长度上限（仍保留前若干高噪时刻）
SLEEP_EVENTS_MAX_SNORE_NOISE_ANCHORS = max(8, int(os.getenv("SLEEP_EVENTS_MAX_SNORE_NOISE_ANCHORS", "64")))


def set_model_switch(enabled):
    """统一控制本文件内是否允许调用模型。"""
    global USE_MODEL, sleepReportAI, sleepAIInsights, sleepEventsAI
    on = bool(enabled)
    USE_MODEL = on
    sleepReportAI = on
    sleepAIInsights = on
    sleepEventsAI = on


# ============================================================
# 日程活动时长与时段约束表（源自《日常活动时长规范》V2.4）
# slots: None 表示灵活，无时段限制；列表为可选时段，生成时随机选其一
# 时段定义：上午 6-11 / 中午 11-14 / 下午 14-18 / 晚上 18-24
# ============================================================
_SAC_AM  = {"start_hour": 6,  "end_hour": 11}  # 上午
_SAC_NN  = {"start_hour": 11, "end_hour": 14}  # 中午
_SAC_PM  = {"start_hour": 14, "end_hour": 18}  # 下午
_SAC_EVE = {"start_hour": 18, "end_hour": 24}  # 晚上

SCHEDULE_ACTIVITY_CONSTRAINTS = {
    # ── 一、运动与身体活动类 ────────────────────────────────
    "骑行":               {"duration": (45, 120),  "slots": [_SAC_AM, _SAC_PM]},
    "办公室微运动":       {"duration": (5,  15),   "slots": [_SAC_AM, _SAC_PM]},
    "跳绳训练":           {"duration": (10, 20),   "slots": [_SAC_AM, _SAC_PM]},
    "冥想":               {"duration": (5,  20),   "slots": [_SAC_AM, _SAC_EVE]},
    "慢节奏骑行":         {"duration": (20, 45),   "slots": [_SAC_PM, _SAC_EVE]},
    "晨间日光浴":         {"duration": (15, 30),   "slots": [_SAC_AM]},
    "普拉提课程":         {"duration": (30, 55),   "slots": [_SAC_AM, _SAC_PM, _SAC_EVE]},
    "晨间跑步":           {"duration": (20, 35),   "slots": [_SAC_AM]},
    "瑜伽练习":           {"duration": (20, 60),   "slots": [_SAC_AM, _SAC_EVE]},
    "芳香疗法":           {"duration": (15, 30),   "slots": [_SAC_EVE]},
    "太极拳练习":         {"duration": (30, 60),   "slots": [_SAC_AM]},
    "呼吸练习":           {"duration": (5,  15),   "slots": None},
    "散步":               {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_PM, _SAC_EVE]},
    "逛美术馆":           {"duration": (60, 120),  "slots": [_SAC_PM]},
    "正念行走":           {"duration": (15, 30),   "slots": [_SAC_AM, _SAC_PM]},
    "轻度拉伸运动":       {"duration": (10, 20),   "slots": [_SAC_AM, _SAC_EVE]},
    "轻度瑜伽":           {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_EVE]},
    "午间散步":           {"duration": (15, 30),   "slots": [_SAC_NN]},
    "爬楼梯锻炼":         {"duration": (10, 20),   "slots": [_SAC_AM, _SAC_PM]},
    "午后散步":           {"duration": (20, 40),   "slots": [_SAC_PM]},
    "做手指冥想":         {"duration": (3,  10),   "slots": None},
    "室内有氧操":         {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_PM]},
    "晨间力量训练":       {"duration": (20, 45),   "slots": [_SAC_AM]},
    "骑共享单车兜风":     {"duration": (20, 45),   "slots": [_SAC_PM, _SAC_EVE]},
    "社区健身器材锻炼":   {"duration": (15, 30),   "slots": [_SAC_AM, _SAC_PM]},
    "健身":               {"duration": (45, 90),   "slots": [_SAC_PM, _SAC_EVE]},
    "温泉泡汤":           {"duration": (60, 180),  "slots": [_SAC_PM, _SAC_EVE]},
    "做手指操":           {"duration": (3,  10),   "slots": None},
    "篮球投篮练习":       {"duration": (30, 90),   "slots": [_SAC_PM, _SAC_EVE]},
    "拳击训练":           {"duration": (30, 60),   "slots": [_SAC_PM, _SAC_EVE]},
    "深蹲训练":           {"duration": (10, 20),   "slots": None},
    "攀岩":               {"duration": (45, 90),   "slots": [_SAC_PM, _SAC_EVE]},
    "河边慢跑":           {"duration": (20, 40),   "slots": [_SAC_AM, _SAC_PM]},
    "广场太极":           {"duration": (30, 60),   "slots": [_SAC_AM]},
    "蹦床公园":           {"duration": (45, 90),   "slots": [_SAC_PM]},
    "呼吸训练":           {"duration": (5,  15),   "slots": None},
    "公园快走":           {"duration": (25, 50),   "slots": [_SAC_AM, _SAC_PM]},
    "平板支撑":           {"duration": (3,  10),   "slots": None},
    "钓鱼":               {"duration": (120, 360), "slots": [_SAC_AM, _SAC_PM]},
    "飞盘运动":           {"duration": (45, 90),   "slots": [_SAC_PM]},
    "园艺浇花":           {"duration": (15, 30),   "slots": [_SAC_AM, _SAC_PM]},
    "壶铃训练":           {"duration": (15, 30),   "slots": None},
    "引体向上挑战":       {"duration": (5,  15),   "slots": None},
    "健身房训练":         {"duration": (45, 90),   "slots": [_SAC_PM, _SAC_EVE]},
    "跳绳间歇训练":       {"duration": (10, 20),   "slots": [_SAC_AM, _SAC_PM]},
    "逛公园":             {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "周末爬山":           {"duration": (180, 360), "slots": [_SAC_AM]},
    "遛狗":               {"duration": (15, 30),   "slots": [_SAC_AM, _SAC_EVE]},
    "泡澡放松":           {"duration": (20, 40),   "slots": [_SAC_EVE]},
    "夜间城市漫步":       {"duration": (20, 40),   "slots": [_SAC_EVE]},
    "夜跑":               {"duration": (20, 45),   "slots": [_SAC_EVE]},
    "骑行上班":           {"duration": (20, 45),   "slots": [_SAC_AM]},
    "弹力带训练":         {"duration": (15, 30),   "slots": None},
    "户外徒步":           {"duration": (120, 480), "slots": [_SAC_AM]},
    "网球对打":           {"duration": (45, 90),   "slots": [_SAC_PM]},
    "羽毛球":             {"duration": (45, 90),   "slots": [_SAC_PM, _SAC_EVE]},
    "划船机训练":         {"duration": (15, 30),   "slots": None},
    "独自散步":           {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_PM, _SAC_EVE]},
    "阳台种花":           {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_PM]},
    "卡丁车":             {"duration": (8,  15),   "slots": [_SAC_PM, _SAC_EVE]},
    "打篮球":             {"duration": (30, 90),   "slots": [_SAC_PM, _SAC_EVE]},
    "夜间散步":           {"duration": (20, 40),   "slots": [_SAC_EVE]},
    "轻度拉伸":           {"duration": (10, 20),   "slots": [_SAC_AM, _SAC_EVE]},
    "滑板":               {"duration": (30, 60),   "slots": [_SAC_PM]},
    "打台球":             {"duration": (30, 60),   "slots": [_SAC_PM, _SAC_EVE]},
    "天台吹风":           {"duration": (15, 30),   "slots": [_SAC_EVE]},
    "轻柔拉伸":           {"duration": (10, 20),   "slots": [_SAC_AM, _SAC_EVE]},
    "傍晚散步":           {"duration": (20, 40),   "slots": [_SAC_PM, _SAC_EVE]},
    "阳台晒太阳":         {"duration": (15, 30),   "slots": [_SAC_AM, _SAC_PM]},
    "跑步":               {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_EVE]},
    "动感单车课":         {"duration": (30, 45),   "slots": [_SAC_PM, _SAC_EVE]},
    "乒乓球":             {"duration": (30, 60),   "slots": [_SAC_PM, _SAC_EVE]},
    "放风筝":             {"duration": (30, 60),   "slots": [_SAC_PM]},
    "下午瑜伽":           {"duration": (20, 45),   "slots": [_SAC_PM]},
    "跑酷训练":           {"duration": (45, 90),   "slots": [_SAC_PM]},
    "夜间骑行":           {"duration": (30, 60),   "slots": [_SAC_EVE]},
    "攀岩馆":             {"duration": (45, 90),   "slots": [_SAC_PM, _SAC_EVE]},
    "HIIT训练":           {"duration": (15, 30),   "slots": [_SAC_AM, _SAC_PM]},
    "游泳":               {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM, _SAC_EVE]},
    "深夜撸铁":           {"duration": (45, 75),   "slots": [_SAC_EVE]},
    "泡温泉":             {"duration": (60, 180),  "slots": [_SAC_PM, _SAC_EVE]},
    "夜钓":               {"duration": (120, 240), "slots": [_SAC_EVE]},
    "真人CS":             {"duration": (60, 120),  "slots": [_SAC_PM]},
    "射箭体验":           {"duration": (20, 40),   "slots": [_SAC_PM, _SAC_EVE]},
    "骑摩托兜风":         {"duration": (30, 90),   "slots": [_SAC_PM, _SAC_EVE]},
    # ── 二、预约与事务类 ────────────────────────────────────
    "心理咨询":           {"duration": (45, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "芳疗师咨询":         {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "体检预约":           {"duration": (90, 180),  "slots": [_SAC_AM]},
    "牙科洁牙":           {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "眼科检查":           {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_PM]},
    "芳疗预约":           {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "营养师咨询":         {"duration": (45, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "按摩理疗":           {"duration": (45, 90),   "slots": [_SAC_PM, _SAC_EVE]},
    "音乐疗愈预约":       {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "汽车保养":           {"duration": (60, 120),  "slots": [_SAC_AM, _SAC_PM]},
    "颈肩理疗":           {"duration": (30, 60),   "slots": [_SAC_PM, _SAC_EVE]},
    "过敏原检测":         {"duration": (15, 30),   "slots": [_SAC_AM]},
    "疫苗接种预约":       {"duration": (15, 30),   "slots": [_SAC_AM, _SAC_PM]},
    "牙科检查":           {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "理发预约":           {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "皮肤科复查":         {"duration": (10, 20),   "slots": [_SAC_AM, _SAC_PM]},
    "按摩预约":           {"duration": (45, 90),   "slots": [_SAC_PM, _SAC_EVE]},
    "运动损伤复查":       {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_PM]},
    "视力检查":           {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_PM]},
    "冥想指导课":         {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_EVE]},
    "运动康复评估":       {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_PM]},
    "骨科复诊":           {"duration": (15, 30),   "slots": [_SAC_AM, _SAC_PM]},
    "宠物疫苗":           {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_PM]},
    "颈椎理疗":           {"duration": (30, 60),   "slots": [_SAC_PM, _SAC_EVE]},
    "健身教练私教课":     {"duration": (45, 60),   "slots": [_SAC_AM, _SAC_PM, _SAC_EVE]},
    "配隐形眼镜":         {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "健身教练课":         {"duration": (45, 60),   "slots": [_SAC_AM, _SAC_PM, _SAC_EVE]},
    "配眼镜":             {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    # ── 三、社交与聚会类 ────────────────────────────────────
    "客户沟通":           {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_PM]},
    "同事下午茶":         {"duration": (15, 30),   "slots": [_SAC_PM]},
    "技术社区交流":       {"duration": (30, 60),   "slots": None},
    "产品路线图讨论":     {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "需求评审会":         {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "Sprint回顾会":       {"duration": (45, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "架构设计讨论":       {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "跨团队对齐会":       {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "邻居烧烤派对":       {"duration": (120, 240), "slots": [_SAC_PM, _SAC_EVE]},
    "团队会议":           {"duration": (20, 45),   "slots": [_SAC_AM]},
    "狼人杀之夜":         {"duration": (120, 240), "slots": [_SAC_EVE]},
    "新人指导会议":       {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "朋友聚会":           {"duration": (120, 240), "slots": [_SAC_PM, _SAC_EVE]},
    "技术Meetup":         {"duration": (90, 180),  "slots": [_SAC_EVE]},
    "电竞开黑":           {"duration": (45, 90),   "slots": [_SAC_EVE]},
    "黑客马拉松":         {"duration": (120, 480), "slots": [_SAC_AM, _SAC_PM]},
    "露营派对":           {"duration": (120, 240), "slots": [_SAC_PM, _SAC_EVE]},
    "烧烤之夜":           {"duration": (120, 240), "slots": [_SAC_PM, _SAC_EVE]},
    "技术评审":           {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "项目讨论":           {"duration": (15, 30),   "slots": [_SAC_AM, _SAC_PM]},
    "周末家庭聚会":       {"duration": (180, 300), "slots": [_SAC_PM]},
    "团队午餐":           {"duration": (45, 90),   "slots": [_SAC_NN]},
    "老同学叙旧":         {"duration": (120, 240), "slots": [_SAC_PM, _SAC_EVE]},
    "火锅局":             {"duration": (90, 150),  "slots": [_SAC_NN, _SAC_EVE]},
    "线上冥想小组":       {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_EVE]},
    "技术选型讨论":       {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "节日家宴":           {"duration": (180, 360), "slots": [_SAC_NN, _SAC_EVE]},
    "朋友聚餐":           {"duration": (90, 150),  "slots": [_SAC_NN, _SAC_EVE]},
    "深夜火锅局":         {"duration": (90, 150),  "slots": [_SAC_EVE]},
    "周末自驾游":         {"duration": (240, 480), "slots": [_SAC_AM]},
    "项目进度讨论":       {"duration": (15, 30),   "slots": [_SAC_AM, _SAC_PM]},
    "代码走查会":         {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "社区活动":           {"duration": (60, 120),  "slots": [_SAC_AM, _SAC_PM]},
    "亲子活动日":         {"duration": (180, 360), "slots": [_SAC_AM, _SAC_PM]},
    "创业者交流会":       {"duration": (90, 180),  "slots": [_SAC_PM, _SAC_EVE]},
    "电影之夜":           {"duration": (120, 180), "slots": [_SAC_EVE]},
    "周末聚餐":           {"duration": (90, 150),  "slots": [_SAC_NN, _SAC_EVE]},
    "烧烤聚会":           {"duration": (120, 240), "slots": [_SAC_PM, _SAC_EVE]},
    "开源社区线下聚":     {"duration": (120, 180), "slots": [_SAC_PM]},
    "深夜大排档":         {"duration": (60, 120),  "slots": [_SAC_EVE]},
    "游戏开黑":           {"duration": (45, 90),   "slots": [_SAC_EVE]},
    "技术方案评审":       {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "季度规划讨论":       {"duration": (60, 120),  "slots": [_SAC_AM, _SAC_PM]},
    "线上读书会":         {"duration": (45, 90),   "slots": [_SAC_EVE]},
    "桌游之夜":           {"duration": (120, 240), "slots": [_SAC_EVE]},
    "生日趴":             {"duration": (180, 300), "slots": [_SAC_EVE]},
    "一对一沟通":         {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_PM]},
    "客户需求评审":       {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "生日聚会":           {"duration": (180, 300), "slots": [_SAC_EVE]},
    "KTV聚会":            {"duration": (120, 240), "slots": [_SAC_EVE]},
    "部门月度总结会":     {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "导师辅导会议":       {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "跨部门协调会":       {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "两人下午茶":         {"duration": (60, 120),  "slots": [_SAC_PM]},
    "线上心理互助小组":   {"duration": (60, 90),   "slots": [_SAC_EVE]},
    "线上社交":           {"duration": (15, 45),   "slots": None},
    "团队周会":           {"duration": (20, 45),   "slots": [_SAC_AM]},
    "与家人视频通话":     {"duration": (15, 45),   "slots": [_SAC_EVE]},
    "小型读书分享":       {"duration": (45, 90),   "slots": [_SAC_PM, _SAC_EVE]},
    "小型私密聚会":       {"duration": (120, 240), "slots": [_SAC_PM, _SAC_EVE]},
    "小型手工沙龙":       {"duration": (90, 180),  "slots": [_SAC_PM]},
    "读书分享会":         {"duration": (60, 120),  "slots": [_SAC_PM]},
    # ── 四、个人成长与日常事务类 ────────────────────────────
    "学习新技能课程":     {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_EVE]},
    "写技术博客":         {"duration": (45, 120),  "slots": [_SAC_AM, _SAC_PM]},
    "数据分析任务":       {"duration": (30, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "撰写创意方案":       {"duration": (45, 90),   "slots": [_SAC_AM]},
    "编曲混音":           {"duration": (60, 180),  "slots": [_SAC_PM, _SAC_EVE]},
    "制定周计划":         {"duration": (20, 30),   "slots": [_SAC_AM]},
    "整理待办清单":       {"duration": (10, 20),   "slots": [_SAC_AM]},
    "规划明日任务":       {"duration": (10, 15),   "slots": [_SAC_EVE]},
    "学习投资理财":       {"duration": (20, 45),   "slots": [_SAC_EVE]},
    "学习时间管理方法":   {"duration": (15, 30),   "slots": [_SAC_AM]},
    "梳理项目文档":       {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "写作":               {"duration": (45, 120),  "slots": [_SAC_AM, _SAC_EVE]},
    "写读书笔记":         {"duration": (20, 45),   "slots": [_SAC_EVE]},
    "完成在线课程作业":   {"duration": (30, 60),   "slots": [_SAC_EVE]},
    "更新个人知识库":     {"duration": (20, 45),   "slots": [_SAC_EVE]},
    "复盘工作":           {"duration": (15, 30),   "slots": [_SAC_PM]},
    "写剧本大纲":         {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_EVE]},
    "学习新编程语言":     {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_EVE]},
    "准备演讲PPT":        {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "回顾季度OKR":        {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "阅读专业内容":       {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_EVE]},
    "完成项目方案":       {"duration": (45, 120),  "slots": [_SAC_AM, _SAC_PM]},
    "写小说章节":         {"duration": (45, 120),  "slots": [_SAC_AM, _SAC_EVE]},
    "深夜写作":           {"duration": (45, 90),   "slots": [_SAC_EVE]},
    "剪辑视频":           {"duration": (45, 180),  "slots": [_SAC_EVE]},
    "做副业项目":         {"duration": (45, 120),  "slots": [_SAC_EVE]},
    "编程练习":           {"duration": (45, 120),  "slots": [_SAC_AM, _SAC_EVE]},
    "整理电子笔记":       {"duration": (15, 30),   "slots": [_SAC_EVE]},
    "撰写工作报告":       {"duration": (30, 60),   "slots": [_SAC_PM]},
    "编程项目":           {"duration": (45, 120),  "slots": [_SAC_AM, _SAC_EVE]},
    "翻译外文资料":       {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "录制播客":           {"duration": (45, 120),  "slots": [_SAC_PM, _SAC_EVE]},
    "研究哲学文献":       {"duration": (30, 60),   "slots": [_SAC_EVE]},
    "优化工作流程":       {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_PM]},
    "研究行业趋势报告":   {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "阅读文学作品":       {"duration": (30, 90),   "slots": [_SAC_EVE]},
    "学习新框架":         {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_EVE]},
    "准备技术分享":       {"duration": (45, 120),  "slots": [_SAC_AM, _SAC_PM]},
    "撰写技术文档":       {"duration": (30, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "3D建模":             {"duration": (60, 180),  "slots": [_SAC_AM, _SAC_EVE]},
    "构思创意提案":       {"duration": (30, 60),   "slots": [_SAC_AM]},
    "整理旧物":           {"duration": (30, 60),   "slots": [_SAC_PM]},
    "整理GitHub仓库":     {"duration": (15, 30),   "slots": None},
    "研究AI工具":         {"duration": (20, 45),   "slots": None},
    "做数据可视化":       {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "复盘工作日志":       {"duration": (15, 30),   "slots": [_SAC_PM]},
    "阅读专业书籍":       {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_EVE]},
    "制定月度目标":       {"duration": (30, 45),   "slots": [_SAC_AM]},
    "学习茶道":           {"duration": (30, 60),   "slots": [_SAC_PM]},
    "练习书法":           {"duration": (20, 45),   "slots": [_SAC_EVE]},
    "完成代码审查":       {"duration": (15, 30),   "slots": [_SAC_AM, _SAC_PM]},
    "代码重构":           {"duration": (30, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "学做新菜谱":         {"duration": (45, 90),   "slots": [_SAC_PM, _SAC_EVE]},
    "打扫卫生间":         {"duration": (15, 30),   "slots": [_SAC_AM, _SAC_PM]},
    "设计UI原型":         {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "写影评":             {"duration": (20, 45),   "slots": [_SAC_EVE]},
    "刷LeetCode":         {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_EVE]},
    "搭建个人网站":       {"duration": (45, 120),  "slots": [_SAC_EVE]},
    "刷内容":             {"duration": (10, 30),   "slots": None},
    "研究新工具":         {"duration": (15, 30),   "slots": None},
    "整理技术笔记":       {"duration": (20, 45),   "slots": [_SAC_EVE]},
    "收拾阳台":           {"duration": (15, 30),   "slots": [_SAC_AM, _SAC_PM]},
    "听播客":             {"duration": (15, 60),   "slots": None},
    "洗车":               {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "绘画":               {"duration": (45, 120),  "slots": [_SAC_PM, _SAC_EVE]},
    "设计创作":           {"duration": (45, 120),  "slots": [_SAC_AM, _SAC_EVE]},
    "制作音乐":           {"duration": (60, 180),  "slots": [_SAC_PM, _SAC_EVE]},
    "听音乐":             {"duration": (15, 45),   "slots": None},
    "阅读小说":           {"duration": (30, 90),   "slots": [_SAC_EVE]},
    "看电影":             {"duration": (90, 150),  "slots": [_SAC_PM, _SAC_EVE]},
    "研究开源项目":       {"duration": (30, 60),   "slots": None},
    "逛购物网站":         {"duration": (15, 30),   "slots": None},
    "手工制作":           {"duration": (45, 120),  "slots": [_SAC_PM, _SAC_EVE]},
    "学习系统设计":       {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "简单家务整理":       {"duration": (10, 20),   "slots": None},
    "检查家电保养":       {"duration": (10, 15),   "slots": None},
    "整理购物清单":       {"duration": (5,  10),   "slots": None},
    "调色修图":           {"duration": (20, 60),   "slots": [_SAC_EVE]},
    "写情绪日记":         {"duration": (10, 15),   "slots": [_SAC_EVE]},
    "学习手冲咖啡":       {"duration": (15, 30),   "slots": [_SAC_AM]},
    "看视频学习":         {"duration": (15, 45),   "slots": None},
    "看科技评测":         {"duration": (10, 20),   "slots": None},
    "看技术直播":         {"duration": (30, 60),   "slots": None},
    "学习新内容":         {"duration": (20, 45),   "slots": None},
    "看漫画":             {"duration": (15, 45),   "slots": None},
    "看直播":             {"duration": (15, 60),   "slots": None},
    "写明信片":           {"duration": (5,  15),   "slots": None},
    "编织毛衣":           {"duration": (30, 90),   "slots": [_SAC_EVE]},
    "整理药箱":           {"duration": (10, 15),   "slots": None},
    "制作手账":           {"duration": (20, 45),   "slots": [_SAC_EVE]},
    "网购日用品":         {"duration": (10, 20),   "slots": None},
    "完成日常工作":       {"duration": (30, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "刷短视频":           {"duration": (10, 30),   "slots": None},
    "抄写经典段落":       {"duration": (15, 30),   "slots": [_SAC_AM, _SAC_EVE]},
    "更新API文档":        {"duration": (15, 30),   "slots": [_SAC_AM, _SAC_PM]},
    "研究竞品分析":       {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "整理冰箱":           {"duration": (10, 20),   "slots": None},
    "整理鞋柜":           {"duration": (10, 20),   "slots": None},
    "写诗":               {"duration": (15, 45),   "slots": [_SAC_EVE]},
    "写信给未来的自己":   {"duration": (15, 30),   "slots": [_SAC_EVE]},
    "整理数码照片":       {"duration": (20, 45),   "slots": [_SAC_EVE]},
    "整理个人空间":       {"duration": (15, 30),   "slots": None},
    "追剧":               {"duration": (40, 90),   "slots": [_SAC_EVE]},
    "研究咖啡豆":         {"duration": (15, 30),   "slots": None},
    "听脱口秀":           {"duration": (15, 60),   "slots": None},
    "听有声书":           {"duration": (15, 60),   "slots": None},
    "写旅行日记":         {"duration": (15, 30),   "slots": [_SAC_EVE]},
    "写感恩日记":         {"duration": (5,  10),   "slots": [_SAC_EVE]},
    "学习冥想技巧":       {"duration": (10, 20),   "slots": [_SAC_AM, _SAC_EVE]},
    "整理衣柜":           {"duration": (20, 45),   "slots": [_SAC_PM]},
    "整理照片相册":       {"duration": (20, 45),   "slots": [_SAC_EVE]},
    "整理房间":           {"duration": (20, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "阅读":               {"duration": (20, 60),   "slots": None},
    "更换床品":           {"duration": (10, 20),   "slots": [_SAC_AM]},
    "写购物心得":         {"duration": (10, 20),   "slots": None},
    "学习调香":           {"duration": (30, 60),   "slots": [_SAC_PM]},
    "布置房间":           {"duration": (20, 60),   "slots": [_SAC_PM]},
    "看综艺节目":         {"duration": (40, 90),   "slots": [_SAC_EVE]},
    "做旅行攻略":         {"duration": (20, 45),   "slots": [_SAC_EVE]},
    "看体育赛事回放":     {"duration": (30, 60),   "slots": None},
    "整理歌单":           {"duration": (15, 30),   "slots": None},
    "写美食点评":         {"duration": (5,  15),   "slots": None},
    "烘焙甜点":           {"duration": (60, 120),  "slots": [_SAC_PM]},
    "写日记":             {"duration": (10, 20),   "slots": [_SAC_EVE]},
    "整理书架":           {"duration": (10, 20),   "slots": None},
    "制作歌单":           {"duration": (15, 30),   "slots": None},
    "学做新菜":           {"duration": (45, 90),   "slots": [_SAC_PM, _SAC_EVE]},
    # ── 五、休闲放松类 ──────────────────────────────────────
    "泡茶放松":           {"duration": (15, 30),   "slots": [_SAC_PM, _SAC_EVE]},
    "林间漫步":           {"duration": (30, 60),   "slots": [_SAC_AM, _SAC_PM]},
    "简单运动":           {"duration": (10, 20),   "slots": None},
    "做兴趣爱好":         {"duration": (45, 90),   "slots": [_SAC_EVE]},
    "夜间听黑胶唱片":     {"duration": (30, 60),   "slots": [_SAC_EVE]},
    "听轻音乐":           {"duration": (20, 45),   "slots": None},
    "去咖啡馆坐坐":       {"duration": (30, 90),   "slots": [_SAC_PM]},
    "翻阅画册":           {"duration": (15, 30),   "slots": None},
    "逛安静的寺庙":       {"duration": (45, 90),   "slots": [_SAC_AM, _SAC_PM]},
    "独自看海":           {"duration": (30, 90),   "slots": [_SAC_PM]},
    "窗边看月亮":         {"duration": (10, 20),   "slots": [_SAC_EVE]},
    "台球":               {"duration": (30, 60),   "slots": [_SAC_PM, _SAC_EVE]},
    "看日出":             {"duration": (40, 90),   "slots": [_SAC_AM]},
    "做面部护理":         {"duration": (15, 30),   "slots": [_SAC_EVE]},
    "逛超市":             {"duration": (30, 60),   "slots": [_SAC_PM, _SAC_EVE]},
    "看艺术展":           {"duration": (60, 120),  "slots": [_SAC_PM]},
    "在咖啡馆角落看书":   {"duration": (45, 90),   "slots": [_SAC_PM]},
    "湖边静坐":           {"duration": (20, 45),   "slots": [_SAC_AM, _SAC_PM]},
    "逛夜市":             {"duration": (60, 120),  "slots": [_SAC_EVE]},
    "深夜电台":           {"duration": (30, 60),   "slots": [_SAC_EVE]},
    "看纪录片":           {"duration": (40, 90),   "slots": [_SAC_EVE]},
    "深夜听雨":           {"duration": (15, 30),   "slots": [_SAC_EVE]},
    "做夜宵":             {"duration": (15, 30),   "slots": [_SAC_EVE]},
    "去24小时书店":       {"duration": (45, 90),   "slots": [_SAC_EVE]},
    "逛书店":             {"duration": (30, 90),   "slots": [_SAC_PM, _SAC_EVE]},
    "泡澡放空":           {"duration": (20, 40),   "slots": [_SAC_EVE]},
    "阳台观星":           {"duration": (15, 30),   "slots": [_SAC_EVE]},
    "听古典音乐":         {"duration": (20, 60),   "slots": [_SAC_EVE]},
    "听钢琴曲":           {"duration": (20, 60),   "slots": [_SAC_EVE]},
    "做渐进式肌肉放松":   {"duration": (10, 20),   "slots": [_SAC_EVE]},
    "VR体验":             {"duration": (20, 45),   "slots": [_SAC_PM, _SAC_EVE]},
    "密室逃脱":           {"duration": (60, 120),  "slots": [_SAC_PM, _SAC_EVE]},
    "开车兜风":           {"duration": (30, 60),   "slots": [_SAC_PM, _SAC_EVE]},
    "泡咖啡馆发呆":       {"duration": (30, 90),   "slots": [_SAC_PM]},
    "逛深夜食堂":         {"duration": (30, 60),   "slots": [_SAC_EVE]},
    "听音乐放松":         {"duration": (15, 45),   "slots": None},
    "参观摄影展":         {"duration": (45, 90),   "slots": [_SAC_PM]},
    "做手冲咖啡":         {"duration": (10, 20),   "slots": [_SAC_AM, _SAC_PM]},
    "露营看星星":         {"duration": (120, 240), "slots": [_SAC_EVE]},
    "KTV唱歌":            {"duration": (120, 240), "slots": [_SAC_EVE]},
    "去酒吧小酌":         {"duration": (60, 120),  "slots": [_SAC_EVE]},
    "深夜便利店":         {"duration": (10, 20),   "slots": [_SAC_EVE]},
    "看午夜场电影":       {"duration": (100, 140), "slots": [_SAC_EVE]},
    "去海边听浪":         {"duration": (30, 60),   "slots": [_SAC_PM, _SAC_EVE]},
    "逛独立书店":         {"duration": (30, 90),   "slots": [_SAC_PM, _SAC_EVE]},
    "桌游":               {"duration": (90, 180),  "slots": [_SAC_PM, _SAC_EVE]},
    "电竞比赛":           {"duration": (30, 60),   "slots": [_SAC_EVE]},
}


def _lookup_activity_constraint(event_name):
    """从 SCHEDULE_ACTIVITY_CONSTRAINTS 查找活动约束，优先精确匹配，再尝试包含匹配。"""
    nm = str(event_name or "").strip()
    if not nm:
        return None
    if nm in SCHEDULE_ACTIVITY_CONSTRAINTS:
        return SCHEDULE_ACTIVITY_CONSTRAINTS[nm]
    for key, val in SCHEDULE_ACTIVITY_CONSTRAINTS.items():
        if key in nm or nm in key:
            return val
    return None


def _qwen_clamp_max_tokens(max_tokens):
    """将 max_tokens 限制在 [1, _QWEN_MAX_TOKENS]。"""
    try:
        v = int(max_tokens)
    except (TypeError, ValueError):
        v = _QWEN_MAX_TOKENS
    return max(1, min(v, _QWEN_MAX_TOKENS))


def _llm_vendor() -> str:
    """大模型供应方：豆包（火山方舟）或通义；见 ``llm_vendor_config.resolve_llm_vendor``。"""
    from llm_vendor_config import resolve_llm_vendor

    return resolve_llm_vendor()


def _qwen_chat_url():
    """OpenAI 兼容 chat/completions 完整 URL（通义 DashScope 或火山方舟豆包，由 LLM_VENDOR 决定）。"""
    if _llm_vendor() == "doubao":
        base = (
            os.getenv("DOUBAO_BASE_URL")
            or os.getenv("BASE_URL")
            or "https://ark.cn-beijing.volces.com/api/v3"
        ).rstrip("/")
        return f"{base}/chat/completions"
    base = (
        os.getenv("QWEN_BASE_URL")
        or os.getenv("DASHSCOPE_BASE_URL")
        or os.getenv("TRANSLATE_BASE_URL")
        or os.getenv("BASE_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")
    return f"{base}/chat/completions"


def _qwen_api_key():
    """通义：优先 DASHSCOPE_API_KEY；豆包：优先 DOUBAO_API_KEY（见 LLM_VENDOR）。"""
    if _llm_vendor() == "doubao":
        return os.getenv("DOUBAO_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    return os.getenv("DASHSCOPE_API_KEY") or os.getenv("DOUBAO_API_KEY")


def _qwen_model_name():
    """模型名：通义优先 QWEN_MODEL_NAME；豆包优先 DOUBAO_MODEL_NAME / MODEL_NAME。"""
    if _llm_vendor() == "doubao":
        return (
            os.getenv("DOUBAO_MODEL_NAME")
            or os.getenv("MODEL_NAME")
            or os.getenv("QWEN_MODEL_NAME")
            or "doubao-seed-2-0-mini-260215"
        )
    return os.getenv("QWEN_MODEL_NAME") or os.getenv("MODEL_NAME") or "qwen-plus"
_SLEEP_CLOCK_HHMM_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
PROMPT_DIR = os.path.join(PROJECT_ROOT, "prompt")


def render_prompt_template(template_name, replacements=None):
    template_path = os.path.join(PROMPT_DIR, template_name)
    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()
    lines = content.splitlines()
    while lines and lines[0].startswith("# 来源"):
        lines.pop(0)
    if lines and not lines[0].strip():
        lines.pop(0)
    content = "\n".join(lines)
    if replacements:
        for key, value in replacements.items():
            content = content.replace(f"{{{{{key}}}}}", str(value))
    return content


def load_prompt_instruction(template_name):
    """读取 prompt 目录下 md 模板，作为模型 system prompt。"""
    template_path = os.path.join(PROMPT_DIR, template_name)
    if not os.path.exists(template_path):
        return ""
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


# 计算两个时间之间的分钟数
def calculate_duration(start_time_str, end_time_str, anchor_date_str=None):
    """计算两时刻之间的分钟数；支持 ISO(UTC) 或 idf_data 中的 HH:MM(:SS)。"""
    def to_dt(s):
        if not s or not isinstance(s, str):
            return None
        s = s.strip()
        if not s:
            return None
        if _SLEEP_CLOCK_HHMM_RE.match(s):
            if anchor_date_str:
                try:
                    base_d = datetime.strptime(anchor_date_str[:10], "%Y-%m-%d").date()
                except ValueError:
                    base_d = datetime(2000, 1, 1).date()
            else:
                base_d = datetime(2000, 1, 1).date()
            if len(s) <= 5:
                tm = datetime.strptime(s, "%H:%M").time()
            else:
                tm = datetime.strptime(s, "%H:%M:%S").time()
            return datetime.combine(base_d, tm)
        try:
            return datetime.fromisoformat(s.replace("Z", ""))
        except ValueError:
            return None

    start_time = to_dt(start_time_str)
    end_time = to_dt(end_time_str)
    if start_time is None or end_time is None:
        return 0

    # 处理跨天（如 23:50 → 00:15）
    if end_time < start_time:
        end_time += timedelta(days=1)

    delta = end_time - start_time
    return int(delta.total_seconds() / 60)


def minutes_between_datetimes(start_dt, end_dt):
    """两时刻之间的整分钟数（可跨日）。"""
    e = end_dt
    if e < start_dt:
        e += timedelta(days=1)
    return int((e - start_dt).total_seconds() / 60)


def distribute_sleep_stage_minutes(total_sleep_minutes, deep_ratio, light_ratio, rem_ratio):
    """浅睡/深睡/REM 相对净睡眠的比例（三者之和为100），拆成整数分钟且三者之和等于 total_sleep_minutes。"""
    t = int(total_sleep_minutes)
    if t <= 0:
        return 0, 0, 0
    d = t * int(deep_ratio) // 100
    l = t * int(light_ratio) // 100
    r = t * int(rem_ratio) // 100
    diff = t - d - l - r
    l += diff
    return d, l, r


def _normalize_three_int100(d, l, r):
    """将三个非负整数比例缩放到和严格为 100（最大余数法），供净睡眠 TST 上拆段。"""
    d, l, r = max(0, int(d)), max(0, int(l)), max(0, int(r))
    s = d + l + r
    if s <= 0:
        return 34, 33, 33
    scaled = [100.0 * d / s, 100.0 * l / s, 100.0 * r / s]
    floors = [int(math.floor(x)) for x in scaled]
    rem = 100 - sum(floors)
    if rem > 0:
        fracs = sorted([(scaled[i] - floors[i], i) for i in range(3)], key=lambda x: -x[0])
        for k in range(rem):
            floors[fracs[k % 3][1]] += 1
    elif rem < 0:
        fracs = sorted([(scaled[i] - floors[i], i) for i in range(3)], key=lambda x: x[0])
        for k in range(-rem):
            for _, i in fracs:
                if floors[i] > 0:
                    floors[i] -= 1
                    break
    return int(floors[0]), int(floors[1]), int(floors[2])


def idf_all_awake_minutes(sleep_data):
    """idf_data 中所有 awake 段的分钟数之和（本地 HH:MM，支持跨日）。"""
    idf = sleep_data.get("idf_data") or []
    raw = sleep_data.get("raw_data") or {}
    anchor = sleep_data.get("record_date") or raw.get("record_date")
    total = 0
    for seg in idf:
        if (seg.get("stage") or "") != "awake":
            continue
        st, en = seg.get("start"), seg.get("end")
        if not st or not en:
            continue
        total += calculate_duration(st, en, anchor)
    return int(total)


def _idf_timeline_from_stages(stage_rows):
    """将 idf 段展开为按分钟的阶段列表（与段顺序一致）。"""
    tl = []
    for seg in stage_rows or []:
        sh, sm = map(int, seg["start"].split(":"))
        eh, em = map(int, seg["end"].split(":"))
        d = (eh * 60 + em) - (sh * 60 + sm)
        if d <= 0:
            d += 1440
        stg = seg.get("stage") or ""
        tl.extend([stg] * int(d))
    return tl


def _idf_stages_from_timeline(tl, anchor_dt):
    """分钟级阶段列表还原为 idf 段（连续同类合并）。"""
    if not tl:
        return []
    out = []
    seg_start = 0
    cur_stage = tl[0]
    for idx in range(1, len(tl) + 1):
        is_break = idx == len(tl) or tl[idx] != cur_stage
        if not is_break:
            continue
        st = anchor_dt + timedelta(minutes=seg_start)
        et = anchor_dt + timedelta(minutes=idx)
        out.append(
            {
                "stage": cur_stage,
                "start": st.strftime("%H:%M"),
                "end": et.strftime("%H:%M"),
            }
        )
        if idx < len(tl):
            seg_start = idx
            cur_stage = tl[idx]
    return out


def _runs_non_awake_in_window(tl, lo, hi):
    """[lo, hi) 内非 awake 的连续同质段 (start, end, stage)。"""
    runs = []
    i = lo
    while i < hi:
        if tl[i] == "awake":
            i += 1
            continue
        stg = tl[i]
        s = i
        while i < hi and tl[i] == stg:
            i += 1
        runs.append((s, i - 1, stg))
    return runs


def _runs_light_in_window(tl, lo, hi):
    """[lo, hi) 内浅睡连续段。"""
    runs = []
    i = lo
    while i < hi:
        if tl[i] != "light":
            i += 1
            continue
        s = i
        while i < hi and tl[i] == "light":
            i += 1
        runs.append((s, i - 1))
    return runs


def _fragment_idf_timeline_high_sensitivity(tl, sleep_off, wake_off, max_run=None, max_iter=300):
    """
    高敏感：通过「同质段内部与外侧异质分钟」对调，打断过长的单段深/浅/REM，
    使结构更碎；不改动各阶段总分钟数。max_run 控制单段上限（避免仍很长）。
    """
    if not tl or wake_off <= sleep_off:
        return tl
    if max_run is None:
        max_run = random.randint(22, 28)
    lo, hi = int(sleep_off), int(wake_off)
    for _ in range(max_iter):
        runs = _runs_non_awake_in_window(tl, lo, hi)
        bad = [r for r in runs if r[1] - r[0] + 1 > max_run]
        if not bad:
            break
        s, e, S = random.choice(bad)
        length = e - s + 1
        mid = s + length // 2
        donors = [
            p
            for p in range(lo, hi)
            if (p < s or p > e) and tl[p] != "awake" and tl[p] != S
        ]
        if not donors:
            break
        p = random.choice(donors)
        tl[mid], tl[p] = tl[p], tl[mid]
    return tl


def _smooth_light_runs_low_sensitivity_timeline(
    tl, sleep_off, wake_off, max_spread=16, max_iter=200
):
    """
    低敏感：缩小各浅睡连续段之间的时长差距（把偏长段边缘的浅睡与邻接深/REM 对调），
    在保持浅睡总分钟数不变的前提下，让浅睡段时长更均匀、过渡不那么突兀。
    """
    if not tl or wake_off <= sleep_off:
        return tl
    lo, hi = int(sleep_off), int(wake_off)
    for _ in range(max_iter):
        light_runs = _runs_light_in_window(tl, lo, hi)
        if len(light_runs) < 2:
            break
        lens = sorted(
            [(b - a + 1, a, b) for a, b in light_runs],
            key=lambda x: -x[0],
        )
        long_len, long_s, long_e = lens[0]
        short_len, short_s, short_e = lens[-1]
        if long_len - short_len <= max_spread:
            break
        swapped = False
        for u in (long_e, long_s):
            if not (lo <= u < hi) or tl[u] != "light":
                continue
            for v in (short_e + 1, short_s - 1):
                if not (lo <= v < hi):
                    continue
                if tl[v] not in ("deep", "rem"):
                    continue
                if u == v:
                    continue
                tl[u], tl[v] = tl[v], tl[u]
                swapped = True
                break
            if swapped:
                break
        if not swapped:
            for u in (long_e, long_s):
                if not (lo <= u < hi) or tl[u] != "light":
                    continue
                pool = [
                    v
                    for v in range(lo, hi)
                    if tl[v] in ("deep", "rem")
                    and not (short_s <= v <= short_e)
                    and not (long_s <= v <= long_e)
                    and abs(v - u) >= 8
                ]
                if not pool:
                    continue
                v = random.choice(pool)
                tl[u], tl[v] = tl[v], tl[u]
                swapped = True
                break
        if not swapped:
            break
    return tl


def _enforce_light_front_lt_back_timeline(tl, sleep_off, wake_off):
    """
    在分钟时间轴 [sleep_off, wake_off) 内，保证前半夜浅睡总分钟 < 后半夜浅睡总分钟；
    用前半夜浅睡与后半夜深/REM 对调实现（与 _enforce_light_back_heavier 同逻辑），
    不改变 deep/light/rem 总分钟数。
    """
    if not tl or wake_off <= sleep_off:
        return tl
    lo, hi = int(sleep_off), int(wake_off)
    wake_m = min(hi, len(tl))
    sleep_m = min(lo, len(tl))
    if wake_m <= sleep_m or sleep_m >= len(tl):
        return tl
    mid = sleep_m + (wake_m - sleep_m) // 2
    front_idx = [i for i in range(sleep_m, mid) if tl[i] == "light"]
    back_light = sum(1 for i in range(mid, wake_m) if tl[i] == "light")
    front_light = len(front_idx)
    need = front_light - back_light + 1
    if need <= 0:
        return tl
    back_non_light = [i for i in range(mid, wake_m) if tl[i] in ("deep", "rem")]
    if not back_non_light:
        return tl
    swaps = min(need, len(front_idx), len(back_non_light))
    front_pick = sorted(front_idx, reverse=True)[:swaps]
    back_pick = sorted(back_non_light)[:swaps]
    for fi, bi in zip(front_pick, back_pick):
        tl[fi], tl[bi] = tl[bi], tl[fi]
    return tl


def _redistribute_excess_light_low_sensitivity_timeline(
    tl, sleep_off, wake_off, max_light_run=60
):
    """
    低敏感：连续浅睡超过 max_light_run（默认 60）的部分不再保留为浅睡，
    改为深睡或 REM（净睡眠期内总分钟数不变，仅浅睡减少、深/REM 增加）。
    前半夜偏多标为深睡，后半夜偏多标为 REM，略随机。
    """
    if not tl or wake_off <= sleep_off:
        return tl
    lo, hi = int(sleep_off), int(wake_off)
    mid = lo + (hi - lo) // 2
    for a, b in _runs_light_in_window(tl, lo, hi):
        ln = b - a + 1
        if ln <= max_light_run:
            continue
        for idx in range(a + max_light_run, b + 1):
            if idx < mid:
                tl[idx] = "deep" if random.random() < 0.66 else "rem"
            else:
                tl[idx] = "rem" if random.random() < 0.58 else "deep"
    return tl


def _low_s_light_redistribute_and_balance_timeline(tl, sleep_off, wake_off):
    """低敏感：超长浅睡改标为深/REM 后，立刻做前半夜浅睡 < 后半夜浅睡。"""
    tl = _redistribute_excess_light_low_sensitivity_timeline(tl, sleep_off, wake_off)
    tl = _enforce_light_front_lt_back_timeline(tl, sleep_off, wake_off)
    return tl


def _tst_phase_minutes_in_timeline_window(tl, lo, hi):
    """时间轴 [lo, hi) 内 deep/light/rem 分钟计数（不含 awake）。"""
    md = ml = mr = 0
    for i in range(int(lo), int(hi)):
        if i < 0 or i >= len(tl):
            continue
        s = tl[i]
        if s == "deep":
            md += 1
        elif s == "light":
            ml += 1
        elif s == "rem":
            mr += 1
    return md, ml, mr


def sleep_report_structure_minutes_and_percents(sleep_data):
    """
    睡眠报告 sleep_structure 用分钟与两套占比：

    - 分钟：深/浅/REM 由 total_sleep_minutes + raw 三占比拆分；清醒为 idf_data 全部 awake 段之和。
    - 饼图 percent（pie_aw…pie_r）：由四段分钟归一，**四者之和恒为 100**，便于同屏环形/条形与分钟条一致。
    - 健康口径（health_aw…health_r）：与 raw_data 对齐 —— 深/浅/REM 为占净睡(TST)%；清醒为 awake_ratio（占 TIB%），
      缺失时用 清醒分钟/上床→起床 估算。供与 health 对账、阈值判定（如 pick_main_title）、文案中的「临床%」使用。

    返回 (m_aw, m_d, m_l, m_r, pie_aw, pie_d, pie_l, pie_r, health_aw, health_d, health_l, health_r)。
    """
    raw = sleep_data.get("raw_data") or {}
    record_date = (sleep_data or {}).get("record_date") or None
    tst = max(0, int(raw.get("total_sleep_minutes") or 0))
    d0 = int(raw.get("deep_sleep_ratio") or 0)
    l0 = int(raw.get("light_sleep_ratio") or 0)
    r0 = int(raw.get("rem_ratio") or 0)
    dp, lp, rp = _normalize_three_int100(d0, l0, r0)
    m_d, m_l, m_r = distribute_sleep_stage_minutes(tst, dp, lp, rp)
    m_aw = idf_all_awake_minutes(sleep_data)

    pie_aw, pie_d, pie_l, pie_r = _int100_from_four_floats(
        [float(m_aw), float(m_d), float(m_l), float(m_r)]
    )

    health_d, health_l, health_r = int(dp), int(lp), int(rp)
    try:
        health_aw = max(0, min(100, int(raw.get("awake_ratio"))))
    except (TypeError, ValueError):
        health_aw = -1
    if health_aw < 0:
        tib = calculate_duration(
            raw.get("bed_time", ""),
            raw.get("wake_up_time", ""),
            record_date,
        )
        if tib and tib > 0:
            health_aw = max(0, min(100, int(round(100.0 * float(m_aw) / float(tib)))))
        else:
            tot = float(tst + m_aw)
            health_aw = (
                max(0, min(100, int(round(100.0 * float(m_aw) / tot))))
                if tot > 0
                else 0
            )

    return m_aw, m_d, m_l, m_r, pie_aw, pie_d, pie_l, pie_r, health_aw, health_d, health_l, health_r


def _split_four_spt_percents_to_int100(night_waso_minutes, deep_mins, light_mins, rem_mins, spt_minutes):
    """清醒(WASO)/深睡/浅睡/REM 占 SPT（入睡→起床）的百分比，四者之和严格为 100（整数）。"""
    spt = max(1, int(spt_minutes))
    parts = [
        100.0 * float(night_waso_minutes) / spt,
        100.0 * float(deep_mins) / spt,
        100.0 * float(light_mins) / spt,
        100.0 * float(rem_mins) / spt,
    ]
    floors = [int(math.floor(p)) for p in parts]
    rem = 100 - sum(floors)
    if rem > 0:
        fracs = sorted([(parts[i] - floors[i], i) for i in range(4)], key=lambda x: -x[0])
        for k in range(rem):
            floors[fracs[k % 4][1]] += 1
    elif rem < 0:
        fracs = sorted([(parts[i] - floors[i], i) for i in range(4)], key=lambda x: x[0])
        for k in range(-rem):
            for _, i in fracs:
                if floors[i] > 0:
                    floors[i] -= 1
                    break
    return tuple(int(x) for x in floors)


def _int100_from_four_floats(parts):
    """四个非负浮点缩放到和为 100 后，用最大余数法得到和严格为 100 的四个整数百分比。"""
    parts = [max(0.0, float(x)) for x in parts]
    s = sum(parts)
    if s <= 0:
        return (2, 34, 32, 32)
    scaled = [100.0 * p / s for p in parts]
    floors = [int(math.floor(x)) for x in scaled]
    rem = 100 - sum(floors)
    if rem > 0:
        fracs = sorted([(scaled[i] - floors[i], i) for i in range(4)], key=lambda x: -x[0])
        for k in range(rem):
            floors[fracs[k % 4][1]] += 1
    elif rem < 0:
        fracs = sorted([(scaled[i] - floors[i], i) for i in range(4)], key=lambda x: x[0])
        for k in range(-rem):
            for _, i in fracs:
                if floors[i] > 0:
                    floors[i] -= 1
                    break
    return tuple(int(x) for x in floors)


def normalize_four_spt_stage_percents(awake_p, deep_p, light_p, rem_p):
    """清醒/深睡/浅睡/REM 占 SPT 的整数百分比，规范为严格和为 100（与人格配置语义一致）。"""
    try:
        aw = int(round(float(awake_p)))
        d = int(round(float(deep_p)))
        l = int(round(float(light_p)))
        r = int(round(float(rem_p)))
    except (TypeError, ValueError):
        return _int100_from_four_floats([0.0, 0.0, 0.0, 0.0])
    aw, d, l, r = max(0, aw), max(0, d), max(0, l), max(0, r)
    if aw + d + l + r == 100:
        return aw, d, l, r
    return _int100_from_four_floats([float(aw), float(d), float(l), float(r)])


def spt_minutes_from_raw_sleep_window(raw_data, record_date_str=None):
    """SPT（入睡→起床）分钟数，与 generate_sleep_data 中四阶段占比分母一致。"""
    spt = calculate_duration(
        (raw_data or {}).get("sleep_time", ""),
        (raw_data or {}).get("wake_time", ""),
        record_date_str,
    )
    if spt >= 1:
        return int(spt)
    tst = int((raw_data or {}).get("total_sleep_minutes", 0) or 0)
    try:
        ar = float((raw_data or {}).get("awake_ratio", 0) or 0) / 100.0
    except (TypeError, ValueError):
        ar = 0.0
    if ar >= 0.999:
        return max(1, tst)
    return max(1, int(round(float(tst) / max(1e-6, (1.0 - ar)))))


def four_stage_minutes_on_spt(raw_data, record_date_str=None):
    """
    按 SPT 与四阶段整数占比（和为 100）拆出清醒/深睡/浅睡/REM 分钟，与 raw_data 写入逻辑一致。
    返回 (m_aw, m_d, m_l, m_r, spt, aw%, d%, l%, r%)。
    """
    rd = raw_data or {}
    spt = spt_minutes_from_raw_sleep_window(rd, record_date_str)
    aw, d, l, r = normalize_four_spt_stage_percents(
        rd.get("awake_ratio", 0),
        rd.get("deep_sleep_ratio", 0),
        rd.get("light_sleep_ratio", 0),
        rd.get("rem_ratio", 0),
    )
    alloc = _spt_minutes_from_int_percents(spt, aw, d, l, r)
    if not alloc:
        alloc = _spt_minutes_from_int_percents(spt, *_int100_from_four_floats([float(aw), float(d), float(l), float(r)]))
    m_aw, m_d, m_l, m_r = alloc
    return m_aw, m_d, m_l, m_r, spt, aw, d, l, r


def _spt_minutes_from_int_percents(spt, p_aw, p_d, p_l, p_r):
    """SPT 总分钟 spt；四段整数占比和为 100；返回 (m_aw, m_d, m_l, m_r) 整数分钟且和为 spt。"""
    spt = int(max(1, spt))
    ps = [int(p_aw), int(p_d), int(p_l), int(p_r)]
    if sum(ps) != 100:
        return None
    floats = [spt * p / 100.0 for p in ps]
    floors = [int(math.floor(x)) for x in floats]
    rem = spt - sum(floors)
    if rem > 0:
        fracs = sorted([(floats[i] - floors[i], i) for i in range(4)], key=lambda x: -x[0])
        for k in range(rem):
            floors[fracs[k % 4][1]] += 1
    elif rem < 0:
        fracs = sorted([(floats[i] - floors[i], i) for i in range(4)], key=lambda x: x[0])
        for k in range(-rem):
            for _, i in fracs:
                if floors[i] > 0:
                    floors[i] -= 1
                    break
    return tuple(int(x) for x in floors)


def load_environment_rows_for_record_date(user_id, record_date, output_dir=None):
    """读取 output 下 environment 文件中某一 record_date 的条目。"""
    output_dir = output_dir or os.getenv("OUTPUT_DIR", "output")
    path = os.path.join(output_dir, f"{user_id}_environment_data.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [
        row
        for row in data
        if isinstance(row, dict)
        and row.get("uid") == user_id
        and row.get("record_date") == record_date
    ]


def _environment_comfort_subscore(env_rows):
    """环境舒适度子分 0–100：无数据时中性分；有数据则按温湿度/噪声粗略扣分。"""
    if not env_rows:
        return 72.0
    temps = []
    noises = []
    for r in env_rows:
        t = r.get("temperature")
        if isinstance(t, (int, float)):
            temps.append(float(t))
        n = r.get("noise")
        if isinstance(n, (int, float)):
            noises.append(float(n))
    pen = 0.0
    if temps:
        mt = sum(temps) / len(temps)
        if mt < 17.0 or mt > 28.0:
            pen += 28.0
        elif mt < 18.0 or mt > 26.0:
            pen += 14.0
    if noises:
        mn = sum(noises) / len(noises)
        if mn >= 65.0:
            pen += 26.0
        elif mn >= 55.0:
            pen += 14.0
        elif mn >= 48.0:
            pen += 6.0
    return max(22.0, min(100.0, 100.0 - pen))


def _rule_subscore_deep(d):
    d = float(d or 0)
    if d >= 20:
        return 100.0
    if d >= 15:
        return 82.0
    return max(28.0, 55.0 - (15.0 - d) * 4.5)


def _rule_subscore_light(l):
    l = float(l or 0)
    if 50.0 <= l <= 55.0:
        return 100.0
    if 50.0 < l <= 60.0:
        return 90.0
    if 60.0 < l <= 65.0:
        return 76.0
    if l < 50.0:
        return 58.0
    return 68.0


def _rule_subscore_rem(r):
    r = float(r or 0)
    if 25.0 <= r < 28.0:
        return 100.0
    if r >= 28.0:
        return 76.0
    if r >= 20.0:
        return 82.0
    return max(30.0, 55.0 - (20.0 - r) * 4.0)


def _rule_subscore_awake(a):
    a = float(a or 0)
    if a <= 5.0:
        return 100.0
    if a <= 10.0:
        return 80.0
    return max(25.0, 72.0 - (a - 10.0) * 5.5)


def _rule_subscore_tst(mins):
    """
    总睡眠时长评分（0–100）：
    - 8h–9h（480–540 min）：最优，100 分
    - 7h–8h（420–480 min）：稍微扣分，线性从 100 降至 72
    - <7h（<420 min）：短睡重罚，线性从 72 急降（约每少睡 1 分钟扣 0.9 分），下限 18
    - >9h（>540 min）：稍微扣分，线性从 100 缓降，下限 55
    """
    m = float(int(mins or 0))
    if 480.0 <= m <= 540.0:
        return 100.0
    if 420.0 <= m < 480.0:
        # 7h→8h：轻度扣分，72–100
        return 72.0 + (m - 420.0) * (28.0 / 60.0)
    if m < 420.0:
        # <7h：每少睡 1 分钟扣 0.9 分；约 6h 净睡时该子项已触达下限 18
        return max(18.0, 72.0 - (420.0 - m) * (72.0 / 80.0))
    # >9h：稍微扣分
    return max(55.0, 100.0 - (m - 540.0) * (45.0 / 120.0))


def _rule_subscore_apnea(n):
    n = int(n or 0)
    if n < 5:
        return 100.0
    return 32.0


def _rule_subscore_respiration(rr):
    r = float(rr or 0)
    if 12.0 <= r <= 18.0:
        return 100.0
    if r > 18.0:
        return max(25.0, 88.0 - (r - 18.0) * 8.0)
    return max(25.0, 88.0 - (12.0 - r) * 9.0)


def _rule_subscore_hr(hr):
    h = float(hr or 0)
    if h > 80.0:
        return 30.0
    if h < 45.0:
        return 45.0
    if 50.0 <= h <= 65.0:
        return 100.0
    if 45.0 <= h < 50.0:
        return 70.0
    if 65.0 < h <= 70.0:
        return 86.0
    if 70.0 < h <= 80.0:
        return 66.0
    return 78.0


def _rule_subscore_efficiency(e):
    x = float(e or 0)
    if x > 90.0:
        return 100.0
    if x >= 85.0:
        return 84.0
    if x >= 80.0:
        return 62.0
    if x >= 70.0:
        return 38.0
    return 20.0


def _rule_subscore_latency(lat):
    """
    入睡潜伏期评分（0–100）：
    - ≤20 min：满分 100
    - 20–25 min：稍微扣分，线性 100 → 85
    - 25–30 min：扣分稍多，线性 85 → 65
    - >30 min：扣分再多一些，线性 65 急降，下限 20
    """
    m = float(lat or 0)
    if m <= 20.0:
        return 100.0
    if m <= 25.0:
        return 100.0 - (m - 20.0) * (15.0 / 5.0)
    if m <= 30.0:
        return 85.0 - (m - 25.0) * (20.0 / 5.0)
    return max(20.0, 65.0 - (m - 30.0) * (45.0 / 30.0))


def calculate_sleep_report_structure_score(
    total_sleep_minutes, deep_percent, light_percent, rem_percent
):
    """
    睡眠报告综合分 0–100：仅由净睡眠时长（分钟）与深/浅/REM 占净睡比例四项
    子分算术平均（与 _rule_subscore_tst / deep / light / rem 规则一致）。
    """
    parts = [
        _rule_subscore_tst(float(total_sleep_minutes or 0)),
        _rule_subscore_deep(float(deep_percent or 0)),
        _rule_subscore_light(float(light_percent or 0)),
        _rule_subscore_rem(float(rem_percent or 0)),
    ]
    out = int(round(sum(parts) / len(parts)))
    return max(0, min(100, out))


def sleep_report_score_from_sleep_data(sleep_data):
    """从单条 sleep 记录 raw_data 提取净睡时长与三阶段占比，得到报告用综合分。"""
    rd = (sleep_data or {}).get("raw_data") or {}
    tst = max(0, int(rd.get("total_sleep_minutes") or 0))
    dp, lp, rp = _normalize_three_int100(
        int(rd.get("deep_sleep_ratio") or 0),
        int(rd.get("light_sleep_ratio") or 0),
        int(rd.get("rem_ratio") or 0),
    )
    return calculate_sleep_report_structure_score(tst, dp, lp, rp)


def calculate_rule_based_sleep_score(sleep_data, environment_rows=None):
    """
    规则化睡眠分 0–100：综合深睡/浅睡/REM 占比、净睡眠时长、入睡潜伏期、呼吸暂停、
    平均呼吸率、平均心率、睡眠效率及（可选）环境采样子分，取十项算术平均。
    清醒占比不参与分数计算。
    """
    rd = (sleep_data or {}).get("raw_data") or {}
    parts = [
        _rule_subscore_deep(rd.get("deep_sleep_ratio", 0)),
        _rule_subscore_light(rd.get("light_sleep_ratio", 0)),
        _rule_subscore_rem(rd.get("rem_ratio", 0)),
        _rule_subscore_tst(rd.get("total_sleep_minutes", 0)),
        _rule_subscore_latency(rd.get("sleep_latency", 0)),
        _rule_subscore_apnea(rd.get("apnea_count", 0)),
        _rule_subscore_respiration(rd.get("average_respiration", 0)),
        _rule_subscore_hr(rd.get("average_heartbeat", 0)),
        _rule_subscore_efficiency(rd.get("sleep_efficiency", 0)),
        _environment_comfort_subscore(environment_rows or []),
    ]
    out = int(round(sum(parts) / len(parts)))
    return max(0, min(100, out))


def recalculate_rule_based_sleep_scores_in_health(user_id, output_dir=None):
    """在已生成 environment 后，按规则分与环境数据写回 health_data 的 sleep_score。"""
    output_dir = output_dir or os.getenv("OUTPUT_DIR", "output")
    health_path = os.path.join(output_dir, f"{user_id}_health_data.json")
    if not os.path.isfile(health_path):
        return 0
    try:
        with open(health_path, "r", encoding="utf-8") as f:
            health = json.load(f)
    except Exception:
        return 0
    if not isinstance(health, list):
        return 0
    n = 0
    for rec in health:
        if not isinstance(rec, dict):
            continue
        rd = rec.get("record_date")
        uid = rec.get("user_id")
        if not rd or not uid:
            continue
        env = load_environment_rows_for_record_date(uid, rd, output_dir=output_dir)
        sc = calculate_rule_based_sleep_score(rec, environment_rows=env)
        rec.setdefault("raw_data", {})
        rec["raw_data"]["sleep_score"] = sc
        n += 1
    try:
        with open(health_path, "w", encoding="utf-8") as f:
            json.dump(health, f, ensure_ascii=False, indent=2)
    except Exception:
        return n
    return n


# new.md 八种人格：深睡/浅睡/REM 占「净睡眠」的比例范围（%）；清醒占「卧床 bed→wake_up」TIB 的比例范围（%）
# 与 docs/人格配置信息.md 一致
PERSONALITY_SLEEP_STAGE_RATIO_RANGES = {
    "M-H-R": {"deep": (13, 18), "light": (50, 57), "rem": (17, 22), "awake": (9, 15)},
    "M-H-C": {"deep": (16, 21), "light": (48, 55), "rem": (20, 24), "awake": (7, 13)},
    "M-L-R": {"deep": (18, 23), "light": (46, 52), "rem": (19, 23), "awake": (4, 9)},
    "M-L-C": {"deep": (21, 25), "light": (43, 49), "rem": (21, 25), "awake": (2, 5)},
    # E-H-R：仍为八种人格中偏弱的一档，但略放宽浅/REM/清醒上界，减轻与报告称号负向阈值的过度重合
    "E-H-R": {"deep": (11, 17), "light": (47, 55), "rem": (18, 22), "awake": (9, 14)},
    "E-H-C": {"deep": (15, 21), "light": (48, 56), "rem": (19, 23), "awake": (7, 13)},
    "E-L-R": {"deep": (17, 22), "light": (47, 53), "rem": (19, 23), "awake": (4, 10)},
    "E-L-C": {"deep": (20, 25), "light": (44, 50), "rem": (21, 25), "awake": (2, 7)},
}


def _sample_stage_ratio(cfg_lo, cfg_hi, p_lo, p_hi):
    lo = max(int(cfg_lo), int(p_lo))
    hi = min(int(cfg_hi), int(p_hi))
    if lo > hi:
        lo, hi = int(p_lo), int(p_hi)
    return random.randint(lo, hi)


def _intersect_cfg_pb(user, key, pb_lo, pb_hi):
    """用户 config 中 key 的区间与人格表 [pb_lo,pb_hi] 求交；交为空时退化为人格表区间。"""
    c = user.get(key) or {}
    try:
        u_lo = int((c.get("min") or [pb_lo])[0])
        u_hi = int((c.get("max") or [pb_hi])[0])
    except (TypeError, ValueError, IndexError):
        u_lo, u_hi = int(pb_lo), int(pb_hi)
    if u_lo > u_hi:
        u_lo, u_hi = u_hi, u_lo
    lo = max(u_lo, int(pb_lo))
    hi = min(u_hi, int(pb_hi))
    if lo > hi:
        return int(pb_lo), int(pb_hi)
    return lo, hi


def _sample_four_spt_percents_bounded(
    a_lo, a_hi, d_lo, d_hi, l_lo, l_hi, r_lo, r_hi, min_awake=1, max_draws=3000
):
    """
    在闭区间内随机 (aw,d,l,r) 整数百分比，满足 aw+d+l+r=100、aw>=min_awake。
    各段均在给定上下界内；若整体不可行返回 None。
    """
    a_lo = max(int(min_awake), int(a_lo))
    a_hi = max(a_lo, int(a_hi))
    d_lo, d_hi = int(d_lo), int(d_hi)
    l_lo, l_hi = int(l_lo), int(l_hi)
    r_lo, r_hi = int(r_lo), int(r_hi)
    aw_min_fe = max(a_lo, 100 - d_hi - l_hi - r_hi)
    aw_max_fe = min(a_hi, 100 - d_lo - l_lo - r_lo)
    if aw_min_fe > aw_max_fe:
        return None
    for _ in range(max_draws):
        aw = random.randint(aw_min_fe, aw_max_fe)
        r_sum = 100 - aw
        d_min = max(d_lo, r_sum - l_hi - r_hi)
        d_max = min(d_hi, r_sum - l_lo - r_lo)
        if d_min > d_max:
            continue
        d = random.randint(d_min, d_max)
        r2 = r_sum - d
        l_min = max(l_lo, r2 - r_hi)
        l_max = min(l_hi, r2 - r_lo)
        if l_min > l_max:
            continue
        l = random.randint(l_min, l_max)
        r = r2 - l
        if r_lo <= r <= r_hi:
            return aw, d, l, r
    return None


# 睡眠质量较好的人格（与 PERSONALITY_SLEEP_STAGE_RATIO_RANGES 八种人格互补为差睡人格）
GOOD_SLEEP_PERSONALITIES = frozenset({"M-L-C", "E-L-C", "M-L-R", "M-H-C", "E-H-C"})
# 睡眠质量相对较差的人格
POOR_SLEEP_PERSONALITIES = frozenset({"M-H-R", "E-H-R", "E-L-R"})
# 高敏感(H) + 高活跃(R)：在离群排期中单独提高「好睡日」占比，且好睡日互不连续
HIGH_SENS_HIGH_ACTIVE_PERSONALITIES = frozenset({"M-H-R", "E-H-R"})
# 低敏感(L) + 低活跃(C)：基线睡眠好，不注入好睡日排期，仅混入较高比例坏睡日
LOW_SENS_LOW_ACTIVE_PERSONALITIES = frozenset({"M-L-C", "E-L-C"})
# 低敏感+高活跃(L-R) 或 高敏感+低活跃(H-C)：混入约 20% 坏睡日，不注入好睡日排期
LOW_SENS_HIGH_OR_HIGH_SENS_LOW_PERSONALITIES = frozenset(
    {"M-L-R", "E-L-R", "M-H-C", "E-H-C"}
)


def _sleep_calendar_bad_day_fraction(personality_type):
    """离群「坏睡日」占日历总天数的比例（按敏感×活跃分档）。高敏感+高活跃为 0（只排好日）。"""
    pt = personality_type or "M-L-C"
    if pt in HIGH_SENS_HIGH_ACTIVE_PERSONALITIES:
        return 0.0
    if pt in LOW_SENS_LOW_ACTIVE_PERSONALITIES:
        return 0.30
    if pt in LOW_SENS_HIGH_OR_HIGH_SENS_LOW_PERSONALITIES:
        return 0.20
    return 0.20


def _max_non_adjacent_good_slots_on_calendar(eligible_sorted_indices):
    """
    在连续自然日上，eligible 下标集合中最多能选多少个「好睡日」且两两不相邻。
    每个最长连续 eligible 段长度为 L 时贡献 ceil(L/2)。
    """
    if not eligible_sorted_indices:
        return 0
    total = 0
    run_start = prev = eligible_sorted_indices[0]
    for x in eligible_sorted_indices[1:]:
        if x == prev + 1:
            prev = x
        else:
            total += (prev - run_start + 2) // 2
            run_start = prev = x
    total += (prev - run_start + 2) // 2
    return total


def _pick_non_adjacent_indices_from_pool(pool, k_target, max_attempts=320):
    """
    从 pool（日期下标列表）中选取至多 k_target 个，任意两个下标不相邻（自然日）。
    多次随机贪心，尽量达到 k_target；若结构上界不足则返回能取到的最大规模之一。
    """
    if k_target <= 0 or not pool:
        return []
    best = []
    for _ in range(max_attempts):
        order = list(pool)
        random.shuffle(order)
        picked = []
        picked_set = set()
        for idx in order:
            if len(picked) >= k_target:
                break
            if (idx - 1) in picked_set or (idx + 1) in picked_set:
                continue
            picked.append(idx)
            picked_set.add(idx)
        if len(picked) > len(best):
            best = picked
        if len(best) >= k_target:
            break
    return best[:k_target]


def pick_non_consecutive_day_indices(n_days, k):
    """
    在 [0, n_days-1] 中无放回选取 k 个下标，且任意两天下标不相邻（用于「差睡日」不连续）。
    可行上界为 ceil(n_days/2)；若 k 过大会先截断。
    """
    if k <= 0 or n_days <= 0:
        return []
    max_k = (n_days + 1) // 2
    k = min(k, max_k)
    if k <= 0:
        return []
    choice = sorted(random.sample(range(n_days - k + 1), k))
    return [choice[i] + i for i in range(k)]


def build_sleep_outlier_mode_by_date(start_date, end_date, personality_type):
    """
    按人格分档预先排期睡眠离群日，返回 { 'YYYY-MM-DD': None | 'bad' | 'good' }。
    规则：每人格只混入「好」或「坏」一类离群日，不同时排 bad 与 good。

    - 高敏感+高活跃（M-H-R / E-H-R）：不排 bad；约 30% 天为 good（互不相邻）。
    - 低敏感+低活跃（M-L-C / E-L-C）：约 30% 天为 bad（互不相邻）；不排 good。
    - 低敏感+高活跃 或 高敏感+低活跃（M-L-R、E-L-R、M-H-C、E-H-C）：约 20% 天为 bad；
      不排 good。
    - 未知人格编码：约 20% bad，不排 good。
    """
    if start_date is None or end_date is None or end_date < start_date:
        return {}
    n_days = (end_date - start_date).days + 1
    dates = [start_date + timedelta(days=i) for i in range(n_days)]
    result = {d.strftime("%Y-%m-%d"): None for d in dates}

    bad_frac = _sleep_calendar_bad_day_fraction(personality_type)
    k_bad = max(0, int(round(n_days * bad_frac)))
    if k_bad > 0:
        for i in pick_non_consecutive_day_indices(n_days, k_bad):
            result[dates[i].strftime("%Y-%m-%d")] = "bad"

    # 仅高敏感+高活跃：无 bad 日，在全部日历日上取约 30% good，且好睡日不相邻
    if personality_type in HIGH_SENS_HIGH_ACTIVE_PERSONALITIES:
        non_bad_indices = list(range(n_days))
        k_good = max(0, int(round(n_days * 0.30)))
        cap = _max_non_adjacent_good_slots_on_calendar(non_bad_indices)
        k_good = min(k_good, cap)
        if k_good > 0 and non_bad_indices:
            random.shuffle(non_bad_indices)
            chosen = _pick_non_adjacent_indices_from_pool(non_bad_indices, k_good)
            for i in chosen:
                result[dates[i].strftime("%Y-%m-%d")] = "good"

    return result


def strip_sleep_calendar_flags_from_health_records(health_data_list):
    """落盘前从 raw_data 移除好睡日/差睡日标记（仅生成过程使用，不写入最终 JSON）。"""
    for row in health_data_list or []:
        rd = row.get("raw_data")
        if isinstance(rd, dict):
            rd.pop("good_sleep_day", None)
            rd.pop("bad_sleep_day", None)


def apply_sleep_outlier_mode(
    personality_type,
    deep_ratio,
    light_ratio,
    rem_ratio,
    sleep_latency,
    apnea_count,
    leave_bed_count,
    leave_bed_minutes,
    sleep_outlier_mode,
    mutate_stage_ratios=True,
):
    """
    按排期注入离群夜指标（替代原先按天随机 maybe_inject）：
    - bad：所有人格均适用 — 深睡/REM 下降、浅睡上升、入睡更慢、呼吸暂停（至少 5 次）与离床增多；
      好睡人格下降幅度较大，差睡人格下降幅度较小（本已较差，降幅受限）。
    - good：差睡人格 — 与 bad 大致相反的温和改善，仍受各字段合理上下限约束。

    mutate_stage_ratios=False 时仅调整潜伏期/呼吸暂停/离床，不改 d/l/r 占比（供 TIB 四段联合抽样路径使用）。
    """
    if sleep_outlier_mode == "bad":
        if mutate_stage_ratios:
            if personality_type in GOOD_SLEEP_PERSONALITIES:
                deep_drop = random.randint(3, 8)
                rem_drop = random.randint(2, 6)
            else:
                # 差睡人格本已偏低，bad 日降幅较小，避免产生生理不合理数值
                deep_drop = random.randint(1, 4)
                rem_drop = random.randint(1, 3)
            deep_ratio = max(5, deep_ratio - deep_drop)
            rem_ratio = max(6, rem_ratio - rem_drop)
            light_ratio = min(86, light_ratio + deep_drop + rem_drop)
            total = deep_ratio + light_ratio + rem_ratio
            if total != 100:
                light_ratio += 100 - total
            light_ratio = max(0, min(100, light_ratio))
        sleep_latency = int(sleep_latency + random.randint(6, 20))
        apnea_count = int(apnea_count + random.randint(1, 4))
        apnea_count = max(5, apnea_count)
        leave_bed_count = int(leave_bed_count + random.randint(1, 2))
        leave_bed_minutes = int(leave_bed_minutes + random.randint(6, 20))
        return (
            deep_ratio,
            light_ratio,
            rem_ratio,
            sleep_latency,
            apnea_count,
            leave_bed_count,
            leave_bed_minutes,
            True,
        )

    if sleep_outlier_mode == "good" and personality_type in POOR_SLEEP_PERSONALITIES:
        if mutate_stage_ratios:
            deep_gain = random.randint(3, 8)
            rem_gain = random.randint(2, 6)
            deep_ratio = min(30, deep_ratio + deep_gain)
            rem_ratio = min(30, rem_ratio + rem_gain)
            light_ratio = max(38, light_ratio - deep_gain - rem_gain)
            total = deep_ratio + light_ratio + rem_ratio
            if total != 100:
                light_ratio += 100 - total
            light_ratio = max(0, min(100, light_ratio))
        sleep_latency = max(2, int(sleep_latency - random.randint(5, 18)))
        apnea_count = max(0, int(apnea_count - random.randint(1, 4)))
        leave_bed_count = max(0, int(leave_bed_count - random.randint(0, 2)))
        leave_bed_minutes = max(0, int(leave_bed_minutes - random.randint(5, 25)))
        return (
            deep_ratio,
            light_ratio,
            rem_ratio,
            sleep_latency,
            apnea_count,
            leave_bed_count,
            leave_bed_minutes,
            True,
        )

    return (
        deep_ratio,
        light_ratio,
        rem_ratio,
        sleep_latency,
        apnea_count,
        leave_bed_count,
        leave_bed_minutes,
        False,
    )


def _deduct_one_from_dlr_closest_to_lower_bound(d, l, r, d_lo, l_lo, r_lo):
    """awake 占比 +1 时，从深/浅/REM 中扣 1 个百分点：选 (值-下限) 最小者。"""
    candidates = [
        ("d", int(d), int(d_lo)),
        ("l", int(l), int(l_lo)),
        ("r", int(r), int(r_lo)),
    ]
    best = None
    best_key = None
    for key, val, lo in candidates:
        if val <= lo:
            continue
        margin = val - lo
        if best is None or margin < best:
            best = margin
            best_key = key
    if best_key is None:
        return d, l, r, False
    d, l, r = int(d), int(l), int(r)
    if best_key == "d":
        d -= 1
    elif best_key == "l":
        l -= 1
    else:
        r -= 1
    return d, l, r, True


def _tib_awake_minutes_from_percent(tib, aw_pct):
    tib = max(1, int(tib))
    aw_pct = int(max(0, min(100, aw_pct)))
    return int(round(tib * aw_pct / 100.0))


def _add_one_to_dlr_closest_to_upper_bound(d, l, r, d_hi, l_hi, r_hi):
    """压缩 awake 占比时，向深/浅/REM 回补 1 个百分点：选 (上限-值) 最大者。"""
    picks = []
    for key, val, hi in (("d", int(d), int(d_hi)), ("l", int(l), int(l_hi)), ("r", int(r), int(r_hi))):
        if val < hi:
            picks.append((hi - val, key))
    if not picks:
        return int(d), int(l), int(r), False
    picks.sort(reverse=True)
    _, key = picks[0]
    d, l, r = int(d), int(l), int(r)
    if key == "d":
        d += 1
    elif key == "l":
        l += 1
    else:
        r += 1
    return d, l, r, True


def _finalize_tib_stage_minutes_for_idf(
    tib,
    sleep_latency,
    post_wake_minutes,
    aw_rt,
    d_rt,
    l_rt,
    r_rt,
    a_lo,
    a_hi,
    d_lo,
    d_hi,
    l_lo,
    l_hi,
    r_lo,
    r_hi,
    awake_count,
    night_waso_cfg_lo=None,
    night_waso_cfg_hi=None,
):
    """
    按「卧床 bed→wake_up」TIB 与四段整数占比（和为 100）生成分钟数，并推导 idf 中段夜间清醒总分钟。

    - 四段占比均为人格表 ∩ 用户 config 后的闭区间抽样（awake 用 awakeSleep ∩ 人格 awake）。
    - 清醒分钟 = round(TIB * awake% / 100)；TST = TIB - 清醒；深/浅/REM 分钟在 TST 内按三占比分配。
    - 入睡潜伏期 + 觉后清醒（wake→wake_up）为「边缘清醒」；剩余清醒预算为夜间中段清醒分钟。
    - 无夜间醒来次数：不在 idf 中插入中段清醒，总清醒分钟压到边缘清醒之和（在 awake 占比允许范围内微调）。
    - 有夜间醒来次数：中段清醒段数 = 次数（不含首段潜伏期清醒与末段觉后清醒）；夜间清醒分钟需 >= max(3, 次数)
      且不足时 awake 每次 +1 并从最接近下限的深/浅/REM 占比扣 1。
    - night_waso_cfg_lo/hi：用户配置「夜间清醒总分钟」（入睡后中段），若给出且 na>0，则在 [lo,hi] 内随机目标并对齐
      总清醒分钟（在 a_lo..a_hi 与 TIB 约束下取最接近的可行 awake%）。
    """
    aw = int(aw_rt)
    d, l, r = int(d_rt), int(l_rt), int(r_rt)
    a_lo, a_hi = int(a_lo), int(a_hi)
    edge = int(sleep_latency) + int(max(0, post_wake_minutes))
    na = max(0, int(awake_count or 0))
    tib = max(1, int(tib))

    def _recompute_minutes():
        m_aw = _tib_awake_minutes_from_percent(tib, aw)
        tst = max(0, tib - m_aw)
        dp, lp, rp = _normalize_three_int100(d, l, r)
        m_d, m_l, m_r = distribute_sleep_stage_minutes(tst, dp, lp, rp)
        return int(m_aw), int(m_d), int(m_l), int(m_r)

    m_aw, m_d, m_l, m_r = _recompute_minutes()

    while m_aw < int(sleep_latency) and aw < a_hi:
        aw += 1
        d, l, r, ok = _deduct_one_from_dlr_closest_to_lower_bound(d, l, r, d_lo, l_lo, r_lo)
        if not ok:
            break
        m_aw, m_d, m_l, m_r = _recompute_minutes()

    rem_night = int(m_aw) - edge
    need_rem = max(3, na) if na > 0 else 0

    def _bump_awake_one():
        nonlocal aw, d, l, r
        if aw >= a_hi:
            return False
        aw += 1
        d, l, r, ok = _deduct_one_from_dlr_closest_to_lower_bound(d, l, r, d_lo, l_lo, r_lo)
        return ok

    if na == 0:
        # 无中段清醒：清醒分钟严格等于「潜伏期 + 觉后清醒」；TST 内仍按深/浅/REM 占比分配
        night_waso = 0
        m_aw = int(max(0, min(int(edge), int(tib))))
        tst = max(0, int(tib) - int(m_aw))
        dp, lp, rp = _normalize_three_int100(d, l, r)
        m_d, m_l, m_r = distribute_sleep_stage_minutes(tst, dp, lp, rp)
        aw_rt = int(round(100.0 * float(m_aw) / float(tib))) if tib > 0 else 0
        aw_rt = max(a_lo, min(a_hi, aw_rt))
        tst_sum = int(m_d + m_l + m_r)
        if tst_sum > 0:
            d_rt, l_rt, r_rt = _normalize_three_int100(
                int(round(100.0 * float(m_d) / float(tst_sum))),
                int(round(100.0 * float(m_l) / float(tst_sum))),
                int(round(100.0 * float(m_r) / float(tst_sum))),
            )
        else:
            d_rt, l_rt, r_rt = dp, lp, rp
        return m_aw, m_d, m_l, m_r, night_waso, aw_rt, d_rt, l_rt, r_rt

    while rem_night < need_rem and aw < a_hi:
        if not _bump_awake_one():
            break
        m_aw, m_d, m_l, m_r = _recompute_minutes()
        rem_night = int(m_aw) - edge

    while rem_night == 0 and na > 0 and aw < a_hi:
        if not _bump_awake_one():
            break
        m_aw, m_d, m_l, m_r = _recompute_minutes()
        rem_night = int(m_aw) - edge

    while 0 < rem_night < need_rem and aw < a_hi:
        if not _bump_awake_one():
            break
        m_aw, m_d, m_l, m_r = _recompute_minutes()
        rem_night = int(m_aw) - edge

    night_waso = rem_night if (na > 0 and rem_night >= need_rem) else 0
    aw_rt, d_rt, l_rt, r_rt = aw, d, l, r

    if (
        na > 0
        and night_waso_cfg_lo is not None
        and night_waso_cfg_hi is not None
        and int(night_waso_cfg_lo) <= int(night_waso_cfg_hi)
    ):
        w_lo = max(int(need_rem), int(night_waso_cfg_lo))
        w_hi = int(max(w_lo, int(night_waso_cfg_hi)))
        max_mid = max(int(need_rem), int(tib) - int(edge) - 1)
        # 卧床窗内最多只能容纳 max_mid 分钟的中段清醒；配置区间与可行上界取交
        w_hi_eff = min(w_hi, max_mid)
        w_lo_eff = min(w_lo, w_hi_eff) if max_mid < w_lo else w_lo
        w_lo_eff = max(int(need_rem), int(w_lo_eff))
        w_hi_eff = max(w_lo_eff, int(w_hi_eff))
        tgt = (
            random.randint(int(w_lo_eff), int(w_hi_eff))
            if w_lo_eff <= w_hi_eff
            else int(w_hi_eff)
        )
        desired_m_aw = int(edge) + int(tgt)
        desired_m_aw = min(desired_m_aw, int(tib) - 1)
        desired_m_aw = max(desired_m_aw, int(edge) + int(need_rem))
        # 为满足「入睡后清醒总分钟」配置，所需总清醒占比可能高于 awakeSleep∩人格 的上限；
        # 此处临时放宽搜索上界（仍不超过 97），否则 idf 中段清醒永远达不到配置区间。
        min_aw_pct = int(
            math.ceil(100.0 * float(desired_m_aw) / float(max(1, int(tib))))
        )
        eff_a_hi = min(97, max(int(a_hi), int(min_aw_pct)))
        best_aw = int(aw)
        best_score = abs(int(m_aw) - int(desired_m_aw))
        for cand_aw in range(int(a_lo), int(eff_a_hi) + 1):
            cand_m = int(_tib_awake_minutes_from_percent(int(tib), int(cand_aw)))
            if cand_m - int(edge) < int(need_rem):
                continue
            sc = abs(int(cand_m) - int(desired_m_aw))
            if sc < best_score:
                best_score = sc
                best_aw = int(cand_aw)
        aw = int(best_aw)
        m_aw = int(_tib_awake_minutes_from_percent(int(tib), aw))
        tst = max(0, int(tib) - int(m_aw))
        dp, lp, rp = _normalize_three_int100(int(d), int(l), int(r))
        m_d, m_l, m_r = distribute_sleep_stage_minutes(tst, dp, lp, rp)
        rem_night = int(m_aw) - int(edge)
        night_waso = rem_night if rem_night >= int(need_rem) else 0
        tst_sum = max(1, int(m_d + m_l + m_r))
        d_rt, l_rt, r_rt = _normalize_three_int100(
            int(round(100.0 * float(m_d) / float(tst_sum))),
            int(round(100.0 * float(m_l) / float(tst_sum))),
            int(round(100.0 * float(m_r) / float(tst_sum))),
        )
        aw_rt = max(int(a_lo), min(int(eff_a_hi), int(aw)))

    return m_aw, m_d, m_l, m_r, night_waso, aw_rt, d_rt, l_rt, r_rt


# 将UTC时间转换为本地时间（北京时区，UTC+8）
def utc_to_local(utc_time_str):
    """将 UTC ISO 字符串转为本地 naive datetime；若为纯 HH:MM(:SS) 则视为已是本地墙钟。"""
    if not utc_time_str or not isinstance(utc_time_str, str):
        return datetime.now()
    s = utc_time_str.strip()
    if not s:
        return datetime.now()
    if _SLEEP_CLOCK_HHMM_RE.match(s):
        base_d = datetime(2000, 1, 1).date()
        if len(s) <= 5:
            tm = datetime.strptime(s, "%H:%M").time()
        else:
            tm = datetime.strptime(s, "%H:%M:%S").time()
        return datetime.combine(base_d, tm)
    utc_time = datetime.fromisoformat(s.replace("Z", ""))
    return utc_time + timedelta(hours=8)

# 格式化时间为HH:MM格式
def format_time_to_hhmm(dt):
    """将datetime对象格式化为HH:MM格式"""
    return dt.strftime('%H:%M')


def probability_zero_night_awakenings(profile):
    """
    按人格睡眠质量给出「本晚夜间清醒次数为 0」的抽样概率。
    睡眠结构较好的人格概率高，较差的人格概率低但仍偶有整晚无中段清醒。
    """
    prof = profile or {}
    sp = prof.get("stage_pattern")
    if sp == "optimal":
        return 0.52
    if sp == "healthy_active":
        return 0.38
    if sp == "sensitive_calm":
        return 0.22
    if sp == "sensitive_active":
        return 0.14
    if sp == "poor_quality":
        return 0.06
    se = prof.get("sleep_efficiency") or {"min": 85, "max": 93}
    try:
        mid = 0.5 * (float(se["min"]) + float(se["max"]))
    except (TypeError, KeyError, ValueError):
        mid = 88.0
    return max(0.05, min(0.48, (mid - 73.0) / 25.0))


def night_wake_episodes_for_prompts(sleep_data):
    """从 idf_data 统计夜间清醒段数：去掉首段入睡潜伏期清醒，去掉与 wake_time 对齐的觉后清醒段。"""
    raw = sleep_data.get("raw_data", {})
    idf = sleep_data.get("idf_data") or []
    awake_segs = [s for s in idf if s.get("stage") == "awake"]
    if len(awake_segs) <= 1:
        return 0
    mid = awake_segs[1:]
    wk = raw.get("wake_time") or ""
    if wk:
        wk_hm = format_time_to_hhmm(utc_to_local(wk))
        if mid and mid[-1].get("start") == wk_hm:
            mid = mid[:-1]
    return len(mid)


def night_wake_minutes_for_prompts(sleep_data):
    """从 idf_data 统计夜间中段清醒总分钟数（不含入睡前与起床后清醒段）。"""
    raw = sleep_data.get("raw_data", {})
    idf = sleep_data.get("idf_data") or []
    awake_segs = [s for s in idf if s.get("stage") == "awake"]
    if len(awake_segs) <= 1:
        return 0

    mid = awake_segs[1:]
    wk = raw.get("wake_time") or ""
    if wk:
        wk_hm = format_time_to_hhmm(utc_to_local(wk))
        if mid and mid[-1].get("start") == wk_hm:
            mid = mid[:-1]

    anchor = sleep_data.get("record_date") or raw.get("record_date")
    total = 0
    for seg in mid:
        start = seg.get("start")
        end = seg.get("end")
        if not start or not end:
            continue
        total += calculate_duration(start, end, anchor)
    return total


def _variant_pick(record_date, key, options):
    """同一指标多文案轮换，按日期稳定选取，避免条条报告雷同。"""
    if not options:
        return None
    seed = f"{record_date or ''}|{key}"
    h = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16)
    return options[h % len(options)]


def build_auditory_snore_module(audios, record_date):
    """
    根据 audios 中 type 为 Snore 的条数生成 auditory.module（单条分析，中文）。
    标题与正文均按日期轮换，不涉及大模型。
    """
    snore_n = sum(1 for a in (audios or []) if (a.get("type") or "") == "Snore")
    key_base = f"snore_mod|{snore_n}"
    if snore_n <= 0:
        opts = [
            (
                "鼾声监测概览",
                "本晚未检出明显打鼾音频片段，上气道通畅度相对较好。若偶有轻微鼾声属常见现象，可继续观察。",
            ),
            (
                "上气道振动信号",
                "整夜鼾声信号较弱，未形成可复核的打鼾片段。建议保持侧卧与规律作息，便于长期对比。",
            ),
            (
                "夜间声学记录",
                "未记录到显著打鼾录音；若家属反馈有鼾声，可能与麦克风位置或环境噪声有关，可结合多日数据一起看。",
            ),
        ]
    elif snore_n == 1:
        opts = [
            (
                "打鼾片段检出",
                f"检出 1 段打鼾相关录音，提示睡眠中存在可识别的鼾声事件。建议控制睡前饮酒、避免仰卧，并关注是否伴随日间困倦。",
            ),
            (
                "单次鼾声事件",
                f"当晚捕捉到 1 次打鼾片段，强度与持续时间需结合多日趋势判断。可尝试抬高床头、减重与规律运动以减轻振动。",
            ),
        ]
    elif snore_n == 2:
        opts = [
            (
                "鼾声活动小结",
                f"共检出 2 段打鼾录音，夜间上气道可能存在间歇性狭窄。建议留意鼻塞、过敏与睡姿，并观察是否影响深睡连续性。",
            ),
            (
                "双段打鼾记录",
                f"记录到 2 次打鼾事件，提示睡眠中振动声较明显。若合并呼吸暂停风险指标升高，建议就医做进一步评估。",
            ),
        ]
    else:
        opts = [
            (
                "频繁鼾声提示",
                f"共检出 {snore_n} 段打鼾录音，夜间鼾声活动较频繁。建议优先排查鼻塞与仰卧习惯，并关注是否伴有呼吸节律异常。",
            ),
            (
                "多段打鼾分析",
                f"当晚打鼾片段达 {snore_n} 次，提示上气道阻力可能偏高。可结合身体电量与日间嗜睡情况，必要时咨询睡眠专科。",
            ),
            (
                "鼾声密度观察",
                f"打鼾录音较多（{snore_n} 段），睡眠中气道稳定性值得关注。建议保持侧卧、控制体重，并持续对比后续夜晚是否改善。",
            ),
        ]
    pair = _variant_pick(record_date, key_base, opts)
    target, desc = pair
    return [{"target": target, "description": desc}]


def _compact_sleep_events_for_auditory_prompt(events, max_n=80):
    out = []
    for e in (events or [])[: max(0, int(max_n or 0))]:
        if not isinstance(e, dict):
            continue
        d = e.get("detail") if isinstance(e.get("detail"), dict) else {}
        out.append(
            {
                "event_timestamp": e.get("event_timestamp"),
                "event_type": e.get("event_type"),
                "type": e.get("type"),
                "code": e.get("code"),
                "duration_sec": e.get("duration_sec"),
                "trigger_cause": d.get("trigger_cause"),
                "action_taken": d.get("action_taken"),
                "result_summary": d.get("result_summary"),
            }
        )
    return out


def _environment_samples_for_auditory_prompt(user_id, record_date, max_n=120, output_dir="output"):
    path = os.path.join(output_dir, f"{user_id}_environment_data.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    rows = [x for x in data if isinstance(x, dict) and x.get("record_date") == record_date]
    rows.sort(key=lambda x: str(x.get("collected_at") or ""))
    cap = max(1, min(int(max_n or 120), 500))
    slim = []
    for x in rows[:cap]:
        slim.append(
            {
                "collected_at": x.get("collected_at"),
                "temperature": x.get("temperature"),
                "humidity": x.get("humidity"),
                "illuminance": x.get("illuminance"),
                "noise": x.get("noise"),
            }
        )
    return slim


def _sleep_metrics_for_auditory_prompt(sleep_data):
    raw = sleep_data.get("raw_data") or {}
    return {
        "record_date": sleep_data.get("record_date"),
        "apnea_count": raw.get("apnea_count"),
        "average_heartbeat": raw.get("average_heartbeat"),
        "average_respiration": raw.get("average_respiration"),
        "awake_ratio": raw.get("awake_ratio"),
        "deep_sleep_ratio": raw.get("deep_sleep_ratio"),
        "light_sleep_ratio": raw.get("light_sleep_ratio"),
        "rem_ratio": raw.get("rem_ratio"),
        "sleep_score": raw.get("sleep_score"),
        "total_sleep_minutes": raw.get("total_sleep_minutes"),
        "sleep_time": raw.get("sleep_time"),
        "wake_time": raw.get("wake_time"),
        "sleep_latency": raw.get("sleep_latency"),
        "sleep_efficiency": raw.get("sleep_efficiency"),
    }


def sleep_data_shallow_from_report_for_auditory(report):
    """从已落盘的 sleep_report 条目还原听觉分析所需的 raw_data 形状（无原始 health 文件时）。"""
    if not isinstance(report, dict):
        return {"record_date": "", "raw_data": {}}
    ss = report.get("sleep_summary") or {}
    sq = report.get("quality_analysis", {}).get("sleep_quality") or {}
    st = report.get("quality_analysis", {}).get("sleep_structure") or {}

    def _health_ratio_from_structure(block_key, net_key="percent_of_net_sleep"):
        blk = st.get(block_key) or {}
        if block_key == "awake":
            return blk.get("percent_of_time_in_bed", blk.get("percent"))
        return blk.get(net_key, blk.get("percent"))

    raw_like = {
        "apnea_count": int(report.get("apnea_count", 0) or 0),
        "average_heartbeat": ss.get("avg_heart_rate"),
        "average_respiration": ss.get("avg_respiratory_rate"),
        "awake_ratio": _health_ratio_from_structure("awake"),
        "deep_sleep_ratio": _health_ratio_from_structure("deep_sleep"),
        "light_sleep_ratio": _health_ratio_from_structure("light_sleep"),
        "rem_ratio": _health_ratio_from_structure("rem_sleep"),
        "sleep_score": None,
        "total_sleep_minutes": ss.get("total_minutes"),
        "sleep_time": None,
        "wake_time": None,
        "sleep_latency": sq.get("sleep_onset_latency_minutes"),
        "sleep_efficiency": sq.get("sleep_efficiency"),
    }
    return {"record_date": report.get("record_date", ""), "raw_data": raw_like}


def _parse_model_json_array(text):
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    if t.startswith("```"):
        lines = t.splitlines()
        if len(lines) >= 2:
            inner = "\n".join(lines[1:])
            if inner.rstrip().endswith("```"):
                inner = inner.rstrip()[:-3].rstrip()
            t = inner.strip()
            if t.lower().startswith("json"):
                t = t[4:].lstrip().strip()
    try:
        parsed = json.loads(t)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    lb = t.find("[")
    rb = t.rfind("]")
    if lb != -1 and rb != -1 and rb > lb:
        try:
            parsed = json.loads(t[lb : rb + 1])
            if isinstance(parsed, list):
                return parsed
        except Exception:
            return None
    return None


def _normalize_auditory_module_list(items):
    if not isinstance(items, list):
        return None
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        tgt = str(it.get("target") or "").strip()
        desc = str(it.get("description") or "").strip()
        if tgt and desc:
            out.append({"target": tgt, "description": desc})
    return out


def _snoring_data_points_for_auditory_prompt(auditory_dict):
    """为听觉提示词合并鼾声分贝点与对应 audio 的持续时长。"""
    aud = auditory_dict if isinstance(auditory_dict, dict) else {}
    duration_by_time = {}
    for audio in aud.get("audios") or []:
        if not isinstance(audio, dict):
            continue
        time_key = audio.get("time")
        if (audio.get("type") or "") == "Snore" and time_key:
            duration_by_time[str(time_key)] = audio.get("duration_sec")

    out = []
    snoring = aud.get("snoring_analysis") or {}
    for point in snoring.get("data_points") or []:
        if not isinstance(point, dict):
            continue
        time_value = point.get("time")
        item = {
            "time": time_value,
            "value": point.get("value"),
            "duration_sec": duration_by_time.get(str(time_value)),
        }
        out.append(item)
    return out


def generate_auditory_module_via_qwen(
    user_id,
    record_date,
    sleep_data,
    sleep_events_for_date,
    auditory_dict,
    output_dir="output",
    *,
    temperature=None,
    top_p=None,
):
    """
    读取 prompt/sleep_audio_analysis.md，结合睡眠事件、睡眠指标、环境与听觉 audios，
    调用大模型生成 quality_analysis.auditory.module（JSON 数组，每项含 target、description）。
    失败返回 None，由调用方回退到 build_auditory_snore_module。
    """
    if not sleepReportAI:
        return None
    instruction = load_prompt_instruction("sleep_audio_analysis.md")
    if not instruction:
        print("  [警告] 读取睡眠听觉分析模板失败或为空: sleep_audio_analysis.md")
        return None
    if not _qwen_api_key():
        return None

    aud = auditory_dict if isinstance(auditory_dict, dict) else {}
    sleep_metrics = _sleep_metrics_for_auditory_prompt(sleep_data)
    payload = {
        "user_id": user_id,
        "record_date": record_date,
        "sleep_events": _compact_sleep_events_for_auditory_prompt(sleep_events_for_date),
        "sleep_metrics": sleep_metrics,
        "apnea_count": sleep_metrics.get("apnea_count"),
        "data_points": _snoring_data_points_for_auditory_prompt(aud),
        "environment_samples": _environment_samples_for_auditory_prompt(
            user_id, record_date, output_dir=output_dir
        ),
        "auditory_audios": [
            {
                "type": a.get("type"),
                "time": a.get("time"),
                "duration_sec": a.get("duration_sec"),
            }
            for a in (aud.get("audios") or [])
            if isinstance(a, dict)
        ],
    }
    prompt = (
        "以下为本晚真实输入数据（JSON）。请仅依据这些数据进行分析，"
        + "返回仅包含 1 条元素的 JSON 数组（字段仅限 `target`、`description`），不要附加任何解释。\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    eff_temperature = 0.35 if temperature is None else temperature
    result = call_qwen_api(
        prompt,
        system_prompt=instruction,
        max_tokens=2048,
        temperature=eff_temperature,
        top_p=top_p,
        sleep_report_llm=True,
    )
    if not result:
        return None
    arr = _parse_model_json_array(result)
    normalized = _normalize_auditory_module_list(arr)
    if normalized is None:
        return None
    if not normalized:
        return None
    return normalized[:1]


def build_apnea_auditory_target_title(record_date):
    """呼吸暂停 ≥5 次时 auditory.target 的标题轮换（与 risk_alert 配套）。"""
    return _variant_pick(
        record_date or "",
        "apnea_alert_title",
        [
            "呼吸健康风险提示",
            "夜间呼吸节律关注",
            "睡眠呼吸风险提醒",
            "呼吸相关健康提示",
        ],
    )


def build_sleep_events_index(user_id, output_dir="output"):
    """按 record_date 索引该用户 sleep_events，减少重复 IO。"""
    idx = {}
    sleep_events_file = os.path.join(output_dir, f"{user_id}_sleep_events.json")
    if not os.path.exists(sleep_events_file):
        return idx
    try:
        with open(sleep_events_file, 'r', encoding='utf-8') as f:
            all_events = json.load(f)
        for e in all_events:
            d = e.get('record_date')
            if not d:
                continue
            idx.setdefault(d, []).append(e)
    except Exception:
        return {}
    return idx

# 调用 OpenAI 兼容 chat/completions：默认通义千问（DashScope）；LLM_VENDOR=doubao 时为火山方舟豆包（.env：BASE_URL、DOUBAO_API_KEY、MODEL_NAME 等）
def call_qwen_api(
    user_prompt,
    system_prompt=None,
    max_tokens=None,
    temperature=None,
    *,
    top_p=None,
    enable_thinking=False,
    sleep_report_llm=False,
):
    """调用大模型（OpenAI 兼容）：默认通义千问；设置 LLM_VENDOR=doubao 时走豆包（方舟）。

    temperature 为 None 时使用 QWEN_TEMPERATURE 或 DOUBAO_TEMPERATURE（默认 0.7）。

    top_p 为 None 时不写入请求体（由服务端默认）；否则写入 0–1 区间内的值。
    max_tokens 默认与上限均为 _QWEN_MAX_TOKENS（10000）。
    enable_thinking 控制是否开启深度思考，默认 False。
    sleep_report_llm 为 True 时仅检查 sleepReportAI（睡眠报告专用），忽略 USE_MODEL。
    """
    if sleep_report_llm:
        if not sleepReportAI:
            return ""
    elif not USE_MODEL:
        return ""
    api_key = _qwen_api_key()
    if not api_key:
        # 如果没有API密钥，返回空字符串
        return ""

    if temperature is None:
        temperature = float(os.getenv("QWEN_TEMPERATURE", os.getenv("DOUBAO_TEMPERATURE", "0.7")))
    temperature = max(0.0, min(1.0, float(temperature)))

    mt = _qwen_clamp_max_tokens(
        _QWEN_MAX_TOKENS if max_tokens is None else max_tokens
    )

    model_name = _qwen_model_name()
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    req_json = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": mt,
        "enable_thinking": bool(enable_thinking),
    }
    if top_p is not None:
        req_json["top_p"] = max(0.0, min(1.0, float(top_p)))

    _top_p_sent = req_json["top_p"] if "top_p" in req_json else "未传（服务端默认）"
    print(f"[LLM 请求] temperature={temperature}, top_p={_top_p_sent}")

    last_error = None
    for attempt in range(_QWEN_RETRIES):
        try:
            with _QWEN_SEM:
                response = requests.post(
                    _qwen_chat_url(),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}"
                    },
                    json=req_json,
                    timeout=_QWEN_TIMEOUT
                )
            response_data = response.json()
            # 429/5xx 走重试
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                if attempt < _QWEN_RETRIES - 1:
                    time.sleep((2 ** attempt) * 0.8 + random.uniform(0, 0.3))
                    continue
            else:
                break
        except Exception as e:
            last_error = str(e)
            if attempt < _QWEN_RETRIES - 1:
                time.sleep((2 ** attempt) * 0.8 + random.uniform(0, 0.3))
                continue
            print(f"通义千问 API 请求出错: {last_error}")
            return ""
    else:
        print(f"通义千问 API 请求失败: {last_error}")
        return ""

    # 检查是否有错误
    if 'error' in response_data:
        error_msg = response_data['error'].get('message', '未知错误')
        print(f"API错误: {error_msg}")
        return ""
    
    if 'choices' not in response_data or not response_data['choices']:
        print("错误：响应中没有'choices'字段")
        return ""
    
    content = response_data['choices'][0]['message']['content']
    return content.strip()


call_doubao_api = call_qwen_api  # 兼容旧调用名


# 生成身体电量
def generate_body_battery(sleep_data, personality_type="M-L-C", output_dir=None):
    """睡眠报告展示「身体电量」（0–100）：仅由净睡时长与深/浅/REM 占净睡比例四项规则子分平均。"""
    _ = personality_type
    _ = output_dir
    return sleep_report_score_from_sleep_data(sleep_data)

# 生成身体电量状态
def get_body_battery_status(body_battery):
    """根据身体电量值返回状态描述"""
    if body_battery >= 95:
        return "身体电量已充满"
    elif body_battery >= 85:
        return "能量高度充沛"
    elif body_battery >= 75:
        return "电量储备充足"
    elif body_battery >= 65:
        return "正在进入蓄能态"
    else:
        return "能量正在温和回升"


# 生成听觉报告
def generate_auditory(sleep_data, user_id=None, sleep_events_index=None, output_dir="output"):
    """根据睡眠数据生成听觉报告。

    若有用户与睡眠窗：audios 条数严格等于睡眠窗内「打鼾 / 梦话 / 咳嗽」睡眠事件条数
    （与事件一一对应，由 build_audios_from_auditory_sleep_events 生成）；无窗或无事件列表则为空。
    """
    apnea_count = int(sleep_data["raw_data"].get("apnea_count", 0) or 0)
    rd = sleep_data.get("record_date", "") or ""
    # 仅当呼吸暂停次数 ≥5 时展示呼吸风险提示（标题按日期轮换）
    if apnea_count >= 5:
        target = build_apnea_auditory_target_title(rd)
        risk_alert = f"昨晚出现{apnea_count}次呼吸暂停疑似时间，建议关注。"
    else:
        target = ""
        risk_alert = ""

    audios = []
    sleep_time, window_end = sleep_local_window_bounds_from_sleep_data(sleep_data)
    flat = []
    if sleep_events_index is not None:
        flat = [e for lst in sleep_events_index.values() for e in lst]
    elif user_id:
        sleep_events_file = os.path.join(output_dir, f"{user_id}_sleep_events.json")
        if os.path.exists(sleep_events_file):
            try:
                with open(sleep_events_file, "r", encoding="utf-8") as f:
                    flat = json.load(f)
            except Exception:
                flat = []

    if user_id and sleep_time and window_end:
        sel = collect_auditory_sleep_events_in_window(
            flat, user_id, sleep_time, window_end, rd or None
        )
        audios = build_audios_from_auditory_sleep_events(sel, rd)

    # snoring_analysis.data_points 在流水线 report_audios 步补全
    return {
        "target": target,
        "risk_alert": risk_alert,
        "snoring_analysis": {"data_points": []},
        "audios": audios,
    }

# audio.json 兼容：audioUrl 可能是一维或二维数组
def _pick_audio_group(audio_groups, rnd=None):
    """
    兼容 audio.json 新旧结构：
    - 旧：audioUrl 为 [ {url,duration_sec}, ... ]
    - 新：audioUrl 为 [ [ {url,duration_sec}, ... ], [ ... ], ... ]
    返回：被选定的“子数组”（list[dict]），并尽量过滤掉无效项。
    """
    if not audio_groups:
        return []

    # 新结构：二维数组
    if isinstance(audio_groups, list) and audio_groups and isinstance(audio_groups[0], list):
        candidates = []
        for g in audio_groups:
            if not isinstance(g, list):
                continue
            valid = [it for it in g if isinstance(it, dict) and it.get("url") and (it.get("duration_sec", 0) or 0) > 0]
            if valid:
                candidates.append(valid)
        if not candidates:
            return []
        if rnd is None:
            rnd = random
        return list(rnd.choice(candidates))

    # 旧结构：一维数组
    valid = [it for it in audio_groups if isinstance(it, dict) and it.get("url") and (it.get("duration_sec", 0) or 0) > 0]
    return list(valid)


def _flatten_audio_groups(audio_groups):
    """把一维/二维结构扁平化为 list[dict]（过滤无效项）。"""
    if not audio_groups:
        return []
    if isinstance(audio_groups, list) and audio_groups and isinstance(audio_groups[0], list):
        out = []
        for g in audio_groups:
            if isinstance(g, list):
                out.extend(g)
        audio_groups = out
    return [it for it in (audio_groups or []) if isinstance(it, dict) and it.get("url") and (it.get("duration_sec", 0) or 0) > 0]


# 从audio.json获取sleep_talk数据
def get_sleep_talk_data(min_snore=2, max_snore=6, include_sleep_talk=True, include_cough=True):
    """从audio.json获取audios数据。Snore 至少 min_snore 条，可选 Somniloquy/Cough。"""
    audio_file = 'qiniu/audio.json'
    if not os.path.exists(audio_file):
        return []
    
    try:
        with open(audio_file, 'r', encoding='utf-8') as f:
            audio_data = json.load(f)
    except Exception as e:
        print(f"读取audio.json文件时出错: {str(e)}")
        return []
    
    selected_audio = []

    # 打鼾音频：至少 min_snore 条，尽量不重复
    # 关键：每条数据先从 audioUrl 选定一个子数组，后续只从该子数组中取
    snoring_audio = _pick_audio_group(audio_data.get('audioUrl', []), rnd=random)
    if snoring_audio:
        random.shuffle(snoring_audio)
        min_n = max(int(min_snore or 0), 0)
        if max_snore is None:
            want = min_n
        else:
            max_n = max(int(max_snore or 0), 0)
            if max_n < min_n:
                max_n = min_n
            want = random.randint(min_n, max_n)
        picked = snoring_audio[: min(want, len(snoring_audio))]
        # 若素材不足，允许重复补齐
        while len(picked) < want:
            picked.append(random.choice(snoring_audio))
        for it in picked:
            selected_audio.append({"url": it.get("url"), "duration_sec": it.get("duration_sec", 0), "type": "Snore"})

    # 梦话音频
    if include_sleep_talk:
        sleep_talking_audio = [item for item in audio_data.get('sleepTalkingUrl', []) if item.get('duration_sec', 0) > 0 and item.get('url')]
        if sleep_talking_audio:
            it = random.choice(sleep_talking_audio)
            selected_audio.append({"url": it.get("url"), "duration_sec": it.get("duration_sec", 0), "type": "Somniloquy"})

    # 咳嗽音频
    if include_cough:
        cough_audio = [item for item in audio_data.get('coughUrl', []) if item.get('duration_sec', 0) > 0 and item.get('url')]
        if cough_audio:
            it = random.choice(cough_audio)
            selected_audio.append({"url": it.get("url"), "duration_sec": it.get("duration_sec", 0), "type": "Cough"})

    return selected_audio


_AUDITORY_SLEEP_EVENT_CODES = frozenset({"snoring", "sleep_talking", "cough_clearing"})
_CODE_TO_AUDIO_TYPE = {"snoring": "Snore", "sleep_talking": "Somniloquy", "cough_clearing": "Cough"}


def plan_auditory_snore_talk_cough_counts(sleep_data):
    """打鼾：0 次，或一晚 3～6 条；梦话 0-1 次；咳嗽 0 次或 3 次。"""
    raw = sleep_data.get("raw_data", {})
    apnea_count = int(raw.get("apnea_count", 0) or 0)
    if apnea_count >= 5:
        has_snore = random.random() < 0.80
    else:
        has_snore = random.random() < 0.55
    n_snore = random.randint(3, 6) if has_snore else 0
    include_sleep_talk = random.random() < 0.35
    include_cough = random.random() < 0.25
    return n_snore, include_sleep_talk, include_cough


def sleep_local_window_bounds_from_sleep_data(sleep_data):
    """返回 (sleep_time, window_end) 本地 naive datetime；无效时 (None, None)。
    window_end 为「起床」时刻（raw_data.wake_time），即睡眠过程（入睡→睡眠结束）上界；
    若无 wake_time 则退化为 wake_up_time。
    """
    raw = sleep_data.get("raw_data", {})
    sleep_time_str = raw.get("sleep_time", "")
    wake_up_time_str = raw.get("wake_up_time", "")
    wake_time_str = raw.get("wake_time", "")
    if not sleep_time_str or (not wake_time_str and not wake_up_time_str):
        return None, None
    sleep_time = _parse_utc_iso_to_local_dt(sleep_time_str)
    window_end = _parse_utc_iso_to_local_dt(wake_time_str) or _parse_utc_iso_to_local_dt(
        wake_up_time_str
    )
    if not sleep_time or not window_end:
        return None, None
    if window_end < sleep_time:
        window_end += timedelta(days=1)
    return sleep_time, window_end


def collect_auditory_sleep_events_in_window(
    all_events,
    user_id,
    sleep_time,
    window_end,
    session_record_date=None,
):
    """同一 record_date（与健康记录日一致，含跨午夜仍用当日）且落在睡眠窗内的打鼾/梦话/咳嗽事件。"""
    if not sleep_time or not window_end or not all_events:
        return []
    out = []
    for e in all_events:
        if user_id and e.get("uid") != user_id:
            continue
        if session_record_date is not None and e.get("record_date") != session_record_date:
            continue
        code = e.get("code")
        if code not in _AUDITORY_SLEEP_EVENT_CODES:
            continue
        dt = session_anchor_event_local_dt(e, sleep_time, window_end)
        if dt == datetime.min:
            continue
        if not (sleep_time <= dt <= window_end):
            continue
        out.append(e)
    out.sort(key=lambda x: session_anchor_event_local_dt(x, sleep_time, window_end))
    return out


def build_audios_from_auditory_sleep_events(events_sorted, record_date=""):
    """按事件条数从 audio.json 取 URL，每条带与事件一致的 time（HH:MM）。"""
    if not events_sorted:
        return []
    audio_file = "qiniu/audio.json"
    if not os.path.exists(audio_file):
        return []
    try:
        with open(audio_file, "r", encoding="utf-8") as f:
            audio_data = json.load(f)
    except Exception:
        return []

    rnd = random.Random(hash(record_date) & 0xFFFFFFFF)
    # 关键：同一条睡眠报告内，Snore 的素材池固定为 audioUrl 的某一个子数组
    snore_pool_fixed = _pick_audio_group(audio_data.get("audioUrl", []), rnd=rnd)

    def pool_for(atype):
        if atype == "Snore":
            return list(snore_pool_fixed)
        if atype == "Somniloquy":
            return _flatten_audio_groups(audio_data.get("sleepTalkingUrl", []))
        if atype == "Cough":
            return _flatten_audio_groups(audio_data.get("coughUrl", []))
        return []

    audios = []
    used_urls = set()
    for ev in events_sorted:
        code = ev.get("code")
        atype = _CODE_TO_AUDIO_TYPE.get(code)
        if not atype:
            continue
        pool = list(pool_for(atype))
        if not pool:
            continue
        rnd.shuffle(pool)
        pick = None
        for it in pool:
            u = it.get("url")
            if u and u not in used_urls:
                pick = it
                break
        if pick is None:
            pick = rnd.choice(pool)
        u = pick.get("url")
        if u:
            used_urls.add(u)
        ts = ev.get("event_timestamp") or ""
        audios.append(
            {
                "url": pick.get("url"),
                "duration_sec": pick.get("duration_sec", 0),
                "type": atype,
                "time": ts,
            }
        )
    return audios


def backfill_auditory_audio_times_from_window_events(
    audios, all_events, user_id, sleep_time, window_end, health_report_record_date=None
):
    """为尚无 time 的听觉类 audio 按睡眠窗内同类事件时间顺序补齐。
    health_report_record_date：与健康/报告 record_date 一致（凌晨时刻事件仍用该日）。
    """
    if not audios or not all_events:
        return

    queues = {
        "Snore": deque(),
        "Somniloquy": deque(),
        "Cough": deque(),
    }
    for e in collect_auditory_sleep_events_in_window(
        all_events,
        user_id,
        sleep_time,
        window_end,
        health_report_record_date,
    ):
        at = _CODE_TO_AUDIO_TYPE.get(e.get("code"))
        ts = e.get("event_timestamp")
        if at and ts:
            queues[at].append(ts)
    for audio in audios:
        at = audio.get("type")
        if not at or audio.get("time"):
            continue
        dq = queues.get(at)
        if dq:
            audio["time"] = dq.popleft()


def resolve_auditory_audio_local_dt(
    record_date, time_str, sleep_start=None, window_end=None
):
    """将 audio.time（HH:MM）解析为睡眠窗内的本地 naive datetime；无窗时退化为 record_date 当日组合。"""
    if not record_date or not time_str:
        return None
    ts = str(time_str).strip()
    if not ts:
        return None
    parsed_tm = None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed_tm = datetime.strptime(ts, fmt).time()
            break
        except ValueError:
            continue
    if parsed_tm is None:
        return None
    try:
        d0 = datetime.strptime(str(record_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    c0 = datetime.combine(d0, parsed_tm)
    candidates = [c0, c0 + timedelta(days=1), c0 - timedelta(days=1)]
    if sleep_start is not None and window_end is not None:
        in_window = [c for c in candidates if sleep_start <= c <= window_end]
        if len(in_window) == 1:
            return in_window[0]
        if len(in_window) > 1:
            return min(in_window)
        return min(candidates, key=lambda c: abs((c - sleep_start).total_seconds()))
    return c0


def build_snoring_analysis_data_points(
    audios,
    env_rows_for_date,
    record_date,
    sleep_start,
    window_end,
    apnea_count,
):
    """
    由 audios 中 type 为 Snore 且含 time 的条目生成 data_points：{time, value}。
    value 优先取环境噪音时间上最近的一条；否则在 60–85（呼吸暂停多时可至 95）随机。
    """
    assigned = []
    env_rows_for_date = env_rows_for_date or []
    for audio in audios or []:
        if (audio.get("type") or "") != "Snore" or not audio.get("time"):
            continue
        snore_value = None
        if env_rows_for_date:
            audio_dt = resolve_auditory_audio_local_dt(
                record_date, audio["time"], sleep_start, window_end
            )
            if audio_dt is not None:
                _, nearest_noise = min(
                    env_rows_for_date,
                    key=lambda item: abs((item[0] - audio_dt).total_seconds()),
                )
                snore_value = int(nearest_noise)
        if snore_value is None:
            base_lo, base_hi = 60, 85
            if int(apnea_count or 0) >= 5:
                base_hi = min(95, base_hi + 5)
            snore_value = random.randint(base_lo, base_hi)
        assigned.append({"time": audio["time"], "value": snore_value})
    return assigned


def index_environment_noise_rows_by_record_date(user_id, output_dir="output"):
    """
    读取 output/{user_id}_environment_data.json，建立 record_date -> [(本地时刻, noise), ...]。
    与流水线 report_audios 中环境索引一致。
    """
    environment_file = os.path.join(output_dir, f"{user_id}_environment_data.json")
    out = {}
    if not os.path.exists(environment_file):
        return out
    try:
        with open(environment_file, "r", encoding="utf-8") as f:
            environment_data = json.load(f)
    except Exception:
        return out
    if not isinstance(environment_data, list):
        return out
    for row in environment_data:
        if not isinstance(row, dict):
            continue
        rd = row.get("record_date")
        if not rd:
            continue
        dt_local = collected_at_to_local_naive_dt(row.get("collected_at"))
        if dt_local is None:
            continue
        noise = row.get("noise")
        try:
            noise_val = int(round(float(noise)))
        except (TypeError, ValueError):
            continue
        out.setdefault(rd, []).append((dt_local, noise_val))
    for rows in out.values():
        rows.sort(key=lambda x: x[0])
    return out


def rebuild_auditory_audios_and_snoring_data_points(
    record_date,
    user_id,
    sleep_events,
    sleep_day,
    env_rows_for_date,
):
    """
    按睡眠窗从 sleep_events 重建 audios，并生成 snoring data_points（不写回文件）。
    与 user_gen_pipeline.report_audios 中 audios + data_points 计算一致；不含 sleep_events duration 回填。
    返回 (audios, data_points 列表)。
    """
    apnea_count = int((sleep_day.get("raw_data") or {}).get("apnea_count", 0) or 0) if sleep_day else 0
    st, we = sleep_local_window_bounds_from_sleep_data(sleep_day or {})
    sel = (
        collect_auditory_sleep_events_in_window(
            sleep_events, user_id, st, we, session_record_date=record_date
        )
        if (st and we)
        else []
    )
    audios = build_audios_from_auditory_sleep_events(sel, record_date)
    if st and we:
        backfill_auditory_audio_times_from_window_events(
            audios, sleep_events, user_id, st, we, record_date
        )
    dps = build_snoring_analysis_data_points(
        audios,
        env_rows_for_date or [],
        record_date,
        st,
        we,
        apnea_count,
    )
    return audios, dps


def _audio_time_to_session_dt(audio_time, record_date, sleep_time=None, window_end=None):
    """将报告 audio.time（HH:MM）对齐为会话内绝对时刻。"""
    if not audio_time or not record_date:
        return datetime.min
    pseudo_event = {"record_date": record_date, "event_timestamp": str(audio_time).strip()}
    return session_anchor_event_local_dt(pseudo_event, sleep_time, window_end)


def backfill_auditory_event_durations_from_report_audios(
    audios,
    all_events,
    user_id,
    record_date,
    sleep_time=None,
    window_end=None,
):
    """
    按睡眠报告 auditory.audios 回填 sleep_events 中听觉事件 duration_sec。
    匹配规则（同 uid+record_date+type）：
    1) 优先 time 精确匹配；
    2) 其次按 time 最近匹配；
    3) 仍无法匹配则按该类型事件时间顺序依次匹配。
    返回更新条数。
    """
    if not audios or not all_events or not record_date:
        return 0

    event_buckets = {"Snore": [], "Somniloquy": [], "Cough": []}
    for e in all_events:
        if user_id and e.get("uid") != user_id:
            continue
        if e.get("record_date") != record_date:
            continue
        atype = _CODE_TO_AUDIO_TYPE.get(e.get("code"))
        if atype not in event_buckets:
            continue
        dt = session_anchor_event_local_dt(e, sleep_time, window_end)
        event_buckets[atype].append((dt, e))

    for k in event_buckets:
        event_buckets[k].sort(key=lambda x: x[0])

    used_event_obj_ids = set()
    updated = 0

    for audio in audios:
        atype = audio.get("type")
        if atype not in event_buckets:
            continue
        try:
            duration = int(audio.get("duration_sec", 0) or 0)
        except (TypeError, ValueError):
            duration = 0
        if duration <= 0:
            continue

        candidates = [
            (dt, ev)
            for dt, ev in event_buckets[atype]
            if id(ev) not in used_event_obj_ids
        ]
        if not candidates:
            continue

        chosen = None
        audio_time = str(audio.get("time") or "").strip()
        if audio_time:
            # 1) 同 HH:MM 的精确匹配
            for dt, ev in candidates:
                if str(ev.get("event_timestamp") or "").strip() == audio_time:
                    chosen = ev
                    break
            # 2) 距离 audio.time 最近匹配
            if chosen is None:
                target_dt = _audio_time_to_session_dt(
                    audio_time, record_date, sleep_time, window_end
                )
                if target_dt != datetime.min:
                    _, chosen = min(
                        candidates,
                        key=lambda x: abs((x[0] - target_dt).total_seconds()),
                    )

        # 3) 无 time 或无法按 time 定位时，按顺序取首个未使用事件
        if chosen is None:
            chosen = candidates[0][1]

        chosen["duration_sec"] = duration
        used_event_obj_ids.add(id(chosen))
        updated += 1

    return updated


_SLEEP_EVENT_SIGNAL_RULES = {
    # noise / environment
    "appliance_continuous": ("environment", ("noise",), 62, 82),
    "environment_continuous": ("environment", ("noise",), 62, 82),
    "neighbor_continuous": ("environment", ("noise",), 60, 80),
    "nature_continuous": ("environment", ("noise",), 58, 76),
    "sudden_impact": ("environment", ("noise",), 65, 88),
    "sudden_traffic": ("environment", ("noise",), 64, 86),
    "voice_doorbell": ("environment", ("noise",), 63, 85),
    "nature_sudden": ("environment", ("noise",), 62, 84),
    "object_sudden": ("environment", ("noise",), 62, 84),
    "snoring": ("environment", ("noise",), 55, 72),
    "sleep_talking": ("environment", ("noise",), 48, 68),
    "silence": ("environment", ("noise",), 22, 40),
    "sleeping": ("environment", ("temperature",), 27, 31),
    # vitals
    "heart_rate_increase": ("vitals", ("metrics", "heart_rate"), 95, 120),
    "nightmare": ("vitals", ("metrics", "heart_rate"), 90, 112),
    "movement": ("vitals", ("metrics", "body_motion_level"), 40, 85),
    "once_movement": ("vitals", ("metrics", "body_motion_level"), 25, 60),
    "natural_movement": ("vitals", ("metrics", "body_motion_level"), 15, 35),
    "posture_switch": ("vitals", ("metrics", "body_motion_level"), 28, 65),
    "limb_movements": ("vitals", ("metrics", "body_motion_level"), 30, 75),
    "cough_clearing": ("environment", ("noise",), 46, 66),
    "swallow": ("vitals", ("metrics", "respiration_rate"), 14, 20),
    "breathing": ("vitals", ("metrics", "respiration_rate"), 12, 20),
}

_EVENT_FEEDBACK_MAX_DELTA_MINUTES = 10
# 每日每字段最大回填条数：噪音/心率类事件较多，需要足够大的上限才能逐事件回填
_EVENT_FEEDBACK_DAILY_FIELD_UPDATE_CAPS = {
    ("environment", "noise"): max(1, int(os.getenv("SLEEP_EVENTS_FEEDBACK_NOISE_CAP", "60"))),
    ("environment", "temperature"): 1,
    ("vitals", "metrics.heart_rate"): max(1, int(os.getenv("SLEEP_EVENTS_FEEDBACK_HR_CAP", "30"))),
    ("vitals", "metrics.body_motion_level"): max(1, int(os.getenv("SLEEP_EVENTS_FEEDBACK_MOTION_CAP", "15"))),
    ("vitals", "metrics.respiration_rate"): max(1, int(os.getenv("SLEEP_EVENTS_FEEDBACK_RESP_CAP", "8"))),
}
# 回填时搜索现有行的窗口宽度（事件时刻 ± 此值分钟），避免因采样间隔错开而找不到匹配行
_EVENT_FEEDBACK_WINDOW_HALF_MINUTES = max(1, int(os.getenv("SLEEP_EVENTS_FEEDBACK_WINDOW_HALF_MIN", "5")))
# 体征 / 环境：相邻 collected_at 最小间隔（分钟）；采样与睡眠事件插入行均不得低于该值
MIN_VITAL_ENV_COLLECTED_AT_GAP_MINUTES = max(1, int(os.getenv("MIN_VITAL_ENV_COLLECTED_AT_GAP_MINUTES", "5")))
# 插入新行时与已有行的最小时间间隔（分钟），不得低于采样最小间隔
_EVENT_FEEDBACK_INSERT_MIN_GAP_MINUTES = max(
    MIN_VITAL_ENV_COLLECTED_AT_GAP_MINUTES,
    int(os.getenv("SLEEP_EVENTS_FEEDBACK_INSERT_GAP_MIN", str(MIN_VITAL_ENV_COLLECTED_AT_GAP_MINUTES))),
)

# 默认关闭：睡眠事件阶段不向 vitals_data / environment_data 插入或改写采样行。
# 需要旧版「按事件回填体征/环境」时设置环境变量 SLEEP_EVENT_FEEDBACK_TO_VITALS_ENV=1。
SLEEP_EVENT_FEEDBACK_TO_VITALS_ENV = str(
    os.getenv("SLEEP_EVENT_FEEDBACK_TO_VITALS_ENV", "")
).strip().lower() in ("1", "true", "yes")

_SLEEP_EVENT_FEEDBACK_PRIORITIES = {
    # 噪声：突发 > 持续 > 常规
    "sudden_impact": 100,
    "sudden_traffic": 98,
    "voice_doorbell": 96,
    "nature_sudden": 94,
    "object_sudden": 92,
    "appliance_continuous": 85,
    "environment_continuous": 84,
    "neighbor_continuous": 83,
    "nature_continuous": 82,
    "snoring": 70,
    "sleep_talking": 68,
    "silence": 30,
    "sleeping": 95,
    # 体征：强异常 > 中异常 > 常规
    "heart_rate_increase": 100,
    "nightmare": 90,
    "movement": 80,
    "limb_movements": 74,
    "posture_switch": 72,
    "once_movement": 70,
    "natural_movement": 60,
    "cough_clearing": 66,
    "swallow": 62,
    "breathing": 50,
}


def apply_sleep_event_feedback_to_fitness_and_environment(user_id):
    """
    睡眠事件后的联动处理（默认不写 vitals_data / environment_data）：
    - 当 SLEEP_EVENT_FEEDBACK_TO_VITALS_ENV=1 时：按事件向 environment_data / vitals_data
      插入或改写最接近事件时刻的采样行（aaa.md 数据标识与范围）。
    - 始终可能更新：噩梦相关的 health_data.idf_data、sleep_events 排序与联动事件。
    """
    sleep_events_file = os.path.join("output", f"{user_id}_sleep_events.json")
    environment_file = os.path.join("output", f"{user_id}_environment_data.json")
    vitals_file = os.path.join("output", f"{user_id}_vitals_data.json")
    health_file = os.path.join("output", f"{user_id}_health_data.json")
    report_file = os.path.join("output", f"{user_id}_event_feedback_report.json")

    def _read_list_json(path):
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _get_nested(dct, path):
        cur = dct
        for k in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(k)
        return cur

    def _set_nested(dct, path, value):
        cur = dct
        for k in path[:-1]:
            nxt = cur.get(k)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[k] = nxt
            cur = nxt
        cur[path[-1]] = value

    report_rows = []
    sleep_events = _read_list_json(sleep_events_file)
    if not sleep_events:
        try:
            with open(report_file, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return {
            "fitness_updates": 0,
            "environment_updates": 0,
            "vitals_updates": 0,
            "skipped_by_delta": 0,
            "skipped_by_cap": 0,
            "skipped_by_priority": 0,
            "report_file": report_file,
        }

    environment_data = _read_list_json(environment_file)
    vitals_data = _read_list_json(vitals_file)
    health_data = _read_list_json(health_file)
    health_by_date = _health_data_by_record_date(user_id)
    health_rows_by_date = {}
    for row in health_data:
        rd = row.get("record_date") if isinstance(row, dict) else None
        if rd:
            health_rows_by_date[rd] = row

    min_gap_sec = int(_EVENT_FEEDBACK_INSERT_MIN_GAP_MINUTES * 60)

    def _build_event_window(ev, target_dt):
        try:
            dur = int(ev.get("duration_sec", 0) or 0)
        except (TypeError, ValueError):
            dur = 0
        dur = max(1, dur)
        # 以事件时刻为中心，前后各扩展 _EVENT_FEEDBACK_WINDOW_HALF_MINUTES 分钟搜索匹配行，
        # 避免因采样间隔与事件时刻错开而始终找不到行（原窗口仅为几秒的事件持续时长）
        half = timedelta(minutes=_EVENT_FEEDBACK_WINDOW_HALF_MINUTES)
        w_start = target_dt - half
        w_end = target_dt + half
        return w_start, w_end, dur

    def _row_candidates_with_dt(rows, record_date, used_idx_set):
        out = []
        for i, row in enumerate(rows):
            if i in used_idx_set:
                continue
            if row.get("record_date") != record_date:
                continue
            row_dt = collected_at_to_local_naive_dt(row.get("collected_at"))
            if row_dt is None:
                continue
            out.append((i, row, row_dt))
        out.sort(key=lambda x: x[2])
        return out

    def _pick_row_in_window(rows, record_date, used_idx_set, target_dt, w_start, w_end):
        cands = _row_candidates_with_dt(rows, record_date, used_idx_set)
        in_window = [(i, row, dt) for i, row, dt in cands if w_start <= dt <= w_end]
        if not in_window:
            return None, None, None, None
        i, row, dt = min(
            in_window, key=lambda x: abs((x[2] - target_dt).total_seconds())
        )
        dist_sec = abs((dt - target_dt).total_seconds())
        return i, row, dt, dist_sec

    def _pick_template_row(rows, record_date, target_dt):
        cands = _row_candidates_with_dt(rows, record_date, set())
        if not cands:
            return None
        _, row, _ = min(cands, key=lambda x: abs((x[2] - target_dt).total_seconds()))
        return row

    def _find_insert_dt_with_gap(rows, record_date, preferred_dt, min_gap_seconds):
        cands = _row_candidates_with_dt(rows, record_date, set())
        existing = [dt for _, _, dt in cands]
        if not existing:
            return preferred_dt

        def ok(dt):
            return all(abs((dt - ed).total_seconds()) >= min_gap_seconds for ed in existing)

        if ok(preferred_dt):
            return preferred_dt

        for mul in range(1, 13):
            for sign in (1, -1):
                trial = preferred_dt + timedelta(seconds=sign * mul * min_gap_seconds)
                if ok(trial):
                    return trial
        return None

    def _build_inserted_row(source, template_row, record_date, insert_dt):
        if source == "environment":
            if template_row:
                row = copy.deepcopy(template_row)
                row.pop("_id", None)
            else:
                row = {
                    "uid": user_id,
                    "session_id": "",
                    "record_date": record_date,
                    "temperature": 24,
                    "humidity": 50,
                    "illuminance": 0,
                    "noise": 35,
                    "device_id": "",
                }
            row["uid"] = user_id
            row["record_date"] = record_date
            row["collected_at"] = local_naive_dt_to_utc_iso_z(insert_dt)
            ts = (insert_dt + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
            row["create_time"] = ts
            row["update_time"] = ts
            row["session_id"] = ""
            if "device_id" not in row:
                row["device_id"] = ""
            return row

        # vitals
        if template_row:
            row = copy.deepcopy(template_row)
            row.pop("_id", None)
        else:
            metrics_fb = {
                "respiration_rate": 14,
                "heart_rate": 68,
                "body_motion_level": 10,
                "blood_oxygen": 97,
                "blood_pressure_systolic": 116,
                "blood_pressure_diastolic": 78,
                "hrv": 72,
            }
            row = {
                "uid": user_id,
                "record_date": record_date,
                "data_source": "radar",
                "metrics": metrics_fb,
                "device_id": "",
                "session_id": "",
            }
        row["uid"] = user_id
        row["record_date"] = record_date
        row["collected_at"] = local_naive_dt_to_utc_iso_z(insert_dt)
        ts = (insert_dt + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        row["create_time"] = ts
        row["update_time"] = ts
        if "metrics" not in row or not isinstance(row["metrics"], dict):
            row["metrics"] = {}
        if "data_source" not in row:
            row["data_source"] = "radar"
        if "device_id" not in row:
            row["device_id"] = ""
        if "session_id" not in row:
            row["session_id"] = ""
        return row

    def _next_unique_insert_dt(rows, record_date, preferred_dt):
        cands = _row_candidates_with_dt(rows, record_date, set())
        existing = {dt for _, _, dt in cands}
        if preferred_dt not in existing:
            return preferred_dt
        for i in range(1, 600):
            trial = preferred_dt + timedelta(seconds=i)
            if trial not in existing:
                return trial
        return preferred_dt

    def _parse_collected_at_utc_naive(collected_at_str):
        if not collected_at_str or not str(collected_at_str).strip():
            return None
        s = str(collected_at_str).strip()
        try:
            if s.endswith("Z"):
                return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
            if "T" in s:
                return datetime.fromisoformat(s).replace(tzinfo=None)
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None

    def _utc_naive_to_iso_z(dt_utc):
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _event_target_utc_naive_no_crossday(record_date, event_time_hhmm, fallback_local_dt=None):
        try:
            d = datetime.strptime(str(record_date)[:10], "%Y-%m-%d")
        except Exception:
            return None
        h = None
        m = None
        if isinstance(event_time_hhmm, str) and ":" in event_time_hhmm:
            try:
                p = event_time_hhmm.strip().split(":")
                h = int(p[0])
                m = int(p[1])
            except Exception:
                h = None
                m = None
        if h is None or m is None:
            if isinstance(fallback_local_dt, datetime):
                h = fallback_local_dt.hour
                m = fallback_local_dt.minute
            else:
                return None
        # 按用户要求：仅做时区小时转换，日期固定为 record_date，不做跨天处理
        utc_hour = (h - 8) % 24
        return datetime(d.year, d.month, d.day, utc_hour, m, 0)

    def _find_row_by_target_utc_minute(rows, record_date, target_utc_dt):
        hit = None
        for i, row in enumerate(rows):
            if row.get("record_date") != record_date:
                continue
            row_utc = _parse_collected_at_utc_naive(row.get("collected_at"))
            if row_utc is None:
                continue
            if (
                row_utc.year == target_utc_dt.year
                and row_utc.month == target_utc_dt.month
                and row_utc.day == target_utc_dt.day
                and row_utc.hour == target_utc_dt.hour
                and row_utc.minute == target_utc_dt.minute
            ):
                if hit is None:
                    hit = (i, row, row_utc)
                else:
                    if abs(row_utc.second) < abs(hit[2].second):
                        hit = (i, row, row_utc)
        return hit

    def _build_inserted_row_with_exact_utc(source, template_row, record_date, insert_utc_dt):
        if source == "environment":
            if template_row:
                row = copy.deepcopy(template_row)
                row.pop("_id", None)
            else:
                row = {
                    "uid": user_id,
                    "session_id": "",
                    "record_date": record_date,
                    "temperature": 24,
                    "humidity": 50,
                    "illuminance": 0,
                    "noise": 35,
                    "device_id": "",
                }
            row["uid"] = user_id
            row["record_date"] = record_date
            row["collected_at"] = _utc_naive_to_iso_z(insert_utc_dt)
            create_local = insert_utc_dt + timedelta(hours=8, seconds=1)
            row["create_time"] = create_local.strftime("%Y-%m-%d %H:%M:%S")
            row["update_time"] = create_local.strftime("%Y-%m-%d %H:%M:%S")
            row["session_id"] = ""
            if "device_id" not in row:
                row["device_id"] = ""
            return row
        if template_row:
            row = copy.deepcopy(template_row)
            row.pop("_id", None)
        else:
            row = {
                "uid": user_id,
                "record_date": record_date,
                "data_source": "radar",
                "metrics": {
                    "respiration_rate": 14,
                    "heart_rate": 68,
                    "body_motion_level": 10,
                    "blood_oxygen": 97,
                    "blood_pressure_systolic": 116,
                    "blood_pressure_diastolic": 78,
                    "hrv": 72,
                },
                "device_id": "",
                "session_id": "",
            }
        row["uid"] = user_id
        row["record_date"] = record_date
        row["collected_at"] = _utc_naive_to_iso_z(insert_utc_dt)
        create_local = insert_utc_dt + timedelta(hours=8, seconds=1)
        row["create_time"] = create_local.strftime("%Y-%m-%d %H:%M:%S")
        row["update_time"] = create_local.strftime("%Y-%m-%d %H:%M:%S")
        if "metrics" not in row or not isinstance(row["metrics"], dict):
            row["metrics"] = {}
        if "data_source" not in row:
            row["data_source"] = "radar"
        if "device_id" not in row:
            row["device_id"] = ""
        if "session_id" not in row:
            row["session_id"] = ""
        return row

    def _hhmm_to_minute(hhmm):
        if not isinstance(hhmm, str):
            return None
        parts = hhmm.strip().split(":")
        if len(parts) < 2:
            return None
        try:
            h = int(parts[0])
            m = int(parts[1])
            return h * 60 + m
        except (TypeError, ValueError):
            return None

    def _minute_to_hhmm(total_min):
        mm = int(total_min) % 1440
        return f"{mm // 60:02d}:{mm % 60:02d}"

    def _idf_to_monotonic_ranges(idf_data):
        ranges = []
        prev_end = None
        for seg in (idf_data or []):
            if not isinstance(seg, dict):
                continue
            stg = str(seg.get("stage") or "light")
            s_raw = _hhmm_to_minute(seg.get("start"))
            e_raw = _hhmm_to_minute(seg.get("end"))
            if s_raw is None or e_raw is None:
                continue
            if prev_end is None:
                s = s_raw
            else:
                s = s_raw
                while s < prev_end:
                    s += 1440
            e = e_raw
            while e <= s:
                e += 1440
            ranges.append((stg, s, e))
            prev_end = e
        return ranges

    def _timeline_to_idf(first_minute, timeline):
        out = []
        if not timeline:
            return out
        i = 0
        n = len(timeline)
        while i < n:
            stg = timeline[i]
            j = i + 1
            while j < n and timeline[j] == stg:
                j += 1
            out.append(
                {
                    "stage": stg,
                    "start": _minute_to_hhmm(first_minute + i),
                    "end": _minute_to_hhmm(first_minute + j),
                }
            )
            i = j
        # 消除极短非 awake 片段（< 3 分钟），合并到前一段；若是首段则合并到后一段
        _MIN_SEG = 3

        def _dur(seg):
            sh, sm = map(int, seg["start"].split(":"))
            eh, em = map(int, seg["end"].split(":"))
            d = (eh * 60 + em) - (sh * 60 + sm)
            return d if d >= 0 else d + 1440

        changed = True
        while changed:
            changed = False
            k = 0
            while k < len(out):
                if out[k]["stage"] != "awake" and _dur(out[k]) < _MIN_SEG:
                    if k > 0:
                        out[k - 1]["end"] = out[k]["end"]
                        out.pop(k)
                        # 前后段同类则再合并
                        if k <= len(out) - 1 and out[k - 1]["stage"] == out[k]["stage"]:
                            out[k - 1]["end"] = out[k]["end"]
                            out.pop(k)
                    elif k + 1 < len(out):
                        out[k + 1]["start"] = out[k]["start"]
                        out.pop(k)
                    else:
                        k += 1
                        continue
                    changed = True
                else:
                    k += 1
        return out

    def _insert_awake_into_idf(idf_data, anchor_dt, duration_min):
        ranges = _idf_to_monotonic_ranges(idf_data)
        if not ranges:
            return idf_data
        first_min = ranges[0][1]
        last_min = ranges[-1][2]
        total = max(1, last_min - first_min)
        base_stage = ranges[0][0] if ranges[0][0] else "light"
        timeline = [base_stage] * total
        for stg, s, e in ranges:
            a = max(0, s - first_min)
            b = min(total, e - first_min)
            if a < b:
                timeline[a:b] = [stg] * (b - a)
        anchor_min = _hhmm_to_minute(anchor_dt.strftime("%H:%M"))
        if anchor_min is None:
            return idf_data
        candidates = [anchor_min - 1440, anchor_min, anchor_min + 1440]
        anchor_axis = min(candidates, key=lambda x: min(abs(x - first_min), abs(x - last_min)))
        if anchor_axis < first_min:
            anchor_axis = first_min
        if anchor_axis >= last_min:
            anchor_axis = last_min - 1
        aw_start = max(0, anchor_axis - first_min)
        aw_end = min(total, aw_start + max(1, int(duration_min)))
        if aw_start >= aw_end:
            return idf_data
        timeline[aw_start:aw_end] = ["awake"] * (aw_end - aw_start)
        return _timeline_to_idf(first_min, timeline)

    def _shift_dt_to_current_stage_tail(idf_data, local_dt):
        """将时间点调整到其所在分期的稍末尾（优先 end-1min，且尽量不贴边界）。"""
        ranges = _idf_to_monotonic_ranges(idf_data)
        if not ranges or local_dt is None:
            return local_dt
        first_min = ranges[0][1]
        last_min = ranges[-1][2]
        anchor_min = _hhmm_to_minute(local_dt.strftime("%H:%M"))
        if anchor_min is None:
            return local_dt
        candidates = [anchor_min - 1440, anchor_min, anchor_min + 1440]
        anchor_axis = min(
            candidates, key=lambda x: min(abs(x - first_min), abs(x - last_min))
        )
        seg = None
        for stg, s, e in ranges:
            if s <= anchor_axis < e:
                seg = (stg, s, e)
                break
        if seg is None:
            return local_dt
        _, s, e = seg
        span = max(1, e - s)
        if span >= 3:
            tail_axis = e - 1
        elif span == 2:
            tail_axis = s + 1
        else:
            return local_dt
        if not (s < tail_axis < e):
            return local_dt
        return local_dt + timedelta(minutes=(tail_axis - anchor_axis))

    # 直接插入模式：每个可映射睡眠事件都新增一条环境/体征行，不再修改已有行
    direct_jobs = []
    nightmare_awake_requests = {}
    sleep_events_nightmare_time_updates = 0
    for idx, ev in enumerate(sleep_events):
        if ev.get("uid") != user_id:
            continue
        event_type = ev.get("event_type")
        code = ev.get("code")
        if event_type == "AI主动干预" and code not in {"nightmare", "heart_rate_increase"}:
            continue
        rule = _SLEEP_EVENT_SIGNAL_RULES.get(code)
        if not rule:
            continue
        rd = ev.get("record_date")
        if not rd:
            continue
        sleep_day = health_by_date.get(rd) or {}
        st, we = sleep_local_window_bounds_from_sleep_data(sleep_day)
        target_dt = session_anchor_event_local_dt(ev, st, we)
        if target_dt == datetime.min:
            continue
        w_start, w_end, _ = _build_event_window(ev, target_dt)
        source, path, lo, hi = rule
        target_utc_dt = _event_target_utc_naive_no_crossday(
            rd, ev.get("event_timestamp") or "", fallback_local_dt=target_dt
        )
        if target_utc_dt is None:
            continue
        if code == "nightmare" and event_type != "AI主动干预":
            # 所有噩梦事件都应在后续出现短暂清醒段：
            # - 主噩梦事件：清醒段锚定到事件后约 2.5 分钟（与噩床前概率性心率上升同属一条生理链路）
            # - AI 主动干预：事件时间已是后续锚点，直接使用即可
            # 以事件为粒度：50% 生成清醒段，50% 不生成
            if random.random() >= 0.5:
                continue
            shifted_dt = _shift_dt_to_current_stage_tail(sleep_day.get("idf_data"), target_dt)
            if isinstance(shifted_dt, datetime):
                target_dt = clamp_dt_to_sleep_window(shifted_dt, st, we)
                ev["event_timestamp"] = format_sleep_event_local_timestamp(target_dt)
                ev["update_time"] = generate_iso_date()
                sleep_events_nightmare_time_updates += 1
                # 与 generate_sleep_events 一致：噩梦后约 2.5 分钟为 AI 干预锚点；此处移动了噩梦时刻，必须同步对应 AI，否则会早于噩梦。
                nid = str(ev.get("_id") or "")
                if nid:
                    ai_anchor = clamp_dt_to_sleep_window(
                        target_dt + timedelta(minutes=2.5), st, we
                    )
                    for ev_ai in sleep_events:
                        if (
                            ev_ai.get("event_type") == "AI主动干预"
                            and ev_ai.get("code") == "nightmare"
                            and str(ev_ai.get("related_event_id") or "") == nid
                        ):
                            ev_ai["event_timestamp"] = format_sleep_event_local_timestamp(
                                ai_anchor
                            )
                            ev_ai["update_time"] = generate_iso_date()
                            break
            dur_min = random.randint(3, 5)
            awake_anchor_dt = target_dt + timedelta(minutes=2.5)
            nightmare_awake_requests.setdefault(rd, []).append(
                {
                    "target_dt": awake_anchor_dt,
                    "duration_min": dur_min,
                    "event_id": ev.get("_id") or "",
                    "source_nightmare_id": (
                        (ev.get("related_event_id") or "")
                        if event_type == "AI主动干预"
                        else (ev.get("_id") or "")
                    ),
                    "event_idx": idx,
                    "event_time": ev.get("event_timestamp") or "",
                }
            )
        direct_jobs.append(
            {
                "event_idx": idx,
                "event_id": ev.get("_id") or "",
                "record_date": rd,
                "code": code,
                "source": source,
                "path": path,
                "field_key": ".".join(path),
                "lo": int(lo),
                "hi": int(hi),
                "target_dt": target_dt,
                "window_start": w_start,
                "window_end": w_end,
                "event_time": ev.get("event_timestamp") or "",
                "target_utc_dt": target_utc_dt,
            }
        )

    if not direct_jobs:
        try:
            with open(report_file, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return {
            "fitness_updates": 0,
            "environment_updates": 0,
            "vitals_updates": 0,
            "skipped_by_delta": 0,
            "skipped_by_cap": 0,
            "skipped_by_priority": 0,
            "report_file": report_file,
        }

    direct_jobs.sort(key=lambda j: (j["record_date"], j["target_dt"], j["event_idx"]))
    environment_updates = 0
    vitals_updates = 0
    health_updates = 0

    if SLEEP_EVENT_FEEDBACK_TO_VITALS_ENV:
        for job in direct_jobs:
            rd = job["record_date"]
            target_dt = job["target_dt"]
            code = job["code"]
            source = job["source"]
            path = job["path"]
            lo = job["lo"]
            hi = job["hi"]
            rows = environment_data if source == "environment" else vitals_data
            target_utc_dt = job["target_utc_dt"]
            matched = _find_row_by_target_utc_minute(rows, rd, target_utc_dt)
            if matched is not None:
                _, row, matched_utc = matched
                matched_time_str = matched_utc.strftime("%Y-%m-%d %H:%M:%S")
                op_reason = "matched_exact_utc_minute"
            else:
                tpl = _pick_template_row(rows, rd, target_dt)
                row = _build_inserted_row_with_exact_utc(source, tpl, rd, target_utc_dt)
                rows.append(row)
                matched_time_str = target_utc_dt.strftime("%Y-%m-%d %H:%M:%S")
                op_reason = "inserted_exact_utc_time"
            old_v = _get_nested(row, path)
            val = random.randint(lo, hi)
            _set_nested(row, path, int(val))

            if source == "environment":
                environment_updates += 1
                if tuple(path) == ("noise",):
                    row["noise_event_affected"] = True
                    row["noise_event_code"] = code
                    row["noise_event_update_time"] = generate_iso_date()
            else:
                vitals_updates += 1

            report_rows.append(
                {
                    "event_id": job["event_id"],
                    "event_idx": job["event_idx"],
                    "record_date": rd,
                    "event_time": job["event_time"],
                    "code": code,
                    "source": source,
                    "field": job["field_key"],
                    "status": "updated",
                    "reason": op_reason,
                    "matched_row_id": row.get("_id") or "",
                    "matched_row_time": matched_time_str,
                    "distance_sec": 0,
                    "old_value": old_v,
                    "new_value": int(val),
                    "range": [lo, hi],
                    "window_start": job["window_start"].strftime("%Y-%m-%d %H:%M:%S"),
                    "window_end": job["window_end"].strftime("%Y-%m-%d %H:%M:%S"),
                    "inserted_collected_at_utc": row.get("collected_at") or "",
                    "target_utc_time": target_utc_dt.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )

        if environment_updates > 0:
            _rebalance_environment_noise_daily_after_feedback(environment_data)
            with open(environment_file, "w", encoding="utf-8") as f:
                json.dump(environment_data, f, ensure_ascii=False, indent=2)

        if vitals_updates > 0:
            with open(vitals_file, "w", encoding="utf-8") as f:
                json.dump(vitals_data, f, ensure_ascii=False, indent=2)

    sleep_events_updates = 0
    sleep_events_ai_updates = 0
    heart_event_info = {
        "type": "abnormal",
        "name": "心率上升",
        "detail": {
            "trigger_cause": "检测到心率异常上升",
            "action_taken": "触发心脏状态分析",
            "result_summary": "判定为心率干扰影响",
        },
    }
    for rd, reqs in nightmare_awake_requests.items():
        health_row = health_rows_by_date.get(rd) or health_by_date.get(rd)
        if not isinstance(health_row, dict):
            continue
        sleep_start, sleep_end = sleep_local_window_bounds_from_sleep_data(health_row)
        if sleep_start is None or sleep_end is None:
            continue
        idf_data = health_row.get("idf_data")
        if not isinstance(idf_data, list) or not idf_data:
            continue
        handled_nightmare_ids = set()
        for req in sorted(reqs, key=lambda x: x["target_dt"]):
            target_dt = clamp_dt_to_sleep_window(req["target_dt"], sleep_start, sleep_end)
            awake_start_dt = target_dt
            awake_end_dt = clamp_dt_to_sleep_window(
                target_dt + timedelta(minutes=max(1, int(req["duration_min"]))),
                sleep_start,
                sleep_end,
            )
            idf_data = _insert_awake_into_idf(
                idf_data,
                target_dt,
                req["duration_min"],
            )
            health_updates += 1
            source_nightmare_id = str(req.get("source_nightmare_id") or "")
            if source_nightmare_id and source_nightmare_id in handled_nightmare_ids:
                continue
            if random.random() >= float(os.getenv("SLEEP_EVENTS_NIGHTMARE_LINKED_HR_PROB", "0.40")):
                continue
            # 与 generate_sleep_events 一致：联动心率上升仅落在噩床前 REM 窗内，时刻严格早于噩梦；无可用窗则跳过
            nightmare_anchor = clamp_dt_to_sleep_window(
                target_dt - timedelta(minutes=2.5), sleep_start, sleep_end
            )
            gmin = float(os.getenv("SLEEP_EVENTS_NIGHTMARE_HR_MIN_GAP_MIN", "1.25"))
            gmax = float(os.getenv("SLEEP_EVENTS_NIGHTMARE_HR_MAX_GAP_MIN", "5.0"))
            hr_slices = _rem_slices_before_nightmare_dt(
                nightmare_anchor, sleep_start, sleep_end, idf_data, gmin, gmax
            )
            if not hr_slices:
                hr_slices = _rem_slices_before_nightmare_dt(
                    nightmare_anchor,
                    sleep_start,
                    sleep_end,
                    idf_data,
                    gmin,
                    min(12.0, gmax + 7.0),
                )
            if not hr_slices:
                continue
            heart_event_dt = clamp_dt_to_sleep_window(
                _pick_random_dt_in_stage_windows(hr_slices, nightmare_anchor),
                sleep_start,
                sleep_end,
            )
            if heart_event_dt >= nightmare_anchor:
                continue
            if (
                _sleep_stage_at_event_anchor(heart_event_dt, idf_data, sleep_start, sleep_end)
                != "rem"
            ):
                continue
            hr_primary_n = sum(
                1
                for ev in sleep_events
                if ev.get("uid") == user_id
                and ev.get("record_date") == rd
                and ev.get("event_type") != "AI主动干预"
                and ev.get("code") == "heart_rate_increase"
            )
            # 每晚最多一条心率上升主事件
            if hr_primary_n < 1:
                heart_event = {
                    "uid": user_id,
                    "record_date": rd,
                    "event_timestamp": format_sleep_event_local_timestamp(heart_event_dt),
                    "event_type": heart_event_info["name"],
                    "type": heart_event_info["type"],
                    "code": "heart_rate_increase",
                    "detail": finalize_sleep_event_detail("heart_rate_increase", heart_event_info),
                    "related_event_id": "",
                    "sort_order": 0,
                    "create_time": generate_iso_date(),
                    "update_time": generate_iso_date(),
                    "duration_sec": _pick_sleep_event_duration_sec(
                        {"code": "heart_rate_increase", "event_type": heart_event_info["name"]}
                    ),
                    "_id": generate_object_id(),
                    "language": "zh",
                }
                sleep_events.append(heart_event)
                sleep_events_updates += 1
                ai_dt = clamp_dt_to_sleep_window(
                    heart_event_dt + timedelta(minutes=random.uniform(2.0, 3.0)),
                    sleep_start,
                    sleep_end,
                )
                ai_event = {
                    "uid": user_id,
                    "record_date": rd,
                    "event_timestamp": format_sleep_event_local_timestamp(ai_dt),
                    "event_type": "AI主动干预",
                    "type": "intervention",
                    "code": "heart_rate_increase",
                    "detail": build_ai_intervention_detail_for_abnormal(
                        "heart_rate_increase",
                        heart_event_info,
                        heart_event["detail"],
                    ),
                    "related_event_id": heart_event.get("_id") or "",
                    "sort_order": 0,
                    "create_time": generate_iso_date(),
                    "update_time": generate_iso_date(),
                    "duration_sec": _pick_sleep_event_duration_sec({"event_type": "AI主动干预"}),
                    "_id": generate_object_id(),
                    "language": "zh",
                }
                sleep_events.append(ai_event)
                sleep_events_ai_updates += 1
                if source_nightmare_id:
                    handled_nightmare_ids.add(source_nightmare_id)
            report_rows.append(
                {
                    "event_id": req["event_id"],
                    "event_idx": req["event_idx"],
                    "record_date": rd,
                    "event_time": req["event_time"],
                    "code": "nightmare",
                    "source": "health",
                    "field": "idf_data",
                    "status": "updated",
                    "reason": "inserted_awake_stage_for_nightmare_intervention",
                    "matched_row_time": target_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "new_value": f"awake+{int(req['duration_min'])}min",
                }
            )
        health_row["idf_data"] = idf_data

    if health_updates > 0 and isinstance(health_data, list) and health_data:
        strip_sleep_calendar_flags_from_health_records(health_data)
        with open(health_file, "w", encoding="utf-8") as f:
            json.dump(health_data, f, ensure_ascii=False, indent=2)

    if (sleep_events_updates + sleep_events_ai_updates + sleep_events_nightmare_time_updates) > 0:
        sleep_events.sort(
            key=lambda e: (
                e.get("record_date") or "",
                session_anchor_event_local_dt(e, None, None),
                str(e.get("event_type") or ""),
            )
        )
        current_date = None
        order = 0
        for event in sleep_events:
            event_date = event.get("record_date")
            if event_date != current_date:
                current_date = event_date
                order = 0
            event["sort_order"] = order
            event["language"] = event.get("language") or "zh"
            order += 1
        with open(sleep_events_file, "w", encoding="utf-8") as f:
            json.dump(sleep_events, f, ensure_ascii=False, indent=2)

    try:
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report_rows, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return {
        "fitness_updates": 0,
        "environment_updates": environment_updates,
        "vitals_updates": vitals_updates,
        "sleep_events_updates": sleep_events_updates,
        "sleep_events_ai_updates": sleep_events_ai_updates,
        "sleep_events_nightmare_time_updates": sleep_events_nightmare_time_updates,
        "skipped_by_delta": 0,
        "skipped_by_cap": 0,
        "skipped_by_priority": 0,
        "report_file": report_file,
    }

# 从环境数据文件中获取环境摘要
def generate_environment_summary(user_id, record_date, output_dir="output"):
    """从环境数据文件中获取环境摘要"""
    # 读取环境数据文件
    environment_file = os.path.join(output_dir, f"{user_id}_environment_data.json")
    if not os.path.exists(environment_file):
        # 如果文件不存在，生成默认数据
        return generate_default_environment_summary()
    
    try:
        with open(environment_file, 'r', encoding='utf-8') as f:
            environment_data = json.load(f)
    except Exception as e:
        print(f"读取环境数据文件时出错: {str(e)}")
        return generate_default_environment_summary()
    
    # 筛选指定日期的环境数据
    date_data = [item for item in environment_data if item.get('record_date') == record_date]
    if not date_data:
        # 如果没有该日期的数据，生成默认数据
        return generate_default_environment_summary()
    
    # 计算温度数据
    temperatures = [item.get('temperature') for item in date_data if 'temperature' in item]
    if temperatures:
        temperature_value = round(sum(temperatures) / len(temperatures))
        temperature_max = max(temperatures)
        temperature_min = min(temperatures)
        if 18 <= temperature_value <= 24:
            temperature_status = "最佳"
        elif (16 <= temperature_value < 18) or (24 < temperature_value <= 26):
            temperature_status = "良好"
        elif temperature_value < 16:
            temperature_status = "偏冷"
        else:
            temperature_status = "偏热"
    else:
        temperature_value = 22
        temperature_max = 24
        temperature_min = 21
        temperature_status = "最佳"
    
    # 计算湿度数据
    humidities = [item.get('humidity') for item in date_data if 'humidity' in item]
    if humidities:
        humidity_value = round(sum(humidities) / len(humidities))
        humidity_max = max(humidities)
        humidity_min = min(humidities)
        if 40 <= humidity_value <= 60:
            humidity_status = "最佳"
        elif (30 <= humidity_value < 40) or (60 < humidity_value <= 70):
            humidity_status = "良好"
        elif humidity_value < 30:
            humidity_status = "干燥"
        else:
            humidity_status = "潮湿"
    else:
        humidity_value = 45
        humidity_max = 50
        humidity_min = 30
        humidity_status = "最佳"
    
    # 计算光照度数据
    illuminances = [item.get('illuminance') for item in date_data if 'illuminance' in item]
    if illuminances:
        illuminance_value = round(sum(illuminances) / len(illuminances))
        illuminance_max = max(illuminances)
        illuminance_min = min(illuminances)
        if illuminance_value < 5:
            illuminance_status = "最佳"
        elif 5 <= illuminance_value <= 20:
            illuminance_status = "偏亮"
        else:
            illuminance_status = "过亮"
    else:
        illuminance_value = 0
        illuminance_max = 5
        illuminance_min = 1
        illuminance_status = "最佳"
    
    # 计算噪音数据
    noises = [item.get('noise') for item in date_data if 'noise' in item]
    if noises:
        noise_value = round(sum(noises) / len(noises))
        noise_max = max(noises)
        noise_min = min(noises)
        if noise_value < 35:
            noise_status = "最佳"
        elif 35 <= noise_value <= 50:
            noise_status = "良好"
        elif 50 < noise_value <= 65:
            noise_status = "偏嘈杂"
        else:
            noise_status = "过载"
    else:
        noise_value = 35
        noise_max = 60
        noise_min = 20
        noise_status = "最佳"
    
    return {
        "temperature": {
            "value": temperature_value,
            "max": temperature_max,
            "min": temperature_min,
            "status": temperature_status
        },
        "humidity": {
            "value": humidity_value,
            "max": humidity_max,
            "min": humidity_min,
            "status": humidity_status
        },
        "illuminance": {
            "value": illuminance_value,
            "max": illuminance_max,
            "min": illuminance_min,
            "status": illuminance_status
        },
        "noise": {
            "value": noise_value,
            "max": noise_max,
            "min": noise_min,
            "status": noise_status
        }
    }

# 生成默认环境摘要
def generate_default_environment_summary():
    """生成默认环境摘要"""
    # 生成温度数据
    temperature_value = random.randint(18, 24)
    temperature_max = temperature_value + random.randint(0, 2)
    temperature_min = temperature_value - random.randint(0, 2)
    if 18 <= temperature_value <= 24:
        temperature_status = "最佳"
    elif (16 <= temperature_value < 18) or (24 < temperature_value <= 26):
        temperature_status = "良好"
    elif temperature_value < 16:
        temperature_status = "偏冷"
    else:
        temperature_status = "偏热"
    
    # 生成湿度数据
    humidity_value = random.randint(40, 60)
    humidity_max = humidity_value + random.randint(0, 5)
    humidity_min = humidity_value - random.randint(0, 5)
    if 40 <= humidity_value <= 60:
        humidity_status = "最佳"
    elif (30 <= humidity_value < 40) or (60 < humidity_value <= 70):
        humidity_status = "良好"
    elif humidity_value < 30:
        humidity_status = "干燥"
    else:
        humidity_status = "潮湿"
    
    # 生成光照度数据
    illuminance_value = random.randint(0, 20)
    illuminance_max = illuminance_value + random.randint(0, 5)
    illuminance_min = max(0, illuminance_value - random.randint(0, 1))
    if illuminance_value < 5:
        illuminance_status = "最佳"
    elif 5 <= illuminance_value <= 20:
        illuminance_status = "良好"
    else:
        illuminance_status = "过亮"
    
    # 生成噪音数据
    noise_value = random.randint(30, 50)
    noise_max = noise_value + random.randint(10, 30)
    noise_min = max(20, noise_value - random.randint(5, 15))
    if noise_value < 35:
        noise_status = "最佳"
    elif 35 <= noise_value <= 50:
        noise_status = "良好"
    elif 50 < noise_value <= 65:
        noise_status = "偏嘈杂"
    else:
        noise_status = "过载"
    
    return {
        "temperature": {
            "value": temperature_value,
            "max": temperature_max,
            "min": temperature_min,
            "status": temperature_status
        },
        "humidity": {
            "value": humidity_value,
            "max": humidity_max,
            "min": humidity_min,
            "status": humidity_status
        },
        "illuminance": {
            "value": illuminance_value,
            "max": illuminance_max,
            "min": illuminance_min,
            "status": illuminance_status
        },
        "noise": {
            "value": noise_value,
            "max": noise_max,
            "min": noise_min,
            "status": noise_status
        }
    }

# 生成AI建议
def generate_ai_evidence(sleep_data):
    """根据睡眠数据生成AI建议"""
    # 直接使用默认值，不调用API
    description = "系统监测到睡眠状态良好，建议保持当前作息习惯。"
    
    return [{
        "title": "Bio-OS 算法已进化",
        "description": description,
        "action_text": "一键应用并期待今晚"
    }]

# 从uploaded_images.json中根据file名称获取图片URL
def get_image_url_by_name(name):
    """根据称号从 uploaded_images.json 取 URL；配置里多为「称号.png」，兼容无后缀匹配。"""
    if not name:
        return ""
    json_path = os.path.join(PROJECT_ROOT, 'qiniu', 'uploaded_images.json')
    # 兼容历史目录结构，避免脚本被单独拷贝后路径失效。
    if not os.path.exists(json_path):
        legacy_path = os.path.join(os.path.dirname(__file__), 'qiniu', 'uploaded_images.json')
        json_path = legacy_path if os.path.exists(legacy_path) else json_path
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

# 生成 notice 字段
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
    # 统一生成可执行动作，不再使用“声/光/味：”设备日志式表达。
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


def generate_notice_via_qwen(
    sleep_data,
    user_id,
    record_date,
    sleep_events_for_date,
    environment_summary,
    personality_type,
    output_dir="output",
    prev_sleep_data=None,
):
    """
    与 preview_sleep_notice 一致：完整说明放在 system，user 仅要求 JSON；temperature=0.35。
    失败时返回 None，由调用方 fallback generate_notice。
    """
    if not sleepReportAI:
        return None
    sleep_metrics = _sleep_metrics_for_auditory_prompt(sleep_data)
    yesterday_block = build_notice_yesterday_sleep_block(
        user_id, record_date, output_dir=output_dir
    )
    env_samples = _environment_samples_for_auditory_prompt(
        user_id, record_date, max_n=80, output_dir=output_dir
    )
    auditory_events = _compact_sleep_events_for_auditory_prompt(
        sleep_events_for_date, max_n=80
    )
    system = render_prompt_template(
        "generate_health_data__notice.md",
        {
            "RECORD_DATE": record_date or "",
            "PERSONALITY_TYPE": personality_type,
            "YESTERDAY_SLEEP_BLOCK": yesterday_block,
            "SLEEP_METRICS_JSON": json.dumps(sleep_metrics, ensure_ascii=False),
            "ENVIRONMENT_SUMMARY_JSON": json.dumps(
                environment_summary or {}, ensure_ascii=False
            ),
            "ENVIRONMENT_SAMPLES_JSON": json.dumps(env_samples, ensure_ascii=False),
            "AUDITORY_EVENTS_JSON": json.dumps(auditory_events, ensure_ascii=False),
        },
    )
    if prev_sleep_data:
        prev_rd = str(prev_sleep_data.get("record_date") or "")
        prev_json = json.dumps(prev_sleep_data, ensure_ascii=False)
        system += f"\n\n前一日（{prev_rd}）睡眠数据：\n{prev_json}"

    user_msg = "请严格按系统说明仅输出一个 JSON 对象，不要 markdown 围栏或解释。"
    raw = call_qwen_api(
        user_msg,
        system_prompt=system,
        max_tokens=512,
        temperature=0.35,
        sleep_report_llm=True,
    )
    if not raw.strip():
        return None
    try:
        parsed = _parse_json_from_response(raw)
    except Exception as e:
        print(f"  [警告] 解析 notice 返回内容失败: {e}")
        return None
    if not isinstance(parsed, dict):
        return None
    if not str(parsed.get("content", "")).strip():
        return None
    return parsed


def generate_main_summary_via_qwen(
    sleep_data,
    sleep_events,
    main_title,
    fallback_summary,
    total_sleep_minutes,
    deep_percent,
    light_percent,
    rem_percent,
    sleep_latency,
    awake_percent,
    sleep_efficiency,
    body_battery,
    personality_type,
    night_wake_episodes,
):
    """
    与 preview_sleep_main_summary 一致：仅用 generate_health_data__main_summary_general.md
    作为 system，user 要求 JSON 含 title 与 summary；temperature=0.35。
    失败时返回 None，由调用方使用本地 fallback_summary。
    """
    if not sleepReportAI:
        return None
    event_lines = []
    for e in sleep_events or []:
        event_lines.append(
            {
                "time": e.get("event_timestamp", ""),
                "type": e.get("event_type", ""),
                "code": e.get("code", ""),
                "detail": e.get("detail", {}),
            }
        )

    sleep_data_for_prompt = {
        **sleep_data,
        "sleep_events": event_lines,
    }

    system = render_prompt_template(
        "generate_health_data__main_summary_general.md",
        {
            "SLEEP_LABEL": main_title,
            "SLEEP_DATA_JSON": json.dumps(sleep_data_for_prompt, ensure_ascii=False),
        },
    )
    user_msg = (
        '请严格依据 system 提示末尾「本次任务的真实输入」中的 JSON 作答；'
        '仅输出 JSON：{"title":"...","summary":"..."}；'
        "title 须与输入 JSON 顶层的 title 完全一致；"
        "summary 中的数字须来自该 JSON，勿照抄文档示例；"
        "不要 markdown 围栏或解释。"
    )
    raw_out = call_qwen_api(
        user_msg,
        system_prompt=system,
        max_tokens=512,
        temperature=0.35,
        sleep_report_llm=True,
    )
    if not raw_out.strip():
        return None
    try:
        parsed = _parse_json_from_response(raw_out)
    except Exception as e:
        print(f"  [警告] 解析 main.summary 返回内容失败: {e}")
        return None
    if not isinstance(parsed, dict):
        return None
    summary = str(parsed.get("summary", "")).strip()
    if not summary:
        return None
    parsed_title = str(parsed.get("title", "")).strip()
    canon = str(main_title or "").strip()
    if parsed_title and canon and parsed_title != canon:
        print(
            f"  [警告] main.summary 模型返回的 title「{parsed_title}」与主标题「{canon}」不一致，已忽略"
        )
    return summary


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


def _parse_json_from_response(content: str):
    """从模型返回内容中提取 JSON，兼容 markdown 代码块包裹"""
    content = content.strip()
    if content.startswith('```'):
        lines = content.splitlines()
        content = '\n'.join(lines[1:-1] if lines[-1].strip() == '```' else lines[1:])
    return json.loads(content.strip())


def generate_pain_point_module_via_qwen(sleep_data):
    """
    读取 prompt/sleep_pain_point_analysis_template.md，填入睡眠数据，
    调用通义千问生成 pain_point_analysis.module（JSON 数组，最多 3 条）。
    解析失败或 API 无响应时返回 None；无痛点时返回空列表 []。
    """
    if not sleepReportAI:
        return None
    instruction = load_prompt_instruction("sleep_pain_point_analysis_template.md")
    if not instruction:
        print("  [警告] 读取痛点分析模板失败或为空: sleep_pain_point_analysis_template.md")
        return None

    raw = sleep_data.get('raw_data', {})
    bed_time_local = utc_to_local(raw.get('bed_time', ''))
    wake_up_time_local = utc_to_local(raw.get('wake_up_time', ''))
    payload = {
        "sleep_data": {
            "record_date": sleep_data.get('record_date', ''),
            "apnea_count": raw.get('apnea_count', 0),
            "average_heartbeat": raw.get('average_heartbeat', 0),
            "average_respiration": raw.get('average_respiration', 0),
            "awake_ratio": raw.get('awake_ratio', 0),
            "deep_sleep_ratio": raw.get('deep_sleep_ratio', 0),
            "light_sleep_ratio": raw.get('light_sleep_ratio', 0),
            "rem_ratio": raw.get('rem_ratio', 0),
            "sleep_score": sleep_report_score_from_sleep_data(sleep_data),
            "total_sleep_minutes": raw.get('total_sleep_minutes', 0),
            "bed_time": format_time_to_hhmm(bed_time_local),
            "wake_up_time": format_time_to_hhmm(wake_up_time_local),
            "sleep_latency": raw.get('sleep_latency', 0),
            "sleep_efficiency": raw.get('sleep_efficiency', 0),
        },
        "schedule_data": [],
        "environment_data": [],
        "vitals_data": [],
    }

    prompt = (
        "以下为本晚真实输入数据（JSON）。请仅依据这些数据进行分析，"
        + "输出 JSON 数组：元素个数 0～3（不得超过 3；每项字段仅限 `target`、`description`），"
        + "与系统说明一致；不要附加任何解释。\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )

    result = call_qwen_api(
        prompt, system_prompt=instruction, max_tokens=4096, sleep_report_llm=True
    )
    if not result:
        return None
    parsed = None
    try:
        parsed = _parse_json_from_response(result)
    except Exception as e:
        print(f"  [警告] 解析痛点分析返回内容失败，尝试宽松提取: {e}")
    if parsed is None:
        parsed = _parse_model_json_array(result)
    if not isinstance(parsed, list):
        print("  [警告] 痛点分析模型返回不是 JSON 数组")
        return None
    normalized = _normalize_auditory_module_list(parsed)
    if normalized is None:
        return None
    return normalized[:3]


def generate_quality_module_via_qwen(sleep_data, *, temperature=None, top_p=None):
    """
    读取 prompt/sleep_quality_analysis_template.md，填入睡眠数据，
    调用通义千问生成 quality_analysis.module（dimensions 数组）。
    失败时返回 None。
    """
    if not sleepReportAI:
        return None
    instruction = load_prompt_instruction("sleep_quality_analysis_template.md")
    if not instruction:
        print("  [警告] 读取质量分析模板失败或为空: sleep_quality_analysis_template.md")
        return None

    raw = sleep_data.get('raw_data', {})
    bed_time_local = utc_to_local(raw.get('bed_time', ''))
    wake_up_time_local = utc_to_local(raw.get('wake_up_time', ''))
    payload = {
        "sleep_data": {
            "record_date": sleep_data.get('record_date', ''),
            "apnea_count": raw.get('apnea_count', 0),
            "average_heartbeat": raw.get('average_heartbeat', 0),
            "average_respiration": raw.get('average_respiration', 0),
            "awake_ratio": raw.get('awake_ratio', 0),
            "deep_sleep_ratio": raw.get('deep_sleep_ratio', 0),
            "light_sleep_ratio": raw.get('light_sleep_ratio', 0),
            "rem_ratio": raw.get('rem_ratio', 0),
            "sleep_score": sleep_report_score_from_sleep_data(sleep_data),
            "total_sleep_minutes": raw.get('total_sleep_minutes', 0),
            "bed_time": format_time_to_hhmm(bed_time_local),
            "wake_up_time": format_time_to_hhmm(wake_up_time_local),
            "sleep_latency": raw.get('sleep_latency', 0),
            "sleep_efficiency": raw.get('sleep_efficiency', 0),
        },
        "schedule_data": [],
        "environment_data": [],
        "vitals_data": [],
    }

    prompt = (
        "以下为本晚真实输入数据（JSON）。请仅依据这些数据进行分析，"
        + "返回仅包含 1 条元素的 JSON 数组（字段仅限 `target`、`description`），不要附加任何解释。\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )

    result = call_qwen_api(
        prompt,
        system_prompt=instruction,
        max_tokens=4096,
        temperature=temperature,
        top_p=top_p,
        sleep_report_llm=True,
    )
    if not result:
        return None
    parsed = None
    try:
        parsed = _parse_json_from_response(result)
    except Exception as e:
        print(f"  [警告] 解析质量分析返回内容失败，尝试宽松提取: {e}")
    if parsed is None:
        parsed = _parse_model_json_array(result)
    if not isinstance(parsed, list):
        print("  [警告] 质量分析模型返回不是 JSON 数组")
        return None
    normalized = _normalize_auditory_module_list(parsed)
    if normalized is None:
        return None
    if not normalized:
        return None
    return normalized[:1]


# 兼容旧脚本/管道中的函数名
generate_auditory_module_via_doubao = generate_auditory_module_via_qwen
generate_notice_via_doubao = generate_notice_via_qwen
generate_main_summary_via_doubao = generate_main_summary_via_qwen
generate_pain_point_module_via_doubao = generate_pain_point_module_via_qwen
generate_quality_module_via_doubao = generate_quality_module_via_qwen


# 生成睡眠报告
def generate_sleep_report(
    sleep_data,
    user_id,
    session_id,
    sleep_standard,
    personality_type='M-L-C',
    sleep_events_index=None,
    recent_titles=None,
):
    """根据睡眠数据生成睡眠报告"""
    raw_data = sleep_data['raw_data']
    record_date = sleep_data.get('record_date', '')
    
    # 计算总睡眠分钟数（净睡眠 TST：深+浅+REM，与 raw_data.total_sleep_minutes 一致）
    total_sleep_minutes = raw_data.get('total_sleep_minutes', 0)

    # sleep_structure：percent 为四段分钟饼图（和为100）；percent_of_net_sleep / percent_of_time_in_bed 与健康 raw 对齐。
    (
        awake_minutes,
        deep_sleep_minutes,
        light_sleep_minutes,
        rem_sleep_minutes,
        pie_awake_pct,
        pie_deep_pct,
        pie_light_pct,
        pie_rem_pct,
        aw_pct_tib,
        deep_pct_tst,
        light_pct_tst,
        rem_pct_tst,
    ) = sleep_report_structure_minutes_and_percents(sleep_data)
    
    # 计算睡眠结构状态
    def get_stage_status(value, standard):
        if isinstance(standard, list):
            # deep, light, rem
            min_val, max_val = standard
            if value < min_val:
                return "过低"
            elif value > max_val:
                return "过高"
            else:
                return "正常"
        else:
            # awake
            if value < standard:
                return "正常"
            else:
                return "过高"
    
    sleep_structure = {
        "awake": {
            "minutes": awake_minutes,
            "percent": pie_awake_pct,
            "status": get_stage_status(aw_pct_tib, sleep_standard.get('awake', 10)),
        },
        "rem_sleep": {
            "minutes": rem_sleep_minutes,
            "percent": pie_rem_pct,
            "percent_of_net_sleep": rem_pct_tst,
            "status": get_stage_status(rem_pct_tst, sleep_standard.get('rem', [20, 25])),
        },
        "light_sleep": {
            "minutes": light_sleep_minutes,
            "percent": pie_light_pct,
            "percent_of_net_sleep": light_pct_tst,
            "status": get_stage_status(light_pct_tst, sleep_standard.get('light', [45, 50])),
        },
        "deep_sleep": {
            "minutes": deep_sleep_minutes,
            "percent": pie_deep_pct,
            "percent_of_net_sleep": deep_pct_tst,
            "status": get_stage_status(deep_pct_tst, sleep_standard.get('deep', [20, 25])),
        },
    }
    
    # 计算睡眠质量相关指标
    bed_time = raw_data.get('bed_time', '')
    wake_up_time = raw_data.get('wake_up_time', '')
    sleep_latency = raw_data.get('sleep_latency', 0)
    sleep_efficiency = raw_data.get('sleep_efficiency', 0)
    wake_time = raw_data.get('wake_time', '')
    apnea_count = int(raw_data.get("apnea_count", 0) or 0)

    # 计算卧床时间
    time_in_bed_minutes = calculate_duration(bed_time, wake_up_time, record_date or None)
    
    # 计算醒后清醒时间
    awake_after_onset_minutes = calculate_duration(wake_time, wake_up_time, record_date or None)
    
    # 转换时间为本地时间并格式化
    bed_time_local = utc_to_local(bed_time)
    wake_up_time_local = utc_to_local(wake_up_time)
    bedtime_str = format_time_to_hhmm(bed_time_local)
    wake_up_time_str = format_time_to_hhmm(wake_up_time_local)
    
    # 生成身体电量
    body_battery = generate_body_battery(sleep_data, personality_type=personality_type)
    body_battery_status = get_body_battery_status(body_battery)

    # 当日睡眠事件（听觉分析模块与 main.summary 共用）
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

    # 听觉 audios：优先与已生成的睡眠事件（同睡眠窗内打鼾/梦话/咳嗽）一一对应
    auditory = generate_auditory(sleep_data, user_id=user_id, sleep_events_index=sleep_events_index)
    # 不在此阶段调用听觉大模型：snoring_analysis.data_points 依赖环境近邻等逻辑，
    # 仅在流水线 report_audios 中在 audios/time 与 data_points 全部就绪后再生成 module。
    auditory_snore_module = build_auditory_snore_module(auditory.get("audios", []), record_date)

    # 生成环境摘要（从环境数据文件中获取）
    environment_summary = generate_environment_summary(user_id, record_date)

    # 痛点 / 质量分析：仅 sleepReportAI 开启时走大模型（与 preview_sleep_* 一致）
    final_pain_module = generate_pain_point_module_via_qwen(sleep_data) or []
    final_quality_module = generate_quality_module_via_qwen(sleep_data) or []

    # 生成 main 字段：优先按当晚指标（含负向规则），正向称号在多候选时与近期记录做差异化
    night_wake_eps = night_wake_episodes_for_prompts(sleep_data)
    main_title = pick_main_title(
        personality_type,
        sleep_data,
        light_pct_tst,
        deep_pct_tst,
        rem_pct_tst,
        sleep_latency,
        sleep_efficiency,
        apnea_count,
        recent_titles=recent_titles,
        report_score=body_battery,
    )
    main_summary = build_main_local_summary(
        main_title,
        deep_pct_tst,
        light_pct_tst,
        rem_pct_tst,
        sleep_latency,
        night_wake_eps,
        total_sleep_minutes=total_sleep_minutes,
        sleep_efficiency=sleep_efficiency,
        awake_percent=aw_pct_tib,
        record_date=record_date,
    )
    main_url = get_main_title_image_url(main_title)

    ai_main_summary = generate_main_summary_via_qwen(
        sleep_data,
        date_sleep_events,
        main_title,
        main_summary,
        total_sleep_minutes,
        deep_pct_tst,
        light_pct_tst,
        rem_pct_tst,
        sleep_latency,
        aw_pct_tib,
        sleep_efficiency,
        body_battery,
        personality_type,
        night_wake_eps,
    )
    if ai_main_summary is not None:
        main_summary = ai_main_summary

    # 构建睡眠报告
    report = {
        "uid": user_id,
        "record_date": record_date,
        "main": {
            "title": main_title,
            "url": main_url,
            "summary": main_summary
        },
        "sleep_summary": {
            "body_battery": body_battery,
            "body_battery_status": body_battery_status,
            "total_minutes": total_sleep_minutes,
            "deep_sleep_minutes": deep_sleep_minutes,
            "avg_heart_rate": raw_data.get('average_heartbeat', 0),
            "avg_respiratory_rate": raw_data.get('average_respiration', 0)
        },
        "pain_point_analysis": {
            "module": final_pain_module,
            "environment_summary": environment_summary
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
                "awake_after_onset_minutes": awake_after_onset_minutes
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

    notice = generate_notice_via_qwen(
        sleep_data=sleep_data,
        user_id=user_id,
        record_date=record_date,
        sleep_events_for_date=date_sleep_events,
        environment_summary=environment_summary,
        personality_type=personality_type,
    )
    if not notice:
        notice = generate_notice(
            total_sleep_minutes,
            deep_pct_tst,
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

# 生成睡眠报告主函数
def generate_sleep_reports():
    """主函数，处理睡眠数据并生成报告"""
    # 加载配置文件
    config_file = 'config/config.json'
    if not os.path.exists(config_file):
        print(f"配置文件 {config_file} 不存在")
        return
    
    with open(config_file, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    sleep_standard = config.get('sleepStandard', {
        "deep": [15, 25],
        "light": [45, 60],
        "rem": [20, 25],
        "awake": 10
    })
    
    # 获取所有健康数据文件
    import glob
    health_data_files = glob.glob('output/*_health_data.json')
    
    print(f"找到 {len(health_data_files)} 个健康数据文件")
    for file in health_data_files:
        print(f"  - {file}")
    
    if not health_data_files:
        print("未找到健康数据文件")
        return
    
    # 为每个用户生成睡眠报告
    for i, health_data_file in enumerate(health_data_files):
        print(f"处理文件 {i+1}/{len(health_data_files)}: {health_data_file}")
        if not os.path.exists(health_data_file):
            print(f"睡眠数据文件 {health_data_file} 不存在")
            continue
        
        # 提取用户ID
        user_id = os.path.basename(health_data_file).replace('_health_data.json', '')
        
        # 检查睡眠报告是否已生成
        output_file = f'output/{user_id}_sleep_report.json'
        if os.path.exists(output_file):
            print(f"用户 {user_id} 的睡眠报告已生成，跳过")
            continue
        
        try:
            with open(health_data_file, 'r', encoding='utf-8') as f:
                sleep_data_list = json.load(f)
            
            if not sleep_data_list:
                print(f"文件 {health_data_file} 中没有睡眠数据")
                continue
            sleep_data_list.sort(key=lambda row: str(row.get("record_date") or ""))

            # 生成报告
            reports = []
            print(f"  用户ID: {user_id}")
            
            # 查找用户对应的session_id
            session_id = ''
            personality_type = 'M-L-C'
            for user in config.get('user_profiles', []):
                if user.get('user_id') == user_id:
                    session_id = user.get('session_id', '')
                    personality_type = user.get('personalInformation', {}).get('type', 'M-L-C')
                    break
            print(f"  Session ID: {session_id}")
            sleep_events_index = build_sleep_events_index(user_id)

            # 记录最近 2 条已用标签，用于 pick_main_title 多样化
            recent_titles_window = []

            # 处理所有睡眠数据
            for j, sleep_data in enumerate(sleep_data_list):
                if j % 5 == 0:  # 每处理5条数据打印一次进度
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
                    # 更新近期标签窗口（保留最近 2 条）
                    used_title = report.get("main", {}).get("title")
                    if used_title:
                        recent_titles_window.append(used_title)
                        if len(recent_titles_window) > 2:
                            recent_titles_window.pop(0)
                except Exception as e:
                    print(f"    处理第 {j+1} 条数据时出错: {str(e)}")
                    continue
            
            # 处理 sleep_talk 数据的重复问题
            reports = check_and_process_sleep_talk(reports)
            
            # 保存报告
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(reports, f, ensure_ascii=False, indent=2)
            
            print(f"  睡眠报告已生成并处理，保存到 {output_file}")
        except Exception as e:
            print(f"  处理文件 {health_data_file} 时出错: {str(e)}")
            import traceback
            traceback.print_exc()

# 生成真正的 MongoDB ObjectId
def generate_object_id():
    """生成一个真正的 MongoDB ObjectId"""
    return str(ObjectId())

# 模拟 MongoDB ISODate
def generate_iso_date():
    """生成一个 ISODate 格式的字符串"""
    return datetime.now().isoformat() + "Z"

# 模拟 MongoDB NumberInt
def number_int(value):
    """返回 NumberInt 格式的字符串"""
    return int(value)

# 检查和处理 sleep_talk 数据的重复问题
def check_and_process_sleep_talk(reports):
    """检查和处理睡眠报告中的 sleep_talk 数据重复问题"""
    # 加载 audio.json 数据
    audio_file = 'qiniu/audio.json'
    if not os.path.exists(audio_file):
        return reports
    
    try:
        with open(audio_file, 'r', encoding='utf-8') as f:
            audio_data = json.load(f)
    except Exception as e:
        print(f"读取audio.json文件时出错: {str(e)}")
        return reports
    
    # 按14天分组
    groups = []
    for i in range(0, len(reports), 14):
        groups.append(reports[i:i+14])
    
    # 处理每个组
    for group in groups:
        # 收集所有已使用的 url
        used_urls = set()
        
        # 第一次遍历：收集所有已使用的 url
        for report in group:
            sleep_talk = report.get('quality_analysis', {}).get('auditory', {}).get('sleep_talk', [])
            for item in sleep_talk:
                used_urls.add(item.get('url', ''))
        
        # 第二次遍历：检查并处理重复
        for report in group:
            sleep_talk = report.get('quality_analysis', {}).get('auditory', {}).get('sleep_talk', [])
            new_sleep_talk = []
            
            for item in sleep_talk:
                url = item.get('url', '')
                # 检查当前报告内是否有重复
                if url in [t.get('url', '') for t in new_sleep_talk]:
                    # 找到同类型的音频替换
                    new_item = find_replacement_audio(item, audio_data, used_urls)
                    if new_item:
                        new_sleep_talk.append(new_item)
                        used_urls.add(new_item.get('url', ''))
                elif url in used_urls:
                    # 找到同类型的音频替换
                    new_item = find_replacement_audio(item, audio_data, used_urls)
                    if new_item:
                        new_sleep_talk.append(new_item)
                        used_urls.add(new_item.get('url', ''))
                else:
                    new_sleep_talk.append(item)
                    used_urls.add(url)
            
            # 更新 sleep_talk 数据
            if 'quality_analysis' in report:
                if 'auditory' in report['quality_analysis']:
                    report['quality_analysis']['auditory']['sleep_talk'] = new_sleep_talk
                else:
                    report['quality_analysis']['auditory'] = {'sleep_talk': new_sleep_talk}
            else:
                report['quality_analysis'] = {'auditory': {'sleep_talk': new_sleep_talk}}
    
    # 再次检查，确保所有数据都不重复
    # 收集所有已使用的 url
    all_used_urls = set()
    for report in reports:
        sleep_talk = report.get('quality_analysis', {}).get('auditory', {}).get('sleep_talk', [])
        for item in sleep_talk:
            url = item.get('url', '')
            if url in all_used_urls:
                # 找到同类型的音频替换
                new_item = find_replacement_audio(item, audio_data, all_used_urls)
                if new_item:
                    # 替换当前 item
                    for i, t in enumerate(sleep_talk):
                        if t.get('url', '') == url:
                            sleep_talk[i] = new_item
                            all_used_urls.add(new_item.get('url', ''))
                            break
            else:
                all_used_urls.add(url)
    
    return reports

def find_replacement_audio(item, audio_data, used_urls):
    """找到同类型的音频替换"""
    url = item.get('url', '')
    
    # 确定音频类型
    audio_type = None
    if url in [a.get('url', '') for a in _flatten_audio_groups(audio_data.get('audioUrl', []))]:
        audio_type = 'audioUrl'
    elif url in [a.get('url', '') for a in _flatten_audio_groups(audio_data.get('sleepTalkingUrl', []))]:
        audio_type = 'sleepTalkingUrl'
    elif url in [a.get('url', '') for a in _flatten_audio_groups(audio_data.get('coughUrl', []))]:
        audio_type = 'coughUrl'
    
    if not audio_type:
        # 如果找不到类型，从所有音频中随机选择
        all_audio = []
        all_audio.extend(_flatten_audio_groups(audio_data.get('audioUrl', [])))
        all_audio.extend(_flatten_audio_groups(audio_data.get('sleepTalkingUrl', [])))
        all_audio.extend(_flatten_audio_groups(audio_data.get('coughUrl', [])))
        valid_audio = [a for a in all_audio if (a.get('duration_sec', 0) or 0) > 0 and a.get('url', '') not in used_urls]
        if valid_audio:
            return random.choice(valid_audio)
        return None
    
    # 从同类型音频中选择
    audio_list = _flatten_audio_groups(audio_data.get(audio_type, []))
    valid_audio = [a for a in audio_list if (a.get('duration_sec', 0) or 0) > 0 and a.get('url', '') not in used_urls]
    if valid_audio:
        return random.choice(valid_audio)
    
    # 如果同类型没有可用音频，从其他类型选择
    other_audio = []
    for key in ['audioUrl', 'sleepTalkingUrl', 'coughUrl']:
        if key != audio_type:
            other_audio.extend(_flatten_audio_groups(audio_data.get(key, [])))
    valid_other = [a for a in other_audio if (a.get('duration_sec', 0) or 0) > 0 and a.get('url', '') not in used_urls]
    if valid_other:
        return random.choice(valid_other)
    
    return None

# 生成 AI 分析数据
def _build_local_ai_analysis(record_date_str, sleep_seg, sched_seg):
    """
    根据睡眠和日程数据本地生成多样化的 AI 分析内容，不调用大模型。
    返回 (title, sleep_insight, schedule_insight)
    """
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

    energy_bucket = 0
    if avg_total >= 420:
        energy_bucket += 1
    if avg_deep >= 20:
        energy_bucket += 1
    if avg_efficiency >= 88:
        energy_bucket += 1
    if avg_awake <= 10:
        energy_bucket += 1
    if avg_latency <= 20:
        energy_bucket += 1
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

    def _safe_hhmm_to_hour(v):
        if not isinstance(v, str) or ":" not in v:
            return None
        try:
            return int(v.strip().split(":")[0])
        except (TypeError, ValueError):
            return None

    if sched_seg:
        total_events = len(sched_seg)
        total_duration = sum(int(r.get("duration_minutes", 0) or 0) for r in sched_seg)
        late_events = 0
        high_load_events = 0
        type_counter = {}
        for r in sched_seg:
            et = str(r.get("event_type", "other") or "other")
            type_counter[et] = type_counter.get(et, 0) + 1
            st_h = _safe_hhmm_to_hour(r.get("start_time"))
            if st_h is not None and st_h >= 21:
                late_events += 1
            if int(r.get("duration_minutes", 0) or 0) >= 90:
                high_load_events += 1
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


def _normalize_text_key(text):
    """用于去重的轻量归一化键。"""
    if not text:
        return ""
    return "".join(str(text).strip().lower().split())


def _make_title_candidates(record_date_str, avg_efficiency, avg_deep, avg_latency):
    """给去重冲突准备一组候选标题。"""
    seed = int(record_date_str.replace("-", ""))
    if avg_efficiency >= 90:
        pool = ["深睡节律在线", "恢复效率高位", "睡眠表现亮眼", "夜间修复充足"]
    elif avg_efficiency >= 80:
        pool = ["节律维持稳定", "睡眠状态平稳", "恢复曲线向好", "夜间修复达标"]
    else:
        pool = ["睡眠修复待补", "节律需要微调", "恢复效率偏弱", "夜间质量待升"]

    # 入睡维度再加一点变化
    if avg_latency > 30:
        pool += ["入睡节律偏慢", "睡前降速不足"]
    elif avg_latency <= 15:
        pool += ["入睡启动顺畅", "入睡效率较高"]

    # 深睡维度再加一点变化
    if avg_deep < 15:
        pool += ["深睡储备不足", "深睡比例待提"]
    elif avg_deep >= 20:
        pool += ["深睡修复充沛", "深睡窗口充足"]

    # 轮转顺序，避免固定第一项
    shift = seed % len(pool)
    return pool[shift:] + pool[:shift]


def _dedupe_ai_text_with_memory(
    record_date_str,
    title,
    sleep_insight,
    schedule_insight,
    avg_efficiency,
    avg_deep,
    avg_latency,
    recent_title_keys,
    recent_text_keys,
    memory_size=7,
):
    """
    历史去重记忆：
    - 标题避免和最近 memory_size 天重复
    - sleep/schedule insight 组合避免和最近 memory_size 天完全重复
    """
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
        # 轻量改写，尽量不破坏原意
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


def _compact_sleep_records_for_trend_14d_prompt(sleep_seg):
    """自锚定日起向后 14 天窗口内的睡眠记录，供 sleep_trend_14d_analysis 提示词 JSON 使用。"""
    out = []
    for r in sorted(sleep_seg or [], key=lambda x: str(x.get("record_date") or "")):
        if not isinstance(r, dict):
            continue
        rd = r.get("raw_data") or {}
        out.append(
            {
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
            }
        )
    return out


def _compact_schedule_records_for_trend_14d_prompt(sched_seg):
    """自锚定日起向前 14 天窗口内的日程记录，供 sleep_trend_14d_analysis 提示词 JSON 使用。"""
    out = []
    for r in sorted(sched_seg or [], key=lambda x: str(x.get("event_date") or "")):
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "event_date": r.get("event_date"),
                "event_type": r.get("event_type"),
                "event_name": r.get("event_name"),
                "start_time": r.get("start_time"),
                "end_time": r.get("end_time"),
                "duration_minutes": r.get("duration_minutes"),
            }
        )
    return out


def generate_ai_analysis(
    user_id,
    use_doubao=False,
    output_dir="output",
    start_date=None,
    end_date=None,
    stream_output_file=None,
):
    """
    为用户每一天生成一条 AI 分析记录。
    每条记录以当天为起点，取当天起向后连续14天（含当天）的睡眠和日程数据。
    日程优先读取 output/{{user_id}}_calendar_events.json（与旧 schedule_data 列表结构一致），
    不存在时再回退读取 {{user_id}}_schedule_data.json。
    use_doubao=True 时读取 prompt/sleep_trend_14d_analysis.md，将 14 天睡眠与日程 JSON 附于提示词后调用大模型；
    模板缺失、无 API Key 或解析失败时回退为本地 _build_local_ai_analysis。
    use_doubao=False 时仅本地生成。
    返回分析结果列表，结构：
    [{ uid, record_date, title, sleep_insight, schedule_insight, create_time, update_time }, ...]
    """
    def _append_stream_row(row):
        if not stream_output_file:
            return
        rows = []
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

    # 读取睡眠数据
    sleep_records = []
    if os.path.exists(health_file):
        with open(health_file, 'r', encoding='utf-8') as f:
            sleep_records = json.load(f)
    else:
        print(f'  未找到睡眠数据文件: {health_file}')

    # 读取日程数据（与 calendar_events 同结构：列表项含 event_date 等；兼容旧版 schedule_data.json）
    schedule_records = []
    if os.path.exists(calendar_file):
        with open(calendar_file, 'r', encoding='utf-8') as f:
            schedule_records = json.load(f)
    elif os.path.exists(schedule_file_legacy):
        with open(schedule_file_legacy, 'r', encoding='utf-8') as f:
            schedule_records = json.load(f)
    else:
        print(f'  未找到日程数据文件: {calendar_file}（或旧版 {schedule_file_legacy}）')
    if isinstance(schedule_records, list):
        schedule_records = [r for r in schedule_records if isinstance(r, dict)]
    else:
        schedule_records = []

    if not sleep_records:
        print(f'  用户 {user_id} 无睡眠数据，跳过 AI 分析')
        return []

    start_dt = None
    end_dt = None
    if start_date:
        start_dt = datetime.strptime(str(start_date)[:10], "%Y-%m-%d")
    if end_date:
        end_dt = datetime.strptime(str(end_date)[:10], "%Y-%m-%d")

    # 按 record_date 排序，获取所有日期
    sleep_records.sort(key=lambda x: x.get('record_date', ''))
    all_sleep_dates = sorted(set(r['record_date'] for r in sleep_records))
    if start_dt or end_dt:
        filtered_dates = []
        for d in all_sleep_dates:
            try:
                d_dt = datetime.strptime(str(d)[:10], "%Y-%m-%d")
            except ValueError:
                continue
            if start_dt and d_dt < start_dt:
                continue
            if end_dt and d_dt > end_dt:
                continue
            filtered_dates.append(d)
        all_sleep_dates = filtered_dates
        if not all_sleep_dates:
            print(
                f"  用户 {user_id} 在 AI 分析日期窗口内无睡眠数据"
                f"（start={start_date or '-'}, end={end_date or '-'}），跳过"
            )
            return []

    # 建立日期 -> 睡眠记录 的映射
    sleep_by_date = {}
    for r in sleep_records:
        d = r['record_date']
        sleep_by_date.setdefault(d, []).append(r)

    # 建立日期 -> 日程记录 的映射
    schedule_by_date = {}
    for r in schedule_records:
        d = r.get('event_date', '')
        if d:
            schedule_by_date.setdefault(d, []).append(r)

    results = []
    total_to_write = len(all_sleep_dates)
    # 历史去重记忆（最近7天）
    recent_title_keys = []
    recent_text_keys = []

    for record_date_str in all_sleep_dates:
        record_date = datetime.strptime(record_date_str, '%Y-%m-%d')

        # 取 [record_date - 13天, record_date] 共14天的数据（以当天为起点向前回溯）
        window_dates = set()
        for offset in range(14):
            window_dates.add((record_date - timedelta(days=offset)).strftime('%Y-%m-%d'))

        sleep_seg = [r for d in window_dates for r in sleep_by_date.get(d, [])]
        sched_seg = [r for d in window_dates for r in schedule_by_date.get(d, [])]

        if not sleep_seg:
            continue
        # 数据不足14天，跳过模型调用
        if len(sleep_seg) < 14:
            continue

        # 先统一计算关键指标，供大模型提示词和去重策略共用
        avg_latency = sum(r['raw_data'].get('sleep_latency', 0) for r in sleep_seg) / len(sleep_seg)
        avg_deep = sum(r['raw_data'].get('deep_sleep_ratio', 0) for r in sleep_seg) / len(sleep_seg)
        avg_total = sum(r['raw_data'].get('total_sleep_minutes', 0) for r in sleep_seg) / len(sleep_seg)
        avg_efficiency = sum(r['raw_data'].get('sleep_efficiency', 0) for r in sleep_seg) / len(sleep_seg)
        avg_awake = sum(r['raw_data'].get('awake_ratio', 0) for r in sleep_seg) / len(sleep_seg)

        if use_doubao:
            period_start = (record_date - timedelta(days=13)).strftime('%Y-%m-%d')
            period_end = record_date_str
            # 先本地生成，作为解析失败或未配置 API 时的回退
            title, sleep_insight, schedule_insight = _build_local_ai_analysis(
                record_date_str, sleep_seg, sched_seg
            )
            template_path = os.path.join(PROMPT_DIR, "sleep_trend_14d_analysis.md")
            if os.path.exists(template_path) and _qwen_api_key():
                try:
                    with open(template_path, "r", encoding="utf-8") as f:
                        instruction = f.read().strip()
                except Exception as e:
                    print(f"  [警告] 读取 sleep_trend_14d_analysis 模板失败: {e}")
                    instruction = ""
                if instruction:
                    payload = {
                        "anchor_record_date": record_date_str,
                        "analysis_period": {"start": period_start, "end": period_end},
                        "sleep_records_14d": _compact_sleep_records_for_trend_14d_prompt(sleep_seg),
                        "schedule_records_14d": _compact_schedule_records_for_trend_14d_prompt(sched_seg),
                    }
                    prompt = (
                        "以下为真实输入数据（JSON）。请仅依据这些数据输出 JSON 对象，"
                        + "字段必须为 `title`、`sleep_insight`、`schedule_insight`，不要附加解释文本。"
                        + "不要照抄文档中的示例数值与文案。\n\n"
                        + json.dumps(payload, ensure_ascii=False)
                    )
                    insight_temp = float(
                        os.getenv("QWEN_AI_ANALYSIS_TEMPERATURE", os.getenv("DOUBAO_AI_ANALYSIS_TEMPERATURE", "0.82"))
                    )
                    insight_temp = min(1.0, max(0.0, insight_temp))
                    api_result = call_qwen_api(
                        prompt,
                        system_prompt=instruction,
                        max_tokens=2048,
                        temperature=insight_temp,
                    )
                    if api_result:
                        try:
                            content = api_result.strip()
                            if content.startswith("```"):
                                parts = content.split("```")
                                content = parts[1] if len(parts) > 1 else content
                                if str(content).lstrip().lower().startswith("json"):
                                    content = str(content).lstrip()[4:].lstrip()
                            content = str(content).strip()
                            lb = content.find("{")
                            rb = content.rfind("}")
                            if lb != -1 and rb != -1 and rb > lb:
                                content = content[lb : rb + 1]
                            parsed = json.loads(content)
                            if isinstance(parsed, dict):
                                title = str(parsed.get("title", title) or title).strip() or title
                                sleep_insight = str(
                                    parsed.get("sleep_insight", sleep_insight) or sleep_insight
                                ).strip() or sleep_insight
                                schedule_insight = str(
                                    parsed.get("schedule_insight", schedule_insight)
                                    or schedule_insight
                                ).strip() or schedule_insight
                        except Exception as e:
                            print(f"  解析模型返回内容失败: {e}，使用本地 AI 分析结果")
            else:
                if not os.path.exists(template_path):
                    print(f"  [警告] 未找到模板 {template_path}，使用本地 AI 分析")
                elif not _qwen_api_key():
                    print("  [警告] 未配置 DASHSCOPE_API_KEY，使用本地 AI 分析")
        else:
            title, sleep_insight, schedule_insight = _build_local_ai_analysis(
                record_date_str, sleep_seg, sched_seg
            )

        # 历史去重记忆：避免连续多天标题/描述撞车
        title, sleep_insight, schedule_insight = _dedupe_ai_text_with_memory(
            record_date_str,
            title,
            sleep_insight,
            schedule_insight,
            avg_efficiency,
            avg_deep,
            avg_latency,
            recent_title_keys,
            recent_text_keys,
            memory_size=7,
        )

        now_iso = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
        results.append({
            "uid": user_id,
            "record_date": record_date_str,
            "title": title,
            "sleep_insight": sleep_insight,
            "schedule_insight": schedule_insight,
            "create_time": now_iso,
            "update_time": now_iso,
            "language": "zh",
        })
        _append_stream_row(results[-1])
        print(f"  [{user_id}] AI分析写入进度：{len(results)}/{total_to_write}")

        print(f'  [{user_id}] {record_date_str} AI分析完成: {title}')

    return results


def generate_ai_analysis_all(use_doubao=None):
    """为所有用户生成 AI 分析，保存到 output/{user_id}_ai_analysis.json"""
    if use_doubao is None:
        use_doubao = sleepAIInsights
    config_file = 'config/config.json'
    if not os.path.exists(config_file):
        print(f'配置文件 {config_file} 不存在')
        return

    with open(config_file, 'r', encoding='utf-8') as f:
        config = json.load(f)

    users = config.get('user_profiles', [])
    print(f'共 {len(users)} 个用户，开始生成 AI 分析...')

    for user in users:
        user_id = user.get('user_id', '')
        if not user_id:
            continue
        output_file = f'output/{user_id}_ai_analysis.json'
        if os.path.exists(output_file):
            print(f'用户 {user_id} 的 AI 分析已存在，跳过')
            continue
        print(f'处理用户: {user_id}')
        results = generate_ai_analysis(user_id, use_doubao=use_doubao)
        if results:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f'  已保存 {len(results)} 条到 {output_file}')


def _environment_centers_from_local_clock(dt_local):
    """
    《固定时段环境变化.md》本地时钟分段：返回 (温度中心℃, 湿度中心%, 光照中心lx, 噪声中心dB)。
    采样点仍须落在入睡～起床窗内；仅按该时刻墙钟映射到对应时段特征。
    """
    h = dt_local.hour + dt_local.minute / 60.0 + dt_local.second / 3600.0
    # 21:00–23:00 入睡准备
    if 21.0 <= h < 23.0:
        return (24.2, 48.0, 180.0, 52.0)
    # 23:00–02:00 入睡与深睡建立
    if h >= 23.0 or h < 2.0:
        return (20.0, 54.0, 2.5, 36.0)
    # 02:00–05:00 夜间稳定
    if 2.0 <= h < 5.0:
        return (18.6, 60.0, 0.5, 26.0)
    # 05:00–07:00 觉醒启动
    if 5.0 <= h < 7.0:
        return (19.5, 54.0, 55.0, 42.0)
    # 07:00–09:00 起床过渡（室内透帘晨光，实测约 100–400 lx）
    if 7.0 <= h < 9.0:
        return (22.5, 46.0, 250.0, 62.0)
    # 其余白天时段（若偶发落入窗内）
    return (23.5, 50.0, 200.0, 45.0)


def _idf_awake_windows_minutes(idf_data):
    """
    从 idf_data 中提取所有 awake 阶段的 (start_min, end_min) 列表（分钟数，从0点起，支持跨午夜）。
    """
    windows = []
    for seg in (idf_data or []):
        if seg.get("stage") != "awake":
            continue
        try:
            sh, sm = map(int, seg["start"].split(":"))
            eh, em = map(int, seg["end"].split(":"))
            windows.append((sh * 60 + sm, eh * 60 + em))
        except Exception:
            pass
    return windows


def _raw_awake_windows_minutes(raw_data):
    """
    从 raw_data 提取卧床清醒与觉后清醒窗口（分钟数，从0点起，支持跨午夜）。
    用于补齐 idf_data 未覆盖的 awake 时段（如 bed_time->sleep_time）。
    """
    out = []
    raw_data = raw_data or {}
    bed_local = _parse_utc_iso_to_local_dt(raw_data.get("bed_time", ""))
    sleep_local = _parse_utc_iso_to_local_dt(raw_data.get("sleep_time", ""))
    wake_local = _parse_utc_iso_to_local_dt(raw_data.get("wake_time", ""))
    wake_up_local = _parse_utc_iso_to_local_dt(raw_data.get("wake_up_time", ""))

    if bed_local and sleep_local and sleep_local > bed_local:
        out.append((bed_local.hour * 60 + bed_local.minute, sleep_local.hour * 60 + sleep_local.minute))
    if wake_local and wake_up_local and wake_up_local > wake_local:
        out.append((wake_local.hour * 60 + wake_local.minute, wake_up_local.hour * 60 + wake_up_local.minute))
    return out


def _is_in_awake_window(dt_local, awake_windows):
    """判断本地时刻是否落在任一清醒阶段窗口内。"""
    m = dt_local.hour * 60 + dt_local.minute
    for s, e in awake_windows:
        if s <= e:
            if s <= m <= e:
                return True
        else:
            if m >= s or m <= e:
                return True
    return False


def _sample_environment_metrics_sequence(
    collected_local_dts,
    user_config,
    onset_env_idx,
    noise_spike_idxs,
    awake_windows=None,
):
    """
    与采样时刻对齐的温度/湿度/光照/噪声：按《固定时段环境变化.md》墙钟分段为基准，
    辅以短时抖动与温湿弱耦合；再沿睡眠窗时间轴施加平滑的「先缓降后缓升」温湿包络（近似昼夜室内变化）；
    入睡困难索引附近升温略降湿；噪声尖峰保留。
    illuminance 规则：睡眠中 0-3 lux，清醒阶段 15% 概率出现 20-30 lux，其余仍为 0-3 lux。
    """
    n = len(collected_local_dts)
    if n <= 0:
        return []

    if user_config:
        tr = user_config.get("sleepTemperature", {"min": [18], "max": [22]})
        hr = user_config.get("sleepHumidity", {"min": [50], "max": [60]})
        ir = user_config.get("sleepIlluminance", {"min": [0], "max": [10]})
        t_lo, t_hi = int(tr["min"][0]), int(tr["max"][0])
        h_lo, h_hi = int(hr["min"][0]), int(hr["max"][0])
        i_lo, i_hi = int(ir["min"][0]), int(ir["max"][0])
        ptype = user_config.get("personalInformation", {}).get("type", "M-L-C")
    else:
        t_lo, t_hi = 18, 26
        h_lo, h_hi = 40, 65
        i_lo, i_hi = 0, 20
        ptype = "M-L-C"

    t_mid = (t_lo + t_hi) / 2.0
    sorted_idx = sorted(range(n), key=lambda i: collected_local_dts[i])

    temps_f = [0.0] * n
    hum_f = [0.0] * n
    illum_out = [0] * n
    noise_f = [0.0] * n

    for k, i in enumerate(sorted_idx):
        dt = collected_local_dts[i]
        tc, hc, lc, nc = _environment_centers_from_local_clock(dt)
        temps_f[i] = tc + random.uniform(-1.1, 1.1)
        hum_f[i] = hc + random.uniform(-2.0, 2.0) - 0.35 * (temps_f[i] - tc)
        noise_f[i] = nc + random.uniform(-2.2, 2.2)
        # illuminance：睡眠中保持极暗（0-3 lux），清醒阶段 15% 概率出现短暂微亮（20-30 lux）
        if awake_windows and _is_in_awake_window(dt, awake_windows) and random.random() < 0.15:
            lux = float(random.randint(20, 30))
        else:
            lux = float(random.randint(0, 3))
        illum_out[i] = int(round(lux))

    for j in range(1, n):
        prev_i = sorted_idx[j - 1]
        cur_i = sorted_idx[j]
        alpha = 0.42
        temps_f[cur_i] = alpha * temps_f[cur_i] + (1 - alpha) * temps_f[prev_i] + random.uniform(-0.35, 0.35)
        hum_f[cur_i] = alpha * hum_f[cur_i] + (1 - alpha) * hum_f[prev_i] - 0.22 * (temps_f[cur_i] - temps_f[prev_i])
        noise_f[cur_i] = 0.55 * noise_f[cur_i] + 0.45 * noise_f[prev_i] + random.uniform(-1.2, 1.2)

    # 沿本地时间先后：前半窗缓慢降温、后半窗缓慢回升（昼夜室内温湿观感）；与墙钟分段叠加
    if n >= 2:
        amp_t = random.uniform(1.1, 2.3)
        amp_t_rise = random.uniform(0.75, 1.55)
        amp_h = random.uniform(2.0, 5.0)
        amp_h_rise = random.uniform(0.8, 2.2)
        for j, i in enumerate(sorted_idx):
            u = j / float(n - 1)
            if u <= 0.5:
                leg = 2.0 * u
                w_down = math.sin((math.pi / 2.0) * leg) ** 2
                temps_f[i] += -amp_t * w_down
                hum_f[i] += amp_h * w_down
            else:
                leg = 2.0 * (u - 0.5)
                w_up = math.sin((math.pi / 2.0) * leg) ** 2
                temps_f[i] += -amp_t * (1.0 - w_up) + amp_t_rise * w_up
                hum_f[i] += amp_h * (1.0 - w_up) - amp_h_rise * w_up

    if onset_env_idx is not None and 0 <= onset_env_idx < n:
        peak = random.uniform(27.5, 31.0)
        for i in range(n):
            dist = abs(i - onset_env_idx)
            blend = max(0.0, 1.0 - min(dist / 3.5, 1.0))
            temps_f[i] = temps_f[i] * (1.0 - blend) + peak * blend
            hum_f[i] -= blend * random.uniform(3.0, 10.0)

    nr = get_noise_range(ptype)
    n_lo, n_hi = int(nr["min"]), int(nr["max"])
    for i in range(n):
        noise_f[i] = max(float(n_lo) - 2.0, min(float(n_hi) + 16.0, noise_f[i]))

    for si in noise_spike_idxs:
        if not (0 <= si < n):
            continue
        peak_n = float(random.randint(64, 84))
        for j in range(n):
            dist = abs(j - si)
            att = max(0.0, 1.0 - min(dist / 2.5, 1.0))
            add = (peak_n - noise_f[j]) * att
            if add > 0:
                noise_f[j] += add * (1.0 if j == si else 0.72)

    out = []
    for i in range(n):
        t_raw = temps_f[i]
        h_raw = hum_f[i]
        lux_raw = float(illum_out[i])
        # 睡眠环境：最大不超过 30 lux
        lux_c = max(0.0, min(30.0, lux_raw))
        t_c = max(float(t_lo) - 0.5, min(float(t_hi) + 4.0, t_raw))
        h_c = max(float(h_lo) - 2.0, min(float(h_hi) + 6.0, h_raw))
        out.append(
            (
                int(round(max(15.0, min(34.0, t_c)))),
                int(round(max(25.0, min(78.0, h_c)))),
                int(round(lux_c)),
                int(round(max(18.0, min(92.0, noise_f[i])))),
            )
        )
    return out


def _build_noise_category_plan(total_days):
    """
    构建按天噪音类别计划（近似配比）：
    - best: 10%  (daily avg < 35)
    - good: 50%  (35-50)
    - noisy: 30% (50-65]
    - overload: 10% (>65)
    """
    if total_days <= 0:
        return []

    ratio_items = [
        ("best", 0.10),
        ("good", 0.50),
        ("noisy", 0.30),
        ("overload", 0.10),
    ]
    raw = [(name, total_days * ratio) for name, ratio in ratio_items]
    base_counts = {name: int(math.floor(v)) for name, v in raw}
    used = sum(base_counts.values())
    remain = total_days - used

    if remain > 0:
        frac_sorted = sorted(raw, key=lambda x: (x[1] - math.floor(x[1])), reverse=True)
        idx = 0
        while remain > 0 and frac_sorted:
            name = frac_sorted[idx % len(frac_sorted)][0]
            base_counts[name] += 1
            idx += 1
            remain -= 1

    plan = []
    for name, _ in ratio_items:
        plan.extend([name] * max(0, int(base_counts.get(name, 0))))
    random.shuffle(plan)
    return plan[:total_days]


def _noise_avg_target_range_for_category(category):
    """返回噪音类别对应的「日均值目标区间」(min_avg, max_avg)。"""
    if category == "best":
        return (20, 34)
    if category == "good":
        return (35, 50)
    if category == "noisy":
        return (51, 65)
    if category == "overload":
        return (66, 82)
    return (35, 50)


def _apply_daily_noise_average_target(metrics_seq, category):
    """
    将当日环境序列的噪音均值拉到目标区间内，保持其他字段不变。
    """
    if not metrics_seq:
        return metrics_seq

    t_min, t_max = _noise_avg_target_range_for_category(category)
    n = len(metrics_seq)
    noises = [float(m[3]) for m in metrics_seq]
    cur_avg = sum(noises) / max(1, n)
    target_avg = random.uniform(float(t_min), float(t_max))
    shift = target_avg - cur_avg

    adjusted = []
    for i, item in enumerate(metrics_seq):
        t, h, lux, noise = item
        new_noise = int(round(max(18.0, min(92.0, float(noise) + shift + random.uniform(-1.0, 1.0)))))
        adjusted.append((t, h, lux, new_noise))

    def _avg(seq):
        return sum(x[3] for x in seq) / max(1, len(seq))

    # 兜底微调：离散取整后若均值越界，逐步回拉到区间边界内
    cur = _avg(adjusted)
    if cur < t_min:
        need = int(math.ceil((t_min - cur) * n))
        i = 0
        while need > 0 and i < n * 6:
            idx = i % n
            t, h, lux, nv = adjusted[idx]
            if nv < 92:
                adjusted[idx] = (t, h, lux, nv + 1)
                need -= 1
            i += 1
    elif cur > t_max:
        need = int(math.ceil((cur - t_max) * n))
        i = 0
        while need > 0 and i < n * 6:
            idx = i % n
            t, h, lux, nv = adjusted[idx]
            if nv > 18:
                adjusted[idx] = (t, h, lux, nv - 1)
                need -= 1
            i += 1
    return adjusted


def _noise_category_from_avg(avg_noise):
    """按日均噪音值推断类别。"""
    try:
        v = float(avg_noise)
    except (TypeError, ValueError):
        return "good"
    if v < 35:
        return "best"
    if v <= 50:
        return "good"
    if v <= 65:
        return "noisy"
    return "overload"


def _rebalance_environment_noise_daily_after_feedback(environment_rows):
    """
    事件回填后，锁定事件命中的噪音点，仅调整非事件点，使日均值尽量回到既定类别区间。
    """
    if not environment_rows:
        return

    by_date = {}
    for row in environment_rows:
        if not isinstance(row, dict):
            continue
        rd = row.get("record_date")
        if not rd:
            continue
        by_date.setdefault(rd, []).append(row)

    for _rd, rows in by_date.items():
        if not rows:
            continue

        categories = [str(r.get("noise_daily_category") or "").strip() for r in rows]
        categories = [c for c in categories if c]
        if categories:
            day_category = max(set(categories), key=categories.count)
        else:
            cur_avg = sum(float(r.get("noise", 0) or 0) for r in rows) / max(1, len(rows))
            day_category = _noise_category_from_avg(cur_avg)

        t_min, t_max = _noise_avg_target_range_for_category(day_category)
        movable = [r for r in rows if r.get("noise_event_affected") is not True]
        if not movable:
            continue

        def _row_noise(row):
            try:
                return int(round(float(row.get("noise", 0) or 0)))
            except (TypeError, ValueError):
                return 35

        total_n = len(rows)
        cur_sum = sum(_row_noise(r) for r in rows)
        cur_avg = cur_sum / max(1, total_n)
        if t_min <= cur_avg <= t_max:
            continue

        target_avg = float(t_min if cur_avg < t_min else t_max)
        need = int(round(target_avg * total_n - cur_sum))
        if need == 0:
            continue

        step = 1 if need > 0 else -1
        remain = abs(need)
        safety = 0
        while remain > 0 and safety < len(movable) * 150:
            idx = safety % len(movable)
            row = movable[idx]
            nv = _row_noise(row)
            if step > 0 and nv < 92:
                row["noise"] = nv + 1
                remain -= 1
            elif step < 0 and nv > 18:
                row["noise"] = nv - 1
                remain -= 1
            safety += 1


class HealthDataGenerator:
    def __init__(self, config_file=None, generate_config=False):
        print("12312312",generate_config)

        self.config_file = config_file or os.getenv('CONFIG_FILE', 'config/config.json')
        self.output_dir = os.getenv('OUTPUT_DIR', 'output')
        # 如果需要生成配置文件
        if generate_config:
            self._generate_config_with_qwen()
        
        # 加载配置文件
        self.users = self._load_config()
    
    def _generate_config_with_qwen(self):
        """使用通义千问（DashScope）生成配置文件数据"""
        print("正在调用通义千问生成配置文件数据...")
        
        # 读取现有配置文件
        if not os.path.exists(self.config_file):
            print(f"配置文件 {self.config_file} 不存在")
            return
        
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                original_config = json.load(f)
            
            # 解析配置文件结构
            if isinstance(original_config, list):
                # 直接是用户数组
                user_profiles = original_config
                config_structure = 'list'
            else:
                # 标准结构，有user_profiles键
                user_profiles = original_config.get('user_profiles', [])
                config_structure = 'standard'
            
            user_count = len(user_profiles)
            if user_count == 0:
                print("配置文件中没有用户数据，无法生成")
                return
            
            user_ids = [user.get('user_id', '') for user in user_profiles]
            print(f"现有配置文件中有 {user_count} 个用户")
            
            # 构造提示词
            prompt = render_prompt_template(
                "generate_health_data__config_generation.md",
                {
                    "USER_PROFILES": str(user_profiles),
                    "USER_COUNT": str(user_count),
                    "USER_IDS": ", ".join(user_ids),
                },
            )
            
            # 调用 DashScope OpenAI 兼容接口
            api_key = _qwen_api_key()
            if not api_key:
                raise ValueError("DASHSCOPE_API_KEY 环境变量未设置（或兼容读取 DOUBAO_API_KEY）")
            
            model_name = _qwen_model_name()
            
            response = requests.post(
                _qwen_chat_url(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                },
                json={
                    "model": model_name,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    "temperature": 0.7,
                    "max_tokens": _QWEN_MAX_TOKENS,
                    "enable_thinking": False,
                },
                timeout=_QWEN_TIMEOUT,
            )
            
            # 解析响应
            response_data = response.json()
            
            # 检查是否有错误
            if 'error' in response_data:
                error_msg = response_data['error'].get('message', '未知错误')
                error_code = response_data['error'].get('code', '未知错误码')
                print(f"API错误: [{error_code}] {error_msg}")
                raise ValueError(f"通义千问 API 调用失败: {error_msg}")
            
            if 'choices' not in response_data or not response_data['choices']:
                print(f"错误：响应中没有'choices'字段，实际响应: {response_data}")
                raise ValueError("模型返回数据格式错误")
            
            content = response_data['choices'][0]['message']['content']
            
            # 清理可能的Markdown格式
            content = content.strip()
            if content.startswith('```json'):
                content = content[7:].strip()
            if content.endswith('```'):
                content = content[:-3].strip()
            
            # 解析JSON
            try:
                generated_data = json.loads(content)
                
                # 验证生成数据格式
                if not isinstance(generated_data, list):
                    raise ValueError("模型返回的数据不是JSON数组")
            except json.JSONDecodeError as e:
                print(f"JSON解析错误: {e}")
                print(f"原始响应内容: {content}")
                # 使用默认数据结构，不更新配置
                print("使用默认数据结构，跳过配置更新")
                return
            except Exception as e:
                print(f"解析数据时出错: {e}")
                # 使用默认数据结构，不更新配置
                print("使用默认数据结构，跳过配置更新")
                return
            
            # 创建用户ID到配置的映射
            user_map = {user.get('user_id'): user for user in user_profiles}
            
            # 更新每个用户的配置
            for generated_user in generated_data:
                user_id = generated_user.get('id')
                if user_id in user_map:
                    # 找到对应的用户并更新数据
                    user = user_map[user_id]
                    
                    # 保留原有的日期范围，不覆盖
                    # if 'date' in generated_user:
                    #     user['date'] = generated_user['date']
                    
                    # 更新睡眠和起床时间
                    if 'sleepTime' in generated_user:
                        sleep_time_data = generated_user['sleepTime']
                        user['sleepTime'] = {
                            'min': sleep_time_data.get('min', ['22:00']) if isinstance(sleep_time_data.get('min'), list) else [sleep_time_data.get('min', '22:00')],
                            'max': sleep_time_data.get('max', ['23:00']) if isinstance(sleep_time_data.get('max'), list) else [sleep_time_data.get('max', '23:00')]
                        }
                    
                    if 'awakeTime' in generated_user:
                        awake_time_data = generated_user['awakeTime']
                        user['awakeTime'] = {
                            'min': awake_time_data.get('min', ['07:00']) if isinstance(awake_time_data.get('min'), list) else [awake_time_data.get('min', '07:00')],
                            'max': awake_time_data.get('max', ['09:00']) if isinstance(awake_time_data.get('max'), list) else [awake_time_data.get('max', '09:00')]
                        }
                    
                    # 更新睡眠阶段比例范围
                    if 'deepSleep' in generated_user:
                        deep_sleep_data = generated_user['deepSleep']
                        user['deepSleep'] = {
                            'min': deep_sleep_data.get('min', [15]) if isinstance(deep_sleep_data.get('min'), list) else [deep_sleep_data.get('min', 15)],
                            'max': deep_sleep_data.get('max', [25]) if isinstance(deep_sleep_data.get('max'), list) else [deep_sleep_data.get('max', 25)]
                        }
                    
                    if 'awakeSleep' in generated_user:
                        awake_sleep_data = generated_user['awakeSleep']
                        user['awakeSleep'] = {
                            'min': awake_sleep_data.get('min', [5]) if isinstance(awake_sleep_data.get('min'), list) else [awake_sleep_data.get('min', 5)],
                            'max': awake_sleep_data.get('max', [15]) if isinstance(awake_sleep_data.get('max'), list) else [awake_sleep_data.get('max', 15)]
                        }
                    
                    if 'lightSleep' in generated_user:
                        light_sleep_data = generated_user['lightSleep']
                        user['lightSleep'] = {
                            'min': light_sleep_data.get('min', [45]) if isinstance(light_sleep_data.get('min'), list) else [light_sleep_data.get('min', 45)],
                            'max': light_sleep_data.get('max', [55]) if isinstance(light_sleep_data.get('max'), list) else [light_sleep_data.get('max', 55)]
                        }
                    
                    if 'remSleep' in generated_user:
                        rem_sleep_data = generated_user['remSleep']
                        user['remSleep'] = {
                            'min': rem_sleep_data.get('min', [20]) if isinstance(rem_sleep_data.get('min'), list) else [rem_sleep_data.get('min', 20)],
                            'max': rem_sleep_data.get('max', [25]) if isinstance(rem_sleep_data.get('max'), list) else [rem_sleep_data.get('max', 25)]
                        }
                    
                    # 更新体征信息
                    if 'fitness' in generated_user:
                        user['fitness'] = generated_user['fitness']
                    
                    # 更新睡眠环境信息
                    if 'sleepTemperature' in generated_user:
                        temp_data = generated_user['sleepTemperature']
                        user['sleepTemperature'] = {
                            'min': temp_data.get('min', [18]) if isinstance(temp_data.get('min'), list) else [temp_data.get('min', 18)],
                            'max': temp_data.get('max', [22]) if isinstance(temp_data.get('max'), list) else [temp_data.get('max', 22)]
                        }
                    
                    if 'sleepHumidity' in generated_user:
                        humidity_data = generated_user['sleepHumidity']
                        user['sleepHumidity'] = {
                            'min': humidity_data.get('min', [50]) if isinstance(humidity_data.get('min'), list) else [humidity_data.get('min', 50)],
                            'max': humidity_data.get('max', [60]) if isinstance(humidity_data.get('max'), list) else [humidity_data.get('max', 60)]
                        }
                    
                    if 'sleepIlluminance' in generated_user:
                        illuminance_data = generated_user['sleepIlluminance']
                        user['sleepIlluminance'] = {
                            'min': illuminance_data.get('min', [0]) if isinstance(illuminance_data.get('min'), list) else [illuminance_data.get('min', 0)],
                            'max': illuminance_data.get('max', [10]) if isinstance(illuminance_data.get('max'), list) else [illuminance_data.get('max', 10)]
                        }
                    
                    print(f"已更新用户 {user_id} 的配置")
                else:
                    print(f"未找到用户ID {user_id}，跳过更新")
            
            # 准备输出配置
            if config_structure == 'list':
                output_config = user_profiles
            else:
                output_config = {'user_profiles': user_profiles}
            
            # 写入配置文件
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(output_config, f, ensure_ascii=False, indent=2)
            
            print(f"配置文件已更新并保存到 {self.config_file}")
            
        except Exception as e:
            print(f"生成配置文件时出错：{e}")
            import traceback
            traceback.print_exc()
            print("使用现有配置文件继续...")
    
    def _load_config(self):
        """加载配置文件"""
        if not os.path.exists(self.config_file):
            raise FileNotFoundError(f"配置文件 {self.config_file} 不存在")
        
        with open(self.config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 检查配置文件结构
        if isinstance(config, list):
            # 直接是用户数组
            return config
        else:
            # 标准结构，有user_profiles键
            return config.get('user_profiles', [])
    
    def generate_sleep_data(self, user_id, user_profile, user, current_date=None, sleep_outlier_mode=None, prev_front_rem=False):
        """生成睡眠数据。sleep_outlier_mode 由批量生成按日期范围预计算：'bad' | 'good' | None。"""
        def _pick_config_int_range(field_name, default_min, default_max):
            """
            从用户配置里读取 {field_name: {min:[x], max:[y]}} 并采样整数。
            配置缺失或非法时，回退到默认区间。
            """
            cfg = user.get(field_name, {})
            try:
                min_v = int(cfg.get('min', [default_min])[0])
                max_v = int(cfg.get('max', [default_max])[0])
            except (TypeError, ValueError, IndexError):
                min_v, max_v = int(default_min), int(default_max)
            if min_v > max_v:
                min_v, max_v = max_v, min_v
            return random.randint(min_v, max_v)

        def _pick_time_in_range(base_dt, time_range):
            """从 time_range(min/max, HH:MM) 采样一个 datetime，支持跨午夜。"""
            if not time_range or 'min' not in time_range or 'max' not in time_range:
                return None
            min_h, min_m = map(int, time_range['min'][0].split(':'))
            max_h, max_m = map(int, time_range['max'][0].split(':'))
            start = base_dt.replace(hour=min_h, minute=min_m, second=0, microsecond=0)
            end = base_dt.replace(hour=max_h, minute=max_m, second=0, microsecond=0)
            if end < start:
                end += timedelta(days=1)
            total_min = int((end - start).total_seconds() // 60)
            return start + timedelta(minutes=random.randint(0, max(0, total_min)))

        def _pick_time_of_day(time_range):
            """从 time_range(min/max, HH:MM) 采样时分，支持跨午夜。"""
            if not time_range or 'min' not in time_range or 'max' not in time_range:
                return None
            min_h, min_m = map(int, time_range['min'][0].split(':'))
            max_h, max_m = map(int, time_range['max'][0].split(':'))
            min_total = min_h * 60 + min_m
            max_total = max_h * 60 + max_m
            if max_total >= min_total:
                picked = random.randint(min_total, max_total)
            else:
                # 跨午夜区间：拆成 [min, 1439] + [0, max]
                span1 = 1440 - min_total
                span2 = max_total + 1
                if random.randint(1, span1 + span2) <= span1:
                    picked = random.randint(min_total, 1439)
                else:
                    picked = random.randint(0, max_total)
            return picked // 60, picked % 60

        # 生成基础日期
        if current_date:
            # 使用传入的日期作为基础日期
            base_date = current_date
        else:
            # 生成随机基础日期（当前日期随机减去1-30天）
            base_date = datetime.now() - timedelta(days=random.randint(1, 30))
        
        # 解析用户配置的睡眠/起床时间范围
        sleep_time_range = user.get('sleepTime', None)
        awake_time_range = user.get('awakeTime', None)

        # 生成入睡时间（优先 sleepTime 配置）
        sleep_time = _pick_time_in_range(base_date, sleep_time_range)
        if sleep_time is None:
            sleep_time = base_date.replace(
                hour=random.randint(22, 23),
                minute=random.randint(0, 59),
                second=0,
                microsecond=0,
            )
        
        # 生成记录日期：
        # - 配置驱动批量生成时，固定使用循环日期，避免跨午夜 sleepTime 产生重复 record_date
        # - 非批量场景（未传 current_date）再退回到入睡时间日期
        if current_date:
            record_date = current_date.strftime('%Y-%m-%d')
        else:
            record_date = sleep_time.strftime('%Y-%m-%d')
        
        # 根据人格编码获取入睡潜伏期
        personality_type = user.get('personalInformation', {}).get('type', 'M-L-C')
        # 高敏感+高活跃（H-R）好睡日：约 30% 由排期注入；该夜生理/分期参考同晨夜型的低敏低活跃好睡型
        good_hr_sleep_night = (
            sleep_outlier_mode == "good"
            and personality_type in HIGH_SENS_HIGH_ACTIVE_PERSONALITIES
        )
        _ch0 = (personality_type or "M-L-C").split("-")[0]
        physiology_personality = (
            f"{_ch0}-L-C" if good_hr_sleep_night else personality_type
        )
        default_sleep_latency = get_sleep_latency(physiology_personality)
        sleep_latency = _pick_config_int_range(
            'sleepLatency',
            default_sleep_latency,
            default_sleep_latency,
        )
        
        # 优先使用 awakeTime 生成起床时间；若缺失则按人格睡眠时长推导
        wake_time = None
        if awake_time_range and 'min' in awake_time_range and 'max' in awake_time_range:
            picked_hm = _pick_time_of_day(awake_time_range)
            if picked_hm is not None:
                h, m = picked_hm
                # 取“入睡之后最近的一次起床时段”，避免凌晨入睡时多加一天
                wake_time = sleep_time.replace(hour=h, minute=m, second=0, microsecond=0)
                if wake_time <= sleep_time:
                    wake_time += timedelta(days=1)

        if wake_time is None:
            # 净睡眠时长（分钟）= sleep_time 至 wake_time；由人格决定目标时长
            total_sleep_minutes = get_total_sleep_minutes(physiology_personality)
            wake_time = sleep_time + timedelta(minutes=total_sleep_minutes)

        total_sleep_minutes = minutes_between_datetimes(sleep_time, wake_time)
        # 兜底：异常时长回退到人格目标时长，避免出现 20+ 小时这类脏数据
        if total_sleep_minutes < 180 or total_sleep_minutes > 900:
            total_sleep_minutes = get_total_sleep_minutes(physiology_personality)
            wake_time = sleep_time + timedelta(minutes=total_sleep_minutes)

        pb = PERSONALITY_SLEEP_STAGE_RATIO_RANGES.get(
            physiology_personality, PERSONALITY_SLEEP_STAGE_RATIO_RANGES["M-L-C"]
        )

        # 根据人格编码生成各项睡眠指标（好睡 H-R 夜用 physiology_personality）
        apnea_count = get_apnea_count(physiology_personality)
        average_heartbeat = get_avg_heartbeat(physiology_personality)
        average_respiration = get_avg_respiration(physiology_personality)

        # 离床信息
        leave_bed_count, leave_bed_minutes = get_leave_bed_info(physiology_personality)

        profile = get_personality_profile(physiology_personality)

        # 离群夜：仅调整潜伏期/呼吸暂停/离床；阶段占比由下方 TIB 四段联合抽样与人格区间约束
        (
            _,
            _,
            _,
            sleep_latency,
            apnea_count,
            leave_bed_count,
            leave_bed_minutes,
            _,
        ) = apply_sleep_outlier_mode(
            personality_type,
            20,
            50,
            30,
            sleep_latency,
            apnea_count,
            leave_bed_count,
            leave_bed_minutes,
            sleep_outlier_mode,
            mutate_stage_ratios=False,
        )

        is_good_poor_sleep = (
            sleep_outlier_mode == "good"
            and personality_type in POOR_SLEEP_PERSONALITIES
        )
        # 非差睡人格（M-L-C 等）的离群坏睡夜：排期为 bad 时在此强化「总睡变短、清醒增多、深/REM 降、浅睡升」
        is_bad_other_sleep = (
            sleep_outlier_mode == "bad"
            and personality_type not in POOR_SLEEP_PERSONALITIES
        )
        # 离群好睡夜：净睡眠 7–8 小时、深睡占 SPT 不低于 18%、REM 不低于 20%（与下方分钟校验一致）
        if is_good_poor_sleep and wake_time is not None:
            target_tst = random.randint(420, 480)
            aw_pct_plan = random.randint(6, 11)
            new_spt = int(
                round(target_tst / max(0.001, (100 - aw_pct_plan) / 100.0))
            )
            new_spt = max(new_spt, target_tst + 12)
            new_spt = min(new_spt, 560)
            wake_time = sleep_time + timedelta(minutes=new_spt)
            total_sleep_minutes = minutes_between_datetimes(sleep_time, wake_time)
            if total_sleep_minutes < 180 or total_sleep_minutes > 900:
                total_sleep_minutes = new_spt

        if is_bad_other_sleep and wake_time is not None:
            cur_tst = int(minutes_between_datetimes(sleep_time, wake_time))
            if cur_tst > 330:
                red = random.randint(45, 105)
            elif cur_tst > 260:
                red = random.randint(28, 70)
            else:
                red = random.randint(15, 40)
            red = min(red, max(0, cur_tst - 200))
            if red > 0:
                wake_time = wake_time - timedelta(minutes=red)
                if wake_time <= sleep_time:
                    wake_time = sleep_time + timedelta(
                        minutes=max(200, min(cur_tst, 300) - red)
                    )
                total_sleep_minutes = int(
                    minutes_between_datetimes(sleep_time, wake_time)
                )

        # 卧床 TIB：上床 bed → 起床 wake_up；四段占比均为人格表 ∩ 用户 config（和为 100），清醒分钟 = TIB×清醒%。
        wake_up_delta = get_wake_after_sleep(physiology_personality)
        bed_time = sleep_time - timedelta(minutes=sleep_latency)
        wake_up_time = wake_time + timedelta(minutes=wake_up_delta)

        d_lo, d_hi = _intersect_cfg_pb(user, "deepSleep", pb["deep"][0], pb["deep"][1])
        l_lo, l_hi = _intersect_cfg_pb(user, "lightSleep", pb["light"][0], pb["light"][1])
        r_lo, r_hi = _intersect_cfg_pb(user, "remSleep", pb["rem"][0], pb["rem"][1])
        a_lo, a_hi = _intersect_cfg_pb(
            user, "awakeSleep", pb["awake"][0], pb["awake"][1]
        )
        a_lo = max(1, min(a_lo, 96))
        a_hi = max(a_lo, min(a_hi, 97))

        if is_good_poor_sleep:
            d_lo = max(18, d_lo)
            d_hi = max(d_hi, 26, d_lo)
            r_lo = max(20, r_lo)
            r_hi = max(r_hi, 28, r_lo)
            l_lo = min(l_lo, 34)
            l_hi = max(l_hi, 58)
            a_lo = max(1, min(a_lo, 8))
            a_hi = max(a_lo, min(a_hi, 12))

        if is_bad_other_sleep:
            # 深睡/REM 上界与下压，浅睡抬高，清醒占比区间整体上移（仍与 config 相交后的区间兼容）
            d_hi = max(d_lo + 4, min(d_hi, random.randint(16, 22)))
            d_lo = max(5, min(d_lo, d_hi - 5))
            r_hi = max(r_lo + 4, min(r_hi, random.randint(17, 23)))
            r_lo = max(6, min(r_lo, r_hi - 5))
            l_lo = max(46, l_lo + random.randint(0, 10))
            l_hi = min(88, max(l_hi, l_lo + 5, 52))
            if l_hi < l_lo + 4:
                l_hi = min(88, l_lo + 8)
            a_lo = max(8, a_lo + random.randint(2, 8))
            a_lo = min(a_lo, a_hi - 1)
            a_hi = min(34, max(a_hi, a_lo + random.randint(4, 10)))
            if a_hi < a_lo + 1:
                a_hi = min(34, a_lo + 2)

        aw_rt = d_rt = l_rt = r_rt = 1
        picked = False
        _quad_tries = 3200 if (is_good_poor_sleep or is_bad_other_sleep) else 1200
        for _ in range(_quad_tries):
            quad = _sample_four_spt_percents_bounded(
                a_lo, a_hi, d_lo, d_hi, l_lo, l_hi, r_lo, r_hi, min_awake=1
            )
            if quad is None:
                break
            aw_rt, d_rt, l_rt, r_rt = quad
            if is_good_poor_sleep:
                tib_try = minutes_between_datetimes(
                    bed_time, wake_time + timedelta(minutes=wake_up_delta)
                )
                m_aw_try = _tib_awake_minutes_from_percent(tib_try, aw_rt)
                tst_try = max(0, int(tib_try) - int(m_aw_try))
                if tst_try < 420 or tst_try > 480:
                    continue
                dp_tr, lp_tr, rp_tr = _normalize_three_int100(d_rt, l_rt, r_rt)
                md_tr, ml_tr, mr_tr = distribute_sleep_stage_minutes(
                    tst_try, dp_tr, lp_tr, rp_tr
                )
                if 100 * md_tr < 18 * tst_try or 100 * mr_tr < 20 * tst_try:
                    continue
            elif is_bad_other_sleep:
                tib_try = minutes_between_datetimes(
                    bed_time, wake_time + timedelta(minutes=wake_up_delta)
                )
                m_aw_try = _tib_awake_minutes_from_percent(tib_try, aw_rt)
                tst_try = max(0, int(tib_try) - int(m_aw_try))
                if tst_try < 1:
                    continue
                dp_tr, lp_tr, rp_tr = _normalize_three_int100(d_rt, l_rt, r_rt)
                md_tr, ml_tr, mr_tr = distribute_sleep_stage_minutes(
                    tst_try, dp_tr, lp_tr, rp_tr
                )
                # 坏睡夜：浅睡占 TST 偏高、深睡与 REM 偏低、清醒占比不过低
                if 100 * ml_tr < 46 * tst_try:
                    continue
                if 100 * md_tr > 24 * tst_try or 100 * mr_tr > 26 * tst_try:
                    continue
                if aw_rt < 9:
                    continue
            picked = True
            break

        if not picked:
            quad2 = _sample_four_spt_percents_bounded(
                a_lo, a_hi, d_lo, d_hi, l_lo, l_hi, r_lo, r_hi, min_awake=1, max_draws=50000
            )
            if quad2:
                aw_rt, d_rt, l_rt, r_rt = quad2
                picked = True

        if not picked:
            aw_rt = max(a_lo, min(a_hi, (a_lo + a_hi) // 2))
            d_rt = max(d_lo, min(d_hi, (d_lo + d_hi) // 2))
            l_rt = max(l_lo, min(l_hi, (l_lo + l_hi) // 2))
            r_rt = 100 - aw_rt - d_rt - l_rt
            for _ in range(80):
                if r_lo <= r_rt <= r_hi:
                    picked = True
                    break
                if r_rt < r_lo:
                    need = r_lo - r_rt
                    for _s in range(need):
                        d_rt, l_rt, r_rt, ok = _deduct_one_from_dlr_closest_to_lower_bound(
                            d_rt, l_rt, r_rt, d_lo, l_lo, r_lo
                        )
                        if not ok:
                            break
                        aw_rt += 1
                        if aw_rt > a_hi:
                            aw_rt = a_hi
                            break
                else:
                    need = r_rt - r_hi
                    for _s in range(need):
                        d_rt, l_rt, r_rt, ok = _add_one_to_dlr_closest_to_upper_bound(
                            d_rt, l_rt, r_rt, d_hi, l_hi, r_hi
                        )
                        if not ok:
                            break
                        aw_rt = max(a_lo, aw_rt - 1)
                r_rt = 100 - aw_rt - d_rt - l_rt
            if not picked:
                aw_rt, d_rt, l_rt, r_rt = a_lo, d_lo, l_lo, 100 - a_lo - d_lo - l_lo

        tib = int(minutes_between_datetimes(bed_time, wake_up_time))
        tib = max(1, tib)
        post_wake = int(
            minutes_between_datetimes(
                wake_time, wake_time + timedelta(minutes=wake_up_delta)
            )
        )
        post_wake = max(0, post_wake)

        aw_cfg = profile.get("night_awakenings", {"min": 0, "max": 1})
        na_cfg = user.get("nightAwakenings", {})
        if is_good_poor_sleep:
            # 好睡日不按用户 nightAwakenings 强约束，仅用当前节律人格（好睡 H-R 已为 *-L-C）画像
            min_aw = int(aw_cfg["min"])
            max_aw = int(aw_cfg["max"])
        else:
            try:
                min_aw = int(na_cfg.get("min", [aw_cfg["min"]])[0])
                max_aw = int(na_cfg.get("max", [aw_cfg["max"]])[0])
            except (TypeError, ValueError, IndexError):
                min_aw, max_aw = int(aw_cfg["min"]), int(aw_cfg["max"])
        if min_aw > max_aw:
            min_aw, max_aw = max_aw, min_aw
        awake_count = random.randint(int(min_aw), int(max_aw))
        awake_count = max(int(min_aw), min(int(max_aw), int(awake_count)))

        # 用户「夜间清醒总分钟」配置（入睡后中段 WASO），与 idf 中段 awake 对齐；好睡日不套用以免抬高清醒占比
        nw_lo, nw_hi = None, None
        if not is_good_poor_sleep:
            nwt_cfg = user.get("nightWakeTotalMinutes")
            if isinstance(nwt_cfg, dict) and nwt_cfg.get("min") and nwt_cfg.get("max"):
                try:
                    nw_lo = int(nwt_cfg["min"][0])
                    nw_hi = int(nwt_cfg["max"][0])
                except (TypeError, ValueError, IndexError):
                    nw_lo, nw_hi = None, None
            if nw_lo is not None and nw_hi is not None and nw_lo > nw_hi:
                nw_lo, nw_hi = nw_hi, nw_lo

        (
            m_aw,
            m_d,
            m_l,
            m_r,
            night_waso_total,
            aw_rt,
            d_rt,
            l_rt,
            r_rt,
        ) = _finalize_tib_stage_minutes_for_idf(
            tib,
            sleep_latency,
            post_wake,
            aw_rt,
            d_rt,
            l_rt,
            r_rt,
            a_lo,
            a_hi,
            d_lo,
            d_hi,
            l_lo,
            l_hi,
            r_lo,
            r_hi,
            awake_count,
            night_waso_cfg_lo=nw_lo,
            night_waso_cfg_hi=nw_hi,
        )

        total_sleep_minutes = int(m_d + m_l + m_r)
        deep_sleep_minutes = int(m_d)
        light_sleep_minutes = int(m_l)
        rem_minutes = int(m_r)
        awake_ratio = int(round(100.0 * float(m_aw) / float(tib)))
        tst_for_pct = max(1, total_sleep_minutes)
        deep_sleep_ratio, light_sleep_ratio, rem_ratio = _normalize_three_int100(
            int(round(100.0 * float(m_d) / float(tst_for_pct))),
            int(round(100.0 * float(m_l) / float(tst_for_pct))),
            int(round(100.0 * float(m_r) / float(tst_for_pct))),
        )

        # idf_data：深/浅/REM 总分钟与上面 TST 拆分一致
        idf_dp, idf_lp, idf_rp = _normalize_three_int100(
            deep_sleep_ratio, light_sleep_ratio, rem_ratio
        )
        idf_deep_minutes, idf_light_minutes, idf_rem_minutes = distribute_sleep_stage_minutes(
            total_sleep_minutes, idf_dp, idf_lp, idf_rp
        )

        idf_awake_events = awake_count if night_waso_total > 0 else 0

        tib_bed_to_wake = minutes_between_datetimes(bed_time, wake_time)
        awake_minutes_bed = tib_bed_to_wake - total_sleep_minutes
        if awake_minutes_bed < sleep_latency:
            awake_minutes_bed = sleep_latency

        bed_time_utc = bed_time - timedelta(hours=8)
        sleep_time_utc = sleep_time - timedelta(hours=8)
        wake_time_utc = wake_time - timedelta(hours=8)
        wake_up_time_utc = wake_up_time - timedelta(hours=8)

        time_in_bed_minutes = minutes_between_datetimes(bed_time, wake_up_time)
        inferred_sleep_efficiency = int(
            round(total_sleep_minutes / max(1, time_in_bed_minutes) * 100)
        )
        inferred_sleep_efficiency = min(100, max(50, inferred_sleep_efficiency))
        sleep_efficiency = _pick_config_int_range(
            'sleepEfficiency',
            inferred_sleep_efficiency,
            inferred_sleep_efficiency,
        )
        sleep_efficiency = min(100, max(50, sleep_efficiency))

        # 根据人格编码生成翻身次数（好睡 H-R 夜参考低敏低活跃）
        turnover_count = get_turnover_count(physiology_personality)

        idf_personality = (
            physiology_personality if good_hr_sleep_night else personality_type
        )
        idf_data = self._generate_sleep_stages(
            bed_time,
            sleep_time,
            wake_time,
            wake_up_time,
            sleep_latency,
            idf_light_minutes,
            idf_deep_minutes,
            idf_rem_minutes,
            idf_personality,
            night_awakening_count=idf_awake_events,
            night_waso_minutes=night_waso_total,
            prev_front_rem=prev_front_rem,
        )

        # 低敏感：idf 内超长浅睡已改标为深/REM，raw 三阶段占比按入睡—起床窗内 idf 实际统计对齐
        deep_ratio_out = deep_sleep_ratio
        light_ratio_out = light_sleep_ratio
        rem_ratio_out = rem_ratio
        _pc = (personality_type or "M-L-C").split("-")
        if len(_pc) > 1 and _pc[1] == "L":
            _tl_idf = _idf_timeline_from_stages(idf_data)
            _s0 = max(0, int(round((sleep_time - bed_time).total_seconds() / 60)))
            _w0 = max(0, int(round((wake_time - bed_time).total_seconds() / 60)))
            if _tl_idf and _w0 > _s0:
                _w0 = min(_w0, len(_tl_idf))
                _s0 = min(_s0, len(_tl_idf))
                _md_i, _ml_i, _mr_i = _tst_phase_minutes_in_timeline_window(
                    _tl_idf, _s0, _w0
                )
                _tst_i = _md_i + _ml_i + _mr_i
                if _tst_i > 0:
                    deep_ratio_out, light_ratio_out, rem_ratio_out = _normalize_three_int100(
                        int(round(100.0 * float(_md_i) / float(_tst_i))),
                        int(round(100.0 * float(_ml_i) / float(_tst_i))),
                        int(round(100.0 * float(_mr_i) / float(_tst_i))),
                    )

        # 构建数据结构
        sleep_data = {
            "user_id": user_id,
            "record_date": record_date,
            "timestamp": bed_time_utc.isoformat() + "Z",
            "dimension_type": "sleep_quality_analysis",
            "data_source": "health_monitor",
            "raw_data": {
                "apnea_count": apnea_count,
                "average_heartbeat": average_heartbeat,
                "average_respiration": average_respiration,
                "awake_ratio": awake_ratio,
                "deep_sleep_ratio": deep_ratio_out,
                "leave_bed_count": leave_bed_count,
                "leave_bed_minutes": leave_bed_minutes,
                "light_sleep_ratio": light_ratio_out,
                "rem_ratio": rem_ratio_out,
                "sleep_score": 0,
                "total_sleep_minutes": total_sleep_minutes,
                "turnover_count": turnover_count,
                "bed_time": bed_time_utc.isoformat() + "Z",
                "sleep_time": sleep_time_utc.isoformat() + "Z",
                "wake_time": wake_time_utc.isoformat() + "Z",
                "wake_up_time": wake_up_time_utc.isoformat() + "Z",
                "sleep_latency": sleep_latency,
                "sleep_efficiency": sleep_efficiency,
                "good_sleep_day": bool(sleep_outlier_mode == "good"),
                "bad_sleep_day": bool(sleep_outlier_mode == "bad"),
            },
            "idf_data": idf_data,
            "create_time": (datetime.now() - timedelta(hours=8)).isoformat() + "Z",
            "update_time": (datetime.now() - timedelta(hours=8)).isoformat() + "Z",
            "__v": 0
        }

        env_try = load_environment_rows_for_record_date(user_id, record_date, output_dir=self.output_dir)
        sleep_data["raw_data"]["sleep_score"] = calculate_rule_based_sleep_score(
            sleep_data, environment_rows=env_try
        )

        return sleep_data
    
    def generate_survey_answers(self, user_id, user_profile, user_preference):
        """生成问卷答案"""
        # 测试接口是否有问题
        try:
            import requests
            url = "https://bionode-test.fulai.tech/app/quiz/survey"
            params = {
                "code": somni_code,
                "format": "flat"
            }
            response = requests.get(url, params=params)
            print(f"API请求状态码: {response.status_code}")
            print(f"API响应内容: {response.text}")
        except Exception as e:
            print(f"API请求出错: {str(e)}")
        
        # 检查问卷数据是否已生成
        output_file = os.path.join(self.output_dir, f"{user_id}_survey_data.json")
        if os.path.exists(output_file):
            print(f"用户 {user_id} 的问卷数据已生成，跳过")
            # 读取并返回已生成的数据
            with open(output_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        # 加载配置文件中的问卷信息
        with open(self.config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 获取questionCode
        survey_code = config.get('questionCode', 'somni_vip')
        
        # 加载questionConfig.json中的问卷问题
        question_config_file = 'C:\\Users\\86176\\Desktop\\project\\somni_generater_user_data\\questionConfig.json'
        questions = []
        if os.path.exists(question_config_file):
            with open(question_config_file, 'r', encoding='utf-8') as f:
                question_config = json.load(f)
            # 获取问卷问题
            questions = question_config.get('questions', [])
        else:
            print(f"警告: {question_config_file} 文件不存在，跳过问卷生成")
        
        # 生成问卷答案
        try:
            answers_data = self._generate_survey_answers_with_qwen(user_profile, user_preference, questions)
            print(f"使用通义千问生成问卷答案成功，共{len(answers_data)}个问题")
        except Exception as e:
            print(f"使用通义千问生成问卷答案时出错：{e}，使用默认答案")
            # 使用默认答案
            answers_data = []
            for question in questions:
                options = question.get('options', [])
                if options:
                    # 根据input_type判断是单选还是多选
                    input_type = question.get('input_type', 'radio')
                    if input_type in ['radio', 'select']:
                        # 单选
                        selected_option = random.choice(options)['option_id']
                        answers_data.append({
                            "question_id": question.get('_id'),
                            "selected_options": [selected_option]
                        })
                    elif input_type == 'checkbox':
                        # 多选，随机选择1-2个选项
                        num_options = random.randint(1, min(2, len(options)))
                        selected_options = [option['option_id'] for option in random.sample(options, num_options)]
                        answers_data.append({
                            "question_id": question.get('_id'),
                            "selected_options": selected_options
                        })
            print(f"使用默认答案生成问卷答案，共{len(answers_data)}个问题")
        
        # 构建完整的问卷答案数据结构
        survey_answers = {
            "uid": user_id,
            "survey_id": "69a6a958c773dde99fc1734d",  # 固定
            "survey_code": survey_code,
            "language": "zh",  # 固定
            "answers": [],
            "create_time": datetime.utcnow().isoformat() + "Z",
            "update_time": datetime.utcnow().isoformat() + "Z",
        }
        
        # 构建answers数组
        for i, answer_data in enumerate(answers_data):
            selected_options = answer_data.get('selected_options', [])
            
            # 使用索引查找对应的问题
            if i < len(questions):
                question = questions[i]
                question_id = question.get('_id')
            else:
                continue
            
            # 构建option_snapshots
            option_snapshots = []
            for option_id in selected_options:
                option = next((o for o in question.get('options', []) if o.get('option_id') == option_id), None)
                if option:
                    option_snapshots.append({
                        "option_id": option.get('option_id'),
                        "option_text": option.get('option_text'),
                        "score": int(option.get('score', '0')),
                        "label_value": option.get('label_value', "")
                    })
            
            # 添加到answers数组
            answer_item = {
                "selected_options": selected_options,
                "question_id": question_id,
                "input_value": "",  # 为空
                "numeric_value": 0,  # 为0
                "title": question.get('title', ""),
                "tags": question.get('tags', []),
                "option_snapshots": option_snapshots
            }
            survey_answers['answers'].append(answer_item)
        
        return survey_answers
    
    def generate_quiz_result(self, user_id, survey_answers, user_profile, user_preference):
        """生成问卷答案的详细数据结构"""
        # 检查问卷结果数据是否已生成
        output_file = os.path.join(self.output_dir, f"{user_id}_quiz_result.json")
        if os.path.exists(output_file):
            print(f"用户 {user_id} 的问卷结果数据已生成，跳过")
            # 读取并返回已生成的数据
            with open(output_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        # 加载配置文件
        with open(self.config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # 找到对应用户的 personalInformation
        user = next((u for u in config.get('user_profiles', []) if u.get('user_id') == user_id), None)
        if not user:
            raise ValueError(f"未找到用户 {user_id}")
        
        personal_info = user.get('personalInformation', {})
        personality_id = personal_info.get('id')
        
        # 模拟人格信息数据（实际应该从数据库查询）
        personality_data = {
            "mhr_codes": personal_info.get('type', '').split('-'),
            "mhr_name": personal_info.get('label', ''),
            "comment_template": "策划师型人格，追求完美节律的你需要学会放松",
            "population_ratio": 2.4,
            "periods": [
                "睡前放松练习",
                "规律作息时间表",
                "环境优化建议"
            ],
            "character_3d_url": "https://raw.githubusercontent.com/AvatarAssets/somni-3d/main/MHR-planner.glb",
            "identity_name": "策划师",
            "indicator_descriptions": {
                "circadian": "晨型焦虑，早起即压力",
                "sensitivity": "高敏感，对环境刺激反应强烈",
                "brain_state": "高活跃（R-Rumination），睡前大脑停不下来",
                "atmosphere": "需要极致的安静与黑暗环境"
            },
            "share_config": {
                "share_title_template": f"我是「{personal_info.get('label', '')}」，你是哪种睡眠人格？",
                "share_desc_template": "完成 Somni 睡眠测评，发现你的专属助眠方案",
                "share_image_url": "",
                "share_summary_template": f"{personal_info.get('label', '')}：策划师型，追求极致节律"
            }
        }
        
        # 生成 analysis_insight 和 status_analysis
        analysis_insight = "追求极致节律，但也极易因压力辗转。节律早但精神紧绷，容易\"早起即焦虑\"。"
        status_analysis = "追求极致节律，容易早起即压力碎觉。"
        
        # 生成数据结构
        quiz_result = {
            "_id": generate_object_id(),
            "uid": user_id,
            "survey_id": generate_object_id(),
            "survey_code": somni_code,
            "answer_id": survey_answers.get('answer_id', generate_object_id()),
            "mhr_codes": personality_data.get('mhr_codes', []),
            "mhr_name": personality_data.get('mhr_name', ''),
            "atmosphere": "warm",
            "tags": [],
            "comment": personality_data.get('comment_template', ''),
            "indicators": {
                "circadian": number_int(0),
                "sensitivity": number_int(0),
                "brain_state": number_int(0),
                "atmosphere": number_int(0)
            },
            "analysis_insight": analysis_insight,
            "population_ratio": personality_data.get('population_ratio', 0),
            "improve_plan": personality_data.get('periods', []),
            "score_detail": {},
            "create_time": generate_iso_date(),
            "update_time": generate_iso_date(),
            "character_3d_url": personality_data.get('character_3d_url', ''),
            "identity_name": personality_data.get('identity_name', ''),
            "indicator_descriptions": personality_data.get('indicator_descriptions', {}),
            "share_config": personality_data.get('share_config', {}),
            "status_analysis": status_analysis
        }
        
        return quiz_result
    
    def generate_quiz_result_record(self, quiz_result_id, uid):
        """生成 quiz_result 数据结构"""
        # 检查问卷结果记录数据是否已生成
        output_file = os.path.join(self.output_dir, f"{uid}_quiz_result_record.json")
        if os.path.exists(output_file):
            print(f"用户 {uid} 的问卷结果记录数据已生成，跳过")
            # 读取并返回已生成的数据
            with open(output_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        quiz_result_record = {
            "_id": generate_object_id(),
            "uid": uid,
            "record_date": datetime.now().strftime('%Y-%m-%d'),
            "quiz_result_id": quiz_result_id,
            "survey_code": somni_code,
            "create_time": generate_iso_date(),
            "update_time": generate_iso_date()
        }
        
        return quiz_result_record
    
    def generate_environment_data(self, user_id, record_date=None):
        """生成环境数据"""
        # 检查环境数据是否已生成
        output_file = os.path.join(self.output_dir, f"{user_id}_environment_data.json")
        if os.path.exists(output_file):
            print(f"用户 {user_id} 的环境数据已生成，跳过")
            # 读取并返回已生成的数据
            with open(output_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        # 查找用户配置（likelyNightSleepEvents）
        user_config = None
        for user in self.users:
            if user.get('user_id') == user_id:
                user_config = user
                break
        allowed_codes = allowed_sleep_event_codes_from_profile(user_config)

        # 生成基础日期
        if record_date:
            # 使用传入的日期
            base_date = datetime.strptime(record_date, '%Y-%m-%d')
        else:
            # 生成随机基础日期（当前日期随机减去1-30天）
            base_date = datetime.now() - timedelta(days=random.randint(1, 30))
        
        # 生成记录日期
        record_date_str = base_date.strftime('%Y-%m-%d')
        
        # 生成多条数据
        num_records = 15
        environment_data_list = []

        # 以睡眠数据窗口（sleep_time -> wake_up_time，本地时间）约束 collected_at
        sleep_window_start = None
        sleep_window_end = None
        day_health = {}
        if record_date:
            health_by_date = _health_data_by_record_date(user_id)
            day_health = health_by_date.get(record_date) or {}
            raw_data = day_health.get('raw_data', {}) if isinstance(day_health, dict) else {}
            sleep_window_start, sleep_window_end = _extract_local_sleep_window(
                raw_data, start_key='bed_time', end_key='wake_up_time'
            )

        collected_local_dts = []
        onset_env_idx = None
        min_gap_min = MIN_VITAL_ENV_COLLECTED_AT_GAP_MINUTES
        if sleep_window_start and sleep_window_end:
            allow_sleep = _profile_allows_sleeping_difficulty(allowed_codes)
            if allow_sleep and num_records > 1:
                onset_t = sleep_window_start + timedelta(minutes=random.randint(0, 28))
                rest_start = onset_t + timedelta(minutes=min_gap_min)
                if rest_start < sleep_window_end:
                    tail = _sample_local_dts_sleep_window_spaced(
                        rest_start, sleep_window_end, num_records - 1, min_gap_minutes=min_gap_min
                    )
                    if len(tail) == num_records - 1:
                        collected_local_dts = [onset_t] + tail
                        onset_env_idx = 0
            if not collected_local_dts:
                collected_local_dts = _sample_local_dts_sleep_window_spaced(
                    sleep_window_start, sleep_window_end, num_records, min_gap_minutes=min_gap_min
                )
        else:
            g_min = MIN_VITAL_ENV_COLLECTED_AT_GAP_MINUTES
            collected_local_dts = [
                base_date + timedelta(hours=2) + timedelta(minutes=g_min * i)
                for i in range(num_records)
            ]

        idf_align = (
            (day_health.get("idf_data") or [])
            if isinstance(day_health, dict)
            else []
        )
        if idf_align and sleep_window_start and sleep_window_end:
            collected_local_dts = _align_collected_local_dts_last_to_idf_end(
                collected_local_dts, idf_align, sleep_window_start, sleep_window_end
            )

        noise_spike_idxs = set()
        if (
            _profile_allows_noise_spike_env(allowed_codes)
            and len(collected_local_dts) >= 2
        ):
            candidates = list(range(len(collected_local_dts)))
            if onset_env_idx is not None and onset_env_idx in candidates:
                candidates.remove(onset_env_idx)
            if candidates:
                noise_spike_idxs.update(random.sample(candidates, min(2, len(candidates))))

        awake_windows = _idf_awake_windows_minutes(idf_align) + _raw_awake_windows_minutes(raw_data)
        metrics_seq = _sample_environment_metrics_sequence(
            collected_local_dts, user_config, onset_env_idx, noise_spike_idxs,
            awake_windows=awake_windows,
        )
        # 单日场景按目标配比抽样一个等级，并约束该日噪音均值
        single_day_category = random.choices(
            ["best", "good", "noisy", "overload"],
            weights=[10, 50, 30, 10],
            k=1,
        )[0]
        metrics_seq = _apply_daily_noise_average_target(metrics_seq, single_day_category)

        idf_end_utc = None
        if idf_align and sleep_window_start and sleep_window_end:
            idf_end_utc = _idf_last_segment_end_utc_iso_z(
                idf_align, sleep_window_start, sleep_window_end
            )
        n_env = len(collected_local_dts)

        # 为每个时间点生成一条记录（collected_at 存 UTC，与本地采样时刻一一对应）
        for idx, collected_at_local in enumerate(collected_local_dts):
            temperature, humidity, illuminance, noise = metrics_seq[idx]

            # 生成创建时间和更新时间（收集时间后1秒，本地墙钟）
            create_time = collected_at_local + timedelta(seconds=1)
            collected_at_utc_z = local_naive_dt_to_utc_iso_z(collected_at_local)
            if idf_end_utc and n_env > 0 and idx == n_env - 1:
                collected_at_utc_z = idf_end_utc

            # 构建数据结构
            environment_data = {
                "uid": user_id,
                "session_id": "",
                "record_date": record_date_str,
                "collected_at": collected_at_utc_z,
                "temperature": temperature,
                "humidity": humidity,
                "illuminance": illuminance,
                "noise": noise,
                "device_id": "",
                "create_time": create_time.strftime('%Y-%m-%d %H:%M:%S'),
                "update_time": create_time.strftime('%Y-%m-%d %H:%M:%S')
            }

            environment_data_list.append(environment_data)
        
        return environment_data_list
    
    def generate_multiple_environment_data(self, user_id, start_date, end_date):
        """生成多个日期的环境数据"""
        # 检查环境数据是否已生成
        output_file = os.path.join(self.output_dir, f"{user_id}_environment_data.json")
        if os.path.exists(output_file):
            print(f"用户 {user_id} 的环境数据已生成，跳过")
            # 读取并返回已生成的数据
            with open(output_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        user_config = None
        for user in self.users:
            if user.get('user_id') == user_id:
                user_config = user
                break
        allowed_codes = allowed_sleep_event_codes_from_profile(user_config)

        health_by_date = _health_data_by_record_date(user_id)
        
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        
        environment_data_list = []
        current_date = start
        total_days = (end - start).days + 1
        daily_noise_plan = _build_noise_category_plan(total_days)
        day_idx = 0
        
        while current_date <= end:
            # 每天生成10条记录
            num_records = 10
            
            base_date = current_date
            record_date_str = base_date.strftime('%Y-%m-%d')

            day_health = health_by_date.get(record_date_str) or {}
            raw_data = day_health.get('raw_data', {}) if isinstance(day_health, dict) else {}
            sleep_window_start, sleep_window_end = _extract_local_sleep_window(
                raw_data, start_key='bed_time', end_key='wake_up_time'
            )

            collected_local_dts = []
            onset_env_idx = None
            min_gap_min = MIN_VITAL_ENV_COLLECTED_AT_GAP_MINUTES
            if sleep_window_start and sleep_window_end:
                allow_sleep = _profile_allows_sleeping_difficulty(allowed_codes)
                if allow_sleep and num_records > 1:
                    onset_t = sleep_window_start + timedelta(minutes=random.randint(0, 28))
                    rest_start = onset_t + timedelta(minutes=min_gap_min)
                    if rest_start < sleep_window_end:
                        tail = _sample_local_dts_sleep_window_spaced(
                            rest_start, sleep_window_end, num_records - 1, min_gap_minutes=min_gap_min
                        )
                        if len(tail) == num_records - 1:
                            collected_local_dts = [onset_t] + tail
                            onset_env_idx = 0
                if not collected_local_dts:
                    collected_local_dts = _sample_local_dts_sleep_window_spaced(
                        sleep_window_start, sleep_window_end, num_records, min_gap_minutes=min_gap_min
                    )
            else:
                g_min = MIN_VITAL_ENV_COLLECTED_AT_GAP_MINUTES
                collected_local_dts = [
                    base_date + timedelta(hours=2) + timedelta(minutes=g_min * i)
                    for i in range(num_records)
                ]

            idf_align = (
                (day_health.get("idf_data") or [])
                if isinstance(day_health, dict)
                else []
            )
            if idf_align and sleep_window_start and sleep_window_end:
                collected_local_dts = _align_collected_local_dts_last_to_idf_end(
                    collected_local_dts, idf_align, sleep_window_start, sleep_window_end
                )

            noise_spike_idxs = set()
            if (
                _profile_allows_noise_spike_env(allowed_codes)
                and len(collected_local_dts) >= 2
            ):
                candidates = list(range(len(collected_local_dts)))
                if onset_env_idx is not None and onset_env_idx in candidates:
                    candidates.remove(onset_env_idx)
                if candidates:
                    noise_spike_idxs.update(random.sample(candidates, min(2, len(candidates))))

            awake_windows = _idf_awake_windows_minutes(idf_align) + _raw_awake_windows_minutes(raw_data)
            metrics_seq = _sample_environment_metrics_sequence(
                collected_local_dts, user_config, onset_env_idx, noise_spike_idxs,
                awake_windows=awake_windows,
            )
            day_noise_category = (
                daily_noise_plan[day_idx]
                if day_idx < len(daily_noise_plan)
                else random.choices(
                    ["best", "good", "noisy", "overload"],
                    weights=[10, 50, 30, 10],
                    k=1,
                )[0]
            )
            metrics_seq = _apply_daily_noise_average_target(metrics_seq, day_noise_category)

            idf_end_utc = None
            if idf_align and sleep_window_start and sleep_window_end:
                idf_end_utc = _idf_last_segment_end_utc_iso_z(
                    idf_align, sleep_window_start, sleep_window_end
                )
            n_env = len(collected_local_dts)

            for idx, collected_at_local in enumerate(collected_local_dts):
                temperature, humidity, illuminance, noise = metrics_seq[idx]

                create_time = collected_at_local + timedelta(seconds=1)
                collected_at_utc_z = local_naive_dt_to_utc_iso_z(collected_at_local)
                if idf_end_utc and n_env > 0 and idx == n_env - 1:
                    collected_at_utc_z = idf_end_utc

                data = {
                    "uid": user_id,
                    "session_id": "",
                    "record_date": record_date_str,
                    "collected_at": collected_at_utc_z,
                    "temperature": temperature,
                    "humidity": humidity,
                    "illuminance": illuminance,
                    "noise": noise,
                    "device_id": "",
                    "create_time": create_time.strftime('%Y-%m-%d %H:%M:%S'),
                    "update_time": create_time.strftime('%Y-%m-%d %H:%M:%S')
                }
                environment_data_list.append(data)
            current_date += timedelta(days=1)
            day_idx += 1
        
        return environment_data_list
    
    def save_environment_data(self, user_id, environment_data_list):
        """保存环境数据到文件"""
        if not environment_data_list:
            print("没有环境数据可保存")
            return
        
        # 构建文件名
        filename = f"{user_id}_environment_data.json"
        file_path = os.path.join(self.output_dir, filename)
        
        atomic_write_json(file_path, environment_data_list)

        print(f"环境数据已保存到 {file_path}")
    
    def _generate_question_prompt(self, question):
        """将问卷问题转为简洁的prompt"""
        title = question.get('title', '')
        input_type = question.get('input_type', 'radio')
        
        # 确定问题类型
        if input_type in ['radio', 'select']:
            question_type = "单选"
        elif input_type == 'checkbox':
            question_type = "多选"
        else:
            question_type = "单选"
        
        # 构建选项字符串
        options = question.get('options', [])
        options_str = "[{" + ", ".join(["option_id: \"{}\", option_text: \"{}\"" .format(option.get('option_id'), option.get('option_text')) for option in options]) + "}]"
        
        # 构建prompt
        prompt = f"{title}{question_type} 选项：{options_str}"
        return prompt
    
    def _generate_survey_answers_with_qwen(self, user_profile, user_preference, questions):
        """使用通义千问（DashScope）生成问卷答案"""
        # 构建问卷问题prompt
        questions_prompt = "\n".join([self._generate_question_prompt(q) for q in questions])
        
        # 构建完整的提示词
        prompt = render_prompt_template(
            "generate_health_data__survey_answers.md",
            {
                "USER_PROFILE": user_profile,
                "USER_PREFERENCE": user_preference,
                "QUESTIONS_PROMPT": questions_prompt,
            },
        )
        
        # 调用 DashScope OpenAI 兼容接口
        api_key = _qwen_api_key()
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY 环境变量未设置（或兼容读取 DOUBAO_API_KEY）")
        
        model_name = _qwen_model_name()
        
        response = requests.post(
            _qwen_chat_url(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            json={
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.7,
                "max_tokens": _QWEN_MAX_TOKENS,
                "enable_thinking": False,
            },
            timeout=_QWEN_TIMEOUT,
        )
        
        # 解析响应
        response_data = response.json()
        
        # 检查是否有错误
        if 'error' in response_data:
            error_msg = response_data['error'].get('message', '未知错误')
            error_code = response_data['error'].get('code', '未知错误码')
            print(f"API错误: [{error_code}] {error_msg}")
            raise ValueError(f"通义千问 API 调用失败: {error_msg}")
        
        if 'choices' not in response_data or not response_data['choices']:
            print(f"错误：响应中没有'choices'字段，实际响应: {response_data}")
            raise ValueError("模型返回数据格式错误")
        
        content = response_data['choices'][0]['message']['content']
        
        # 清理可能的Markdown格式
        content = content.strip()
        if content.startswith('```json'):
            content = content[7:].strip()
        if content.endswith('```'):
            content = content[:-3].strip()
        
        # 解析JSON
        answers_data = json.loads(content)
        
        # 验证生成数据格式
        if not isinstance(answers_data, list):
            raise ValueError("模型返回的数据不是JSON数组")
        
        return answers_data
    
    def _generate_sleep_stages(
        self,
        bed_time,
        sleep_time,
        wake_time,
        wake_up_time,
        sleep_latency,
        light_sleep_minutes,
        deep_sleep_minutes,
        rem_minutes,
        personality_type="M-L-C",
        night_awakening_count=0,
        night_waso_minutes=0,
        prev_front_rem=False,
    ):
        """人格化生成 idf_data：符合 new.md 的“前半夜深睡多、后半夜 REM 多”的周期过程；
        在睡眠窗口内按 night_awakening_count / night_waso_minutes 穿插夜间清醒段；
        二者任一为 0 时不插入睡眠中段的清醒（仍保留入睡潜伏期与 wake_time→wake_up_time 的觉后清醒）。"""
        stages = []

        def add_stage(stage_type, duration_min, ct):
            if duration_min <= 0:
                return ct
            end = ct + timedelta(minutes=duration_min)
            if stages and stages[-1]["stage"] == stage_type:
                stages[-1]["end"] = end.strftime("%H:%M")
            else:
                stages.append(
                    {
                        "stage": stage_type,
                        "start": ct.strftime("%H:%M"),
                        "end": end.strftime("%H:%M"),
                    }
                )
            return end

        def _allocate_by_weights(total_minutes, weights):
            """按权重分配分钟数，返回与 weights 等长的整数列表，总和严格等于 total_minutes。"""
            if total_minutes <= 0 or not weights:
                return [0] * len(weights)
            s = float(sum(weights))
            if s <= 0:
                base = total_minutes // len(weights)
                arr = [base] * len(weights)
                arr[0] += total_minutes - sum(arr)
                return arr
            raw = [total_minutes * (w / s) for w in weights]
            arr = [int(x) for x in raw]
            remain = total_minutes - sum(arr)
            if remain > 0:
                frac_idx = sorted(
                    range(len(raw)),
                    key=lambda i: raw[i] - arr[i],
                    reverse=True,
                )
                for i in frac_idx[:remain]:
                    arr[i] += 1
            return arr

        def _light_slots_with_jitter(total_light, slot_count, min_slot=11):
            """把浅睡分布到多个 light 槽位，加入小抖动避免模板化。"""
            if slot_count <= 0:
                return []
            base = _allocate_by_weights(total_light, [1] * slot_count)
            if total_light < slot_count * min_slot:
                return base
            arr = base[:]
            for _ in range(slot_count * 2):
                i = random.randint(0, slot_count - 1)
                j = random.randint(0, slot_count - 1)
                if i == j:
                    continue
                delta = random.randint(1, 4)
                if arr[i] - delta >= min_slot:
                    arr[i] -= delta
                    arr[j] += delta
            return arr

        def _jitter_cycle_alloc(arr, rounds=12, max_delta=4):
            """
            在不改变总和的前提下，对各周期分钟分配做随机转移，降低模板化。
            仅允许在正值槽位之间搬移，保证每槽不为负。
            """
            if not arr or len(arr) <= 1:
                return arr
            out = arr[:]
            n = len(out)
            for _ in range(max(1, int(rounds))):
                i = random.randint(0, n - 1)
                j = random.randint(0, n - 1)
                if i == j or out[i] <= 0:
                    continue
                d = random.randint(1, max(1, int(max_delta)))
                d = min(d, out[i])
                if d <= 0:
                    continue
                out[i] -= d
                out[j] += d
            return out

        def _rebalance_total(arr, target_total):
            """
            将整数数组总和校正到 target_total，且各元素保持 >=0。
            """
            out = [max(0, int(x)) for x in (arr or [])]
            if not out:
                return out
            delta = int(target_total) - sum(out)
            if delta > 0:
                for _ in range(delta):
                    out[random.randint(0, len(out) - 1)] += 1
            elif delta < 0:
                need = -delta
                for _ in range(need):
                    positive_idx = [i for i, v in enumerate(out) if v > 0]
                    if not positive_idx:
                        break
                    i = random.choice(positive_idx)
                    out[i] -= 1
            return out

        def _split_waso_minutes(total, k):
            """将 total 拆成 k 段正整数分钟，供多段夜间清醒使用。"""
            if k <= 0 or total <= 0:
                return []
            k = min(k, total)
            pieces = [1] * k
            rem = total - k
            for _ in range(rem):
                pieces[random.randint(0, k - 1)] += 1
            random.shuffle(pieces)
            return pieces

        # 人格特征：M/E-晨夜型，H/L-敏感度，R/C-脑活跃度
        p = (personality_type or "M-L-C").split("-")
        chronotype = p[0] if len(p) > 0 else "M"
        sensitivity = p[1] if len(p) > 1 else "L"
        activity = p[2] if len(p) > 2 else "C"

        current = bed_time
        current = add_stage("awake", sleep_latency, bed_time)

        l_rem = int(light_sleep_minutes)
        d_rem = int(deep_sleep_minutes)
        r_rem = int(rem_minutes)
        total_tst = l_rem + d_rem + r_rem

        # 依据总睡眠长度决定 4-5 周期；高敏感倾向更多周期，单周期更短、结构更碎
        if sensitivity == "H":
            if total_tst >= 300:
                cycle_count = 5
            elif total_tst >= 240:
                cycle_count = 5 if random.random() < 0.62 else 4
            else:
                cycle_count = 4
        else:
            cycle_count = 5 if total_tst >= random.randint(410, 450) else 4

        na = max(0, int(night_awakening_count or 0))
        nw = max(0, int(night_waso_minutes or 0))
        waso_durs = _split_waso_minutes(nw, na) if na > 0 and nw > 0 else []
        awake_after_cycle = {}
        if waso_durs:
            upper = max(0, cycle_count - 2)
            for d in waso_durs:
                cidx = random.randint(0, upper) if upper >= 0 else 0
                awake_after_cycle.setdefault(cidx, []).append(d)

        # 深睡：前高后低；REM：前低后高（与 new.md 睡眠过程一致）
        deep_w = [0.40, 0.30, 0.18, 0.09, 0.03][:cycle_count]
        rem_w = [0.10, 0.17, 0.23, 0.25, 0.25][:cycle_count]
        # 每晚加入细微扰动，使阶段时长曲线更像真实图谱
        deep_w = [max(0.02, w * random.uniform(0.9, 1.12)) for w in deep_w]
        rem_w = [max(0.02, w * random.uniform(0.9, 1.12)) for w in rem_w]

        # 人格微调：高敏感/高活跃 -> 深睡更前置且更碎，低敏感/低活跃 -> 更平滑
        if sensitivity == "H":
            deep_w = [w * (1.08 if i < 2 else 0.9) for i, w in enumerate(deep_w)]
        if activity == "R":
            deep_w = [w * (1.05 if i == 0 else 0.95 if i >= cycle_count - 2 else 1.0) for i, w in enumerate(deep_w)]
            rem_w = [w * (0.92 if i == 0 else 1.06 if i >= cycle_count - 2 else 1.0) for i, w in enumerate(rem_w)]
        if chronotype == "E":
            rem_w = [w * (1.03 if i >= cycle_count - 2 else 0.98) for i, w in enumerate(rem_w)]

        deep_alloc = _allocate_by_weights(d_rem, deep_w)
        rem_alloc = _allocate_by_weights(r_rem, rem_w)

        # 前后半夜分界（4周期:2|2；5周期:2|3）
        split_idx = cycle_count // 2
        front_idx = list(range(split_idx))
        back_idx = list(range(split_idx, cycle_count))

        # 约束1：前半夜 REM 即使出现也要短（单段上限，且概率压低）
        rem_front_cap = 6 if sensitivity == "H" else 8
        rem_excess = 0
        for i in front_idx:
            if rem_alloc[i] > rem_front_cap:
                rem_excess += rem_alloc[i] - rem_front_cap
                rem_alloc[i] = rem_front_cap
            elif rem_alloc[i] > 0 and random.random() < (0.96 if activity == "R" else 0.90):
                # 人格驱动：前半夜 REM 仅以极低概率保留，常见情况削短并后移到后半夜
                cut = min(rem_alloc[i] - 2, random.randint(3, 7)) if rem_alloc[i] >= 5 else 0
                if cut > 0:
                    rem_alloc[i] -= cut
                    rem_excess += cut

        # 约束1.05：前半夜 REM 以“非常低概率”保留；大多数情况整段后移到后半夜
        # 你要求的是“前半夜 REM 可以有，但概率非常低”。
        keep_front_rem_prob = 0.15 if activity == "R" else 0.22
        # 若前一天前半夜已出现 REM，则当天前半夜概率下调，降低跨天连续性（但不禁用）
        if prev_front_rem:
            keep_front_rem_prob *= 0.70
        for i in front_idx:
            if rem_alloc[i] > 0 and random.random() > keep_front_rem_prob:
                rem_excess += rem_alloc[i]
                rem_alloc[i] = 0

        # 约束1.1：前半夜 REM 允许“概率出现”，但避免在相邻周期里连续出现（观感会很密集）
        # 这里约束的是“出现在哪些周期里”的相邻关系，而不仅是单段时长。
        if front_idx and back_idx:
            prev_has_rem = False
            for i in front_idx:
                has = rem_alloc[i] > 0
                if has and prev_has_rem:
                    # 相邻周期连续 REM：把当前周期的 REM 整段后移到后半夜
                    rem_excess += rem_alloc[i]
                    rem_alloc[i] = 0
                    has = False
                prev_has_rem = has
        if rem_excess > 0 and back_idx:
            add_back = _allocate_by_weights(rem_excess, [1] * len(back_idx))
            for j, i in enumerate(back_idx):
                rem_alloc[i] += add_back[j]

        # 增加周期级随机抖动（总和守恒）；高敏感加大抖动，使各周期分钟更参差、更易形成碎段
        jr = cycle_count * (6 if sensitivity == "H" else 3)
        deep_alloc = _jitter_cycle_alloc(
            deep_alloc, rounds=jr, max_delta=(7 if sensitivity == "H" else 4)
        )
        rem_alloc = _jitter_cycle_alloc(
            rem_alloc, rounds=jr, max_delta=(8 if sensitivity == "H" else 5)
        )
        deep_alloc = _rebalance_total(deep_alloc, d_rem)
        rem_alloc = _rebalance_total(rem_alloc, r_rem)

        # 约束2：后半夜 deep 为低概率且很短（随机保留一个短深睡，其他后移到前半夜）
        deep_back_cap = 6 if sensitivity == "H" else 8
        deep_excess = 0
        if back_idx:
            # 为避免模板化，保留段位随机（但更偏向后半夜早段）
            keep_i = back_idx[0] if random.random() < 0.65 else random.choice(back_idx)
            for i in back_idx:
                if i != keep_i:
                    deep_excess += deep_alloc[i]
                    deep_alloc[i] = 0
                elif deep_alloc[i] > deep_back_cap:
                    deep_excess += deep_alloc[i] - deep_back_cap
                    deep_alloc[i] = deep_back_cap
        if deep_excess > 0 and front_idx:
            add_front = _allocate_by_weights(deep_excess, [1] * len(front_idx))
            for j, i in enumerate(front_idx):
                deep_alloc[i] += add_front[j]

        # 约束2.1：后半夜 REM 总量应显著高于前半夜（更接近真实生理过程）
        front_rem_total = sum(rem_alloc[i] for i in front_idx)
        back_rem_total = sum(rem_alloc[i] for i in back_idx)
        rem_need_shift = front_rem_total - back_rem_total + random.randint(3, 8)
        if rem_need_shift > 0 and front_idx and back_idx:
            for fi in front_idx:
                if rem_need_shift <= 0:
                    break
                can_move = max(0, rem_alloc[fi] - 3)
                mv = min(can_move, rem_need_shift)
                rem_alloc[fi] -= mv
                rem_need_shift -= mv
            if rem_need_shift > 0:
                add_back2 = _allocate_by_weights(rem_need_shift, [1] * len(back_idx))
                for j, bi in enumerate(back_idx):
                    rem_alloc[bi] += add_back2[j]

        # 每个周期采用：light -> deep -> light -> rem 的结构
        light_slots = cycle_count * 2
        light_alloc = _light_slots_with_jitter(l_rem, light_slots, min_slot=11)

        # 组装每周期 light（pre/post），后续可做前后半夜再平衡
        cycle_light = []
        li = 0
        for _ in range(cycle_count):
            pre_light = light_alloc[li] if li < len(light_alloc) else 0
            li += 1
            post_light = light_alloc[li] if li < len(light_alloc) else 0
            li += 1
            cycle_light.append([pre_light, post_light])

        # 同一周期内浅睡前后段随机再分配，保持每周期总分钟不变，降低固定形态
        for i in range(cycle_count):
            pre_light, post_light = cycle_light[i]
            tot = pre_light + post_light
            if tot <= 0:
                continue
            min_seg = 11 if tot >= 22 else 0
            lo = min_seg
            hi = max(min_seg, tot - min_seg)
            if hi < lo:
                lo = 0
                hi = tot
            new_pre = random.randint(lo, hi)
            new_post = tot - new_pre
            cycle_light[i] = [new_pre, new_post]

        # 约束3：后半夜浅睡总时长必须大于前半夜
        front_light_total = sum(cycle_light[i][0] + cycle_light[i][1] for i in front_idx)
        back_light_total = sum(cycle_light[i][0] + cycle_light[i][1] for i in back_idx)
        # 预留安全边际，降低后续对齐/合并时被“吃掉优势”的概率
        need_shift = front_light_total - back_light_total + random.randint(6, 12)
        if need_shift > 0 and front_idx and back_idx:
            # 从前半夜转移到后半夜，尽量不让单段 light 低于 6 分钟
            front_slots = []
            back_slots = []
            for i in front_idx:
                front_slots.extend([(i, 0), (i, 1)])
            for i in back_idx:
                back_slots.extend([(i, 0), (i, 1)])
            b_cursor = 0
            while need_shift > 0 and front_slots:
                moved_this_round = False
                for fi, fs in front_slots:
                    if need_shift <= 0:
                        break
                    if cycle_light[fi][fs] <= 11:
                        continue
                    bi, bs = back_slots[b_cursor % len(back_slots)]
                    cycle_light[fi][fs] -= 1
                    cycle_light[bi][bs] += 1
                    b_cursor += 1
                    need_shift -= 1
                    moved_this_round = True
                if not moved_this_round:
                    break

        # 浅睡总量守恒校正：保证所有 light 槽位总分钟严格等于公式拆分值
        flat_light = [x for pair in cycle_light for x in pair]
        flat_light = _rebalance_total(flat_light, l_rem)
        for i in range(cycle_count):
            cycle_light[i][0] = flat_light[i * 2]
            cycle_light[i][1] = flat_light[i * 2 + 1]

        carry_rem = 0
        for i in range(cycle_count):
            pre_light, post_light = cycle_light[i]
            d_min = max(0, int(deep_alloc[i]))
            r_min = max(0, int(rem_alloc[i])) + carry_rem
            carry_rem = 0

            # 高活跃人格第一周期浅睡更长，体现“入睡后前段过渡偏长”
            if activity == "R" and i == 0 and post_light > 4:
                shift = min(6, post_light - 4)
                pre_light += shift
                post_light -= shift

            # 顺序约束：必须以 light 开头，避免出现 deep/rem 先于 light 的片段。
            # 同一周期 deep 与 rem 同时出现仅保留极低概率，其余把 rem 递延到下一周期。
            if d_min > 0 and r_min > 0 and random.random() > 0.12:
                carry_rem = r_min
                r_min = 0

            if i in front_idx:
                if r_min > 0 and d_min > 0:
                    rv = random.random()
                    if rv < 0.80:
                        # 前半夜若出现 REM，优先放在第二段 light 之前，减少 L-D-L-R 模板感
                        pattern = "L-D-R-L"
                    elif rv < 0.95:
                        # 仅保留小概率出现经典 L-D-L-R
                        pattern = "L-D-L-R"
                    else:
                        # 极小概率扰动，避免多日连续同形态
                        pattern = "L-R-L-D"
                elif d_min > 0:
                    pattern = "L-D-L"
                else:
                    pattern = "L-L-R" if r_min > 0 else "L-L"
            else:
                if r_min > 0 and d_min > 0:
                    pattern = random.choice(["L-D-L-R", "L-D-R-L"])
                elif r_min > 0:
                    pattern = "L-R-L"
                elif d_min > 0:
                    pattern = "L-D-L"
                else:
                    pattern = "L-L"

            stage_map = {
                "L": ("light", pre_light),
                "D": ("deep", d_min),
                "R": ("rem", r_min),
            }
            # 用两个 light 槽位分别承载 pre/post，避免完全同形态
            light_cursor = 0
            for token in pattern.split("-"):
                if token == "L":
                    dur = pre_light if light_cursor == 0 else post_light
                    light_cursor += 1
                    if dur > 0:
                        current = add_stage("light", dur, current)
                    continue
                st, dur = stage_map[token]
                if dur > 0:
                    current = add_stage(st, dur, current)

            for d_aw in awake_after_cycle.get(i, []):
                current = add_stage("awake", d_aw, current)

        if carry_rem > 0:
            current = add_stage("rem", carry_rem, current)

        # 兜底：若存在四舍五入残留，补到 light，保持与 wake_time 对齐
        consumed_sleep = int(round((current - sleep_time).total_seconds() / 60))
        residual = total_tst - max(0, consumed_sleep)
        if residual > 0:
            current = add_stage("light", residual, current)

        # 强制对齐 wake_time（少量误差补齐/裁剪到 light）
        delta_to_wake = int(round((wake_time - current).total_seconds() / 60))
        if delta_to_wake > 0:
            current = add_stage("light", delta_to_wake, current)
        elif delta_to_wake < 0 and stages:
            overshoot = -delta_to_wake
            while overshoot > 0 and stages:
                last = stages[-1]
                st = datetime.strptime(last["start"], "%H:%M")
                et = datetime.strptime(last["end"], "%H:%M")
                seg = int((et - st).total_seconds() / 60)
                if seg <= 0:
                    seg += 24 * 60
                cut = min(seg, overshoot)
                new_end = (et - timedelta(minutes=cut)).strftime("%H:%M")
                if new_end == last["start"]:
                    stages.pop()
                else:
                    last["end"] = new_end
                overshoot -= cut
            current = wake_time

        morning = minutes_between_datetimes(wake_time, wake_up_time)
        if morning > 0:
            current = add_stage("awake", morning, wake_time)

        # 人格后处理：高敏感 — 打断过长单段，结构更碎（仍通过下方合并阈值避免过短）；
        # 低敏感 — 矫正浅睡各连续段时长差异过大，使分布更平滑。
        tl_post = _idf_timeline_from_stages(stages)
        sleep_off = max(0, int(round((sleep_time - bed_time).total_seconds() / 60)))
        wake_off = max(0, int(round((wake_time - bed_time).total_seconds() / 60)))
        if tl_post and wake_off > sleep_off:
            wake_off = min(wake_off, len(tl_post))
            sleep_off = min(sleep_off, len(tl_post))
            if sensitivity == "H":
                tl_post = _fragment_idf_timeline_high_sensitivity(
                    tl_post, sleep_off, wake_off
                )
            elif sensitivity == "L":
                tl_post = _smooth_light_runs_low_sensitivity_timeline(
                    tl_post, sleep_off, wake_off
                )
                tl_post = _low_s_light_redistribute_and_balance_timeline(
                    tl_post, sleep_off, wake_off
                )
            stages = _idf_stages_from_timeline(tl_post, bed_time)

        # 消除过短非 awake 段：合并到相邻段。高敏感略放宽下限，以保留一定碎段感。
        # 优先合并到前一段；若是首段则合并到后一段；若合并后前后两段同类则再次合并。
        MIN_SEG_MINUTES = 9 if sensitivity == "H" else 11

        def _seg_minutes(seg):
            sh, sm = map(int, seg["start"].split(":"))
            eh, em = map(int, seg["end"].split(":"))
            d = (eh * 60 + em) - (sh * 60 + sm)
            return d if d >= 0 else d + 1440

        def _stages_to_timeline(stage_rows):
            tl = []
            for seg in (stage_rows or []):
                dur = _seg_minutes(seg)
                if dur <= 0:
                    continue
                tl.extend([seg["stage"]] * dur)
            return tl

        def _timeline_to_stages(tl, anchor_dt):
            out = []
            if not tl:
                return out
            seg_start = 0
            cur_stage = tl[0]
            for idx in range(1, len(tl) + 1):
                is_break = idx == len(tl) or tl[idx] != cur_stage
                if not is_break:
                    continue
                st = anchor_dt + timedelta(minutes=seg_start)
                et = anchor_dt + timedelta(minutes=idx)
                out.append(
                    {
                        "stage": cur_stage,
                        "start": st.strftime("%H:%M"),
                        "end": et.strftime("%H:%M"),
                    }
                )
                if idx < len(tl):
                    seg_start = idx
                    cur_stage = tl[idx]
            return out

        def _enforce_light_back_heavier(stage_rows):
            """
            硬约束：sleep_time->wake_time 区间内，前半夜浅睡总分钟数 < 后半夜浅睡总分钟数。
            通过前后半夜分钟级“换标”实现，保持总体分钟数不变。
            """
            tl = _stages_to_timeline(stage_rows)
            if not tl:
                return stage_rows
            sleep_offset = max(0, int(round((sleep_time - bed_time).total_seconds() / 60)))
            wake_offset = max(0, int(round((wake_time - bed_time).total_seconds() / 60)))
            if wake_offset <= sleep_offset or sleep_offset >= len(tl):
                return stage_rows
            wake_offset = min(wake_offset, len(tl))
            mid = sleep_offset + (wake_offset - sleep_offset) // 2
            front_idx = [
                i for i in range(sleep_offset, mid)
                if tl[i] == "light"
            ]
            back_light = sum(1 for i in range(mid, wake_offset) if tl[i] == "light")
            front_light = len(front_idx)
            need = front_light - back_light + 1
            if need <= 0:
                return stage_rows
            back_non_light = [
                i for i in range(mid, wake_offset)
                if tl[i] not in ("light", "awake")
            ]
            if not back_non_light:
                return stage_rows
            swaps = min(need, len(front_idx), len(back_non_light))
            # 选取前半夜后段、后半夜前段优先交换，减少阶段碎片
            front_pick = sorted(front_idx, reverse=True)[:swaps]
            back_pick = sorted(back_non_light)[:swaps]
            for fi, bi in zip(front_pick, back_pick):
                back_stage = tl[bi]
                tl[bi] = "light"
                tl[fi] = back_stage
            return _timeline_to_stages(tl, bed_time)

        def _merge_short_non_awake_segments(stage_rows):
            out = stage_rows
            merged = True
            while merged:
                merged = False
                i = 0
                while i < len(out):
                    seg = out[i]
                    if seg["stage"] != "awake" and _seg_minutes(seg) < MIN_SEG_MINUTES:
                        if i > 0:
                            # 合并到前一段：把本段的 end 赋给前一段
                            out[i - 1]["end"] = seg["end"]
                            out.pop(i)
                        elif i + 1 < len(out):
                            # 首段：合并到后一段（把本段 start 赋给后一段）
                            out[i + 1]["start"] = seg["start"]
                            out.pop(i)
                        else:
                            i += 1
                            continue
                        merged = True
                        # 合并后检查前后两段是否同类，若是则再合并
                        if i > 0 and i <= len(out) - 1:
                            if out[i - 1]["stage"] == out[i]["stage"]:
                                out[i - 1]["end"] = out[i]["end"]
                                out.pop(i)
                    else:
                        i += 1
            return out

        stages = _merge_short_non_awake_segments(stages)
        # 最终硬约束：前半夜浅睡 < 后半夜浅睡
        stages = _enforce_light_back_heavier(stages)
        # 低敏感：硬约束交换后可能再次拉大浅睡段时长差，再平滑一轮
        if sensitivity == "L":
            tl2 = _idf_timeline_from_stages(stages)
            if tl2 and wake_off > sleep_off:
                wo2 = min(wake_off, len(tl2))
                so2 = min(sleep_off, len(tl2))
                tl2 = _smooth_light_runs_low_sensitivity_timeline(tl2, so2, wo2)
                tl2 = _low_s_light_redistribute_and_balance_timeline(tl2, so2, wo2)
                stages = _idf_stages_from_timeline(tl2, bed_time)
        # 交换后再清一次碎片，确保非 awake 阶段不低于合并阈值
        stages = _merge_short_non_awake_segments(stages)
        # 低敏感：合并短段可能再次产生超长浅睡，循环改标为深/REM 并维持前半夜浅睡 < 后半夜
        if sensitivity == "L":
            for _ in range(8):
                tlx = _idf_timeline_from_stages(stages)
                if not tlx or wake_off <= sleep_off:
                    break
                wo_m = min(wake_off, len(tlx))
                so_m = min(sleep_off, len(tlx))
                lr = _runs_light_in_window(tlx, so_m, wo_m)
                mx = max((b - a + 1 for a, b in lr), default=0)
                mid_m = so_m + (wo_m - so_m) // 2
                front_l = sum(1 for i in range(so_m, mid_m) if tlx[i] == "light")
                back_l = sum(1 for i in range(mid_m, wo_m) if tlx[i] == "light")
                if mx <= 60 and front_l < back_l:
                    break
                tlx = _low_s_light_redistribute_and_balance_timeline(tlx, so_m, wo_m)
                stages = _idf_stages_from_timeline(tlx, bed_time)
                stages = _merge_short_non_awake_segments(stages)

        return stages

    
    def _generate_schedule_events_with_qwen(self, user_preference, event_count):
        """使用通义千问（DashScope）根据用户偏好生成日程事件"""
        print("正在调用通义千问为用户生成日程事件...")
        
        # 构造提示词
        prompt = render_prompt_template(
            "generate_health_data__schedule_events.md",
            {
                "EVENT_COUNT": event_count,
                "USER_PREFERENCE": user_preference,
            },
        )
        
        # 调用 DashScope OpenAI 兼容接口
        api_key = _qwen_api_key()
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY 环境变量未设置（或兼容读取 DOUBAO_API_KEY）")
        
        model_name = _qwen_model_name()
        
        response = requests.post(
            _qwen_chat_url(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            json={
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.7,
                "max_tokens": _QWEN_MAX_TOKENS,
                "enable_thinking": False,
            },
            timeout=_QWEN_TIMEOUT,
        )
        
        # 解析响应
        response_data = response.json()
        
        # 检查是否有错误
        if 'error' in response_data:
            error_msg = response_data['error'].get('message', '未知错误')
            error_code = response_data['error'].get('code', '未知错误码')
            print(f"API错误: [{error_code}] {error_msg}")
            raise ValueError(f"通义千问 API 调用失败: {error_msg}")
        
        if 'choices' not in response_data or not response_data['choices']:
            print(f"错误：响应中没有'choices'字段，实际响应: {response_data}")
            raise ValueError("模型返回数据格式错误")
        
        content = response_data['choices'][0]['message']['content']
        
        # 清理可能的Markdown格式
        content = content.strip()
        if content.startswith('```json'):
            content = content[7:].strip()
        if content.endswith('```'):
            content = content[:-3].strip()
        
        # 解析JSON
        generated_events = json.loads(content)
        
        # 验证生成数据格式
        if not isinstance(generated_events, list):
            raise ValueError("模型返回的数据不是JSON数组")
        
        print(f"已生成{len(generated_events)}个日程事件")
        return generated_events
    
    
    def generate_schedule_data(self, user_id, start_date, end_date, max_events_per_day, user_preference=None):
        """生成日程数据，根据人格编码生成符合用户画像的日程安排

        人格维度影响：
        - 作息类型(M/E)：影响活动时间段，M型早起活动集中在上午，E型活动偏向下午和晚间
        - 敏感度(H/L)：H型偏好安静独处活动，L型社交和户外活动更多；会议时长略缩短
        - 大脑活跃度(R/C)：R型偏好思维密集型活动，C型偏好轻松实践型活动

        密度：默认每天目标为 10-12 条日程，并通过「当天总时长预算」控制实际条数。
        若出现 2-3 小时的长时段事件，会自然挤占预算，导致当日最终事件数量减少。
        """
        # 检查日程数据是否已生成
        output_file = os.path.join(self.output_dir, f"{user_id}_schedule_data.json")
        if os.path.exists(output_file):
            print(f"用户 {user_id} 的日程数据已生成，跳过")
            with open(output_file, 'r', encoding='utf-8') as f:
                return json.load(f)

        # 查找用户配置和人格类型
        user_config = None
        personality_type = 'M-L-C'
        for user in self.users:
            if user.get('user_id') == user_id:
                user_config = user
                personality_type = user.get('personalInformation', {}).get('type', 'M-L-C')
                break

        dims = parse_personality_code(personality_type)
        profile = get_personality_profile(personality_type)

        # ============================================================
        # 根据人格定义事件池和权重
        # ============================================================

        # 按人格类型定义专属事件池
        PERSONALITY_EVENTS = {
            "M-H-R": {
                # 完美主义百灵鸟：自律、计划导向、偏好安静有序的活动
                "task": [
                    "制定周计划", "复盘工作日志", "整理待办清单", "阅读专业书籍", "学习新技能课程",
                    "撰写工作报告", "数据分析任务", "梳理项目文档", "更新个人知识库", "完成在线课程作业",
                    "整理电子笔记", "研究行业趋势报告", "优化工作流程", "准备演讲PPT", "写读书笔记",
                    "清理邮箱和消息", "制定月度目标", "回顾季度OKR", "学习时间管理方法", "整理书架和桌面"
                ],
                "activity": [
                    "晨间跑步", "瑜伽练习", "冥想放松", "听播客", "轻度拉伸运动",
                    "公园快走", "跳绳训练", "普拉提课程", "太极拳练习", "晨间日光浴",
                    "骑行上班", "爬楼梯锻炼", "办公室微运动", "午间散步", "睡前拉伸放松",
                    "呼吸训练", "正念行走", "室内有氧操", "弹力带训练", "泡脚放松"
                ],
                "meeting": [
                    "团队周会", "项目进度讨论", "一对一沟通", "部门月度总结会",
                    "跨部门协调会", "客户需求评审", "技术方案评审", "读书分享会",
                    "导师辅导会议", "季度规划讨论"
                ],
                "appointment": [
                    "体检预约", "心理咨询", "眼科检查", "牙科洁牙",
                    "营养师咨询", "中医体质调理", "皮肤科复查", "疫苗接种预约"
                ],
                "weights": {"task": 35, "activity": 30, "meeting": 20, "appointment": 15},
                "events_per_day": {"min": 2, "max": 4},
            },
            "M-H-C": {
                # 敏感的晨间鹿：安静独处、低刺激、环境依赖
                "task": [
                    "阅读文学作品", "写日记", "整理房间", "手工制作", "学习冥想技巧",
                    "练习书法", "画水彩画", "编织毛衣", "整理照片相册", "抄写经典段落",
                    "制作手账", "学习插花", "烘焙甜点", "研究精油配方", "写感恩日记",
                    "整理衣柜", "学习茶道", "做拼图", "写明信片", "整理药箱"
                ],
                "activity": [
                    "散步", "轻度瑜伽", "冥想", "听轻音乐", "呼吸练习", "泡茶放松",
                    "清晨赏花", "林间漫步", "湖边静坐", "阳台晒太阳", "做面部护理",
                    "芳香疗法", "温泉泡汤", "轻柔拉伸", "听白噪音放松", "做手指操",
                    "园艺浇花", "喂鸟观鸟", "看日出", "傍晚散步"
                ],
                "meeting": [
                    "与亲密朋友小聚", "线上读书会", "与家人视频通话", "两人下午茶",
                    "与闺蜜逛花市", "小型手工沙龙", "线上冥想小组"
                ],
                "appointment": [
                    "中医调理预约", "心理咨询", "按摩理疗", "针灸预约",
                    "芳疗师咨询", "睡眠门诊", "过敏原检测", "体质调理复诊"
                ],
                "weights": {"task": 25, "activity": 45, "meeting": 12, "appointment": 15},
                "events_per_day": {"min": 1, "max": 3},
            },
            "M-L-R": {
                # 效率至上考拉：高效、规律运动、思维活跃
                "task": [
                    "完成项目方案", "撰写技术文档", "复盘工作", "规划明日任务", "阅读专业内容",
                    "学习新框架", "代码重构", "写技术博客", "研究竞品分析", "准备技术分享",
                    "优化数据库查询", "搭建测试环境", "编写自动化脚本", "更新API文档", "做性能压测",
                    "学习系统设计", "整理技术笔记", "研究新工具", "完成代码审查", "写周报总结"
                ],
                "activity": [
                    "健身房训练", "跑步", "游泳", "骑行",
                    "HIIT训练", "攀岩", "羽毛球", "乒乓球",
                    "拳击训练", "划船机训练", "户外徒步", "跳绳间歇训练",
                    "壶铃训练", "引体向上挑战", "平板支撑", "动感单车课",
                    "篮球投篮练习", "网球对打", "深蹲训练", "晨间力量训练"
                ],
                "meeting": [
                    "团队会议", "项目讨论", "客户沟通", "技术评审",
                    "Sprint回顾会", "需求评审会", "架构设计讨论", "代码走查会",
                    "产品路线图讨论", "跨团队对齐会", "新人指导会议", "技术选型讨论"
                ],
                "appointment": [
                    "体检预约", "牙科检查", "运动损伤复查", "视力检查",
                    "健身教练私教课", "营养师咨询", "骨科复诊", "运动康复评估"
                ],
                "weights": {"task": 35, "activity": 25, "meeting": 30, "appointment": 10},
                "events_per_day": {"min": 2, "max": 4},
            },
            "M-L-C": {
                # 阳光漫步者：轻松踏实、简单运动、注重舒适
                "task": [
                    "完成日常工作", "简单家务整理", "阅读", "听播客",
                    "整理购物清单", "缴纳水电费", "更新记账本", "收拾阳台",
                    "给植物换盆", "擦拭家具", "洗车", "整理冰箱",
                    "网购日用品", "预约快递取件", "检查家电保养", "写购物心得",
                    "学做新菜谱", "整理鞋柜", "更换床品", "打扫卫生间"
                ],
                "activity": [
                    "散步", "简单运动", "看电影", "做兴趣爱好", "逛公园", "骑行",
                    "逛超市", "遛狗", "拍照记录生活", "去咖啡馆坐坐",
                    "逛书店", "去花鸟市场", "河边慢跑", "广场太极",
                    "社区健身器材锻炼", "周末爬山", "钓鱼", "放风筝",
                    "逛夜市", "骑共享单车兜风"
                ],
                "meeting": [
                    "朋友聚餐", "团队午餐", "周末家庭聚会", "同事下午茶",
                    "邻居烧烤派对", "老同学叙旧", "生日聚会", "节日家宴",
                    "社区活动", "亲子活动日"
                ],
                "appointment": [
                    "理发预约", "体检预约", "牙科洁牙", "配眼镜",
                    "汽车保养", "家电维修", "宠物疫苗", "快递上门取件"
                ],
                "weights": {"task": 25, "activity": 40, "meeting": 25, "appointment": 10},
                "events_per_day": {"min": 1, "max": 4},
            },
            "E-H-R": {
                # 深夜灵感守望者：夜间创作、高强度思维、白天休整
                "task": [
                    "深夜写作", "编程项目", "设计创作", "绘画", "剪辑视频", "撰写创意方案",
                    "写小说章节", "制作音乐", "3D建模", "写诗", "研究哲学文献",
                    "设计UI原型", "写剧本大纲", "制作动画分镜", "调色修图",
                    "录制播客", "编曲混音", "写乐评影评", "翻译外文资料", "构思创意提案"
                ],
                "activity": [
                    "午后散步", "冥想", "听音乐放松", "下午瑜伽",
                    "泡咖啡馆发呆", "逛美术馆", "看艺术展", "夜间听黑胶唱片",
                    "阳台观星", "夜间城市漫步", "看纪录片", "泡澡放空",
                    "做手冲咖啡", "翻阅画册", "听古典音乐", "做白日梦",
                    "逛独立书店", "参观摄影展", "深夜电台", "窗边发呆"
                ],
                "meeting": [
                    "线上创意讨论", "与同好交流", "艺术沙龙", "写作工坊",
                    "独立电影放映会", "诗歌朗诵会", "摄影爱好者聚会", "深夜电台连线",
                    "创意头脑风暴", "设计评审会"
                ],
                "appointment": [
                    "心理咨询", "中医调理", "睡眠门诊", "针灸理疗",
                    "眼科检查", "颈椎理疗", "芳疗预约", "正骨推拿"
                ],
                "weights": {"task": 40, "activity": 30, "meeting": 12, "appointment": 15},
                "events_per_day": {"min": 1, "max": 4},
            },
            "E-H-C": {
                # 深海独奏家：安静独处、夜间沉浸、环境敏感
                "task": [
                    "听音乐", "阅读小说", "写作", "看电影", "布置房间", "整理个人空间",
                    "写情绪日记", "制作歌单", "整理书架", "研究香薰精油",
                    "学习手冲咖啡", "做手工蜡烛", "写信给未来的自己", "整理旧物",
                    "学习星座知识", "做梦境记录", "写影评", "收集灵感图片",
                    "学习调香", "整理数码照片"
                ],
                "activity": [
                    "独自散步", "冥想", "泡澡放松", "轻度拉伸",
                    "深夜听雨", "点香薰蜡烛", "做面膜护肤", "窗边看月亮",
                    "听ASMR", "做瑜伽尼德拉", "慢节奏骑行", "逛安静的寺庙",
                    "独自看海", "在咖啡馆角落看书", "听钢琴曲", "做手指冥想",
                    "夜间泡脚", "做渐进式肌肉放松", "听自然白噪音", "阳台种花"
                ],
                "meeting": [
                    "与亲密朋友视频", "小型私密聚会", "两人安静晚餐", "与知己散步聊天",
                    "线上心理互助小组", "小型读书分享", "与家人视频", "闺蜜下午茶"
                ],
                "appointment": [
                    "心理咨询", "按摩预约", "芳疗师咨询", "睡眠门诊",
                    "中医把脉", "颈肩理疗", "冥想指导课", "音乐疗愈预约"
                ],
                "weights": {"task": 30, "activity": 38, "meeting": 12, "appointment": 20},
                "events_per_day": {"min": 0, "max": 3},
            },
            "E-L-R": {
                # 创意夜猫子：夜间活跃、随性自然、思维持续运转
                "task": [
                    "看视频学习", "玩游戏", "浏览资讯", "学习新内容", "编程练习", "写技术博客",
                    "研究开源项目", "刷LeetCode", "看技术直播", "折腾智能家居",
                    "搭建个人网站", "学习新编程语言", "做数据可视化", "写自动化脚本",
                    "研究AI工具", "看科技评测", "参与开源贡献", "做副业项目",
                    "学习投资理财", "整理GitHub仓库"
                ],
                "activity": [
                    "夜跑", "健身", "打篮球", "骑行",
                    "深夜撸铁", "台球", "飞盘运动", "滑板",
                    "攀岩馆", "蹦床公园", "射箭体验", "卡丁车",
                    "密室逃脱", "桌游", "电竞比赛", "VR体验",
                    "保龄球", "真人CS", "夜间骑行", "跑酷训练"
                ],
                "meeting": [
                    "朋友聚会", "线上游戏组队", "技术社区交流", "黑客马拉松",
                    "桌游之夜", "电竞开黑", "烧烤聚会", "深夜火锅局",
                    "技术Meetup", "创业者交流会", "开源社区线下聚", "狼人杀之夜"
                ],
                "appointment": [
                    "体检预约", "牙科检查", "运动损伤复查", "配隐形眼镜",
                    "健身教练课", "理发预约", "纹身咨询", "汽车保养"
                ],
                "weights": {"task": 35, "activity": 25, "meeting": 30, "appointment": 10},
                "events_per_day": {"min": 1, "max": 4},
            },
            "E-L-C": {
                # 月光冲浪者：深夜放松、随性自然、入睡快
                "task": [
                    "刷内容", "追剧", "玩游戏", "写作", "思考人生",
                    "看综艺节目", "刷短视频", "听有声书", "逛购物网站", "看直播",
                    "整理收藏夹", "写美食点评", "做旅行攻略", "看漫画",
                    "学做新菜", "研究咖啡豆", "看体育赛事回放", "听脱口秀",
                    "写旅行日记", "整理歌单"
                ],
                "activity": [
                    "夜间散步", "简单运动", "听音乐", "做饭",
                    "逛夜市", "深夜便利店", "开车兜风", "去酒吧小酌",
                    "看午夜场电影", "去24小时书店", "夜钓", "天台吹风",
                    "做夜宵", "泡温泉", "KTV唱歌", "打台球",
                    "逛深夜食堂", "骑摩托兜风", "去海边听浪", "露营看星星"
                ],
                "meeting": [
                    "朋友深夜聊天", "周末聚餐", "线上社交", "火锅局",
                    "烧烤之夜", "KTV聚会", "露营派对", "生日趴",
                    "电影之夜", "游戏开黑", "深夜大排档", "周末自驾游"
                ],
                "appointment": [
                    "理发预约", "体检预约", "汽车保养", "宠物体检",
                    "配眼镜", "牙科洁牙", "快递取件", "家政保洁预约"
                ],
                "weights": {"task": 25, "activity": 35, "meeting": 30, "appointment": 10},
                "events_per_day": {"min": 1, "max": 4},
            },
        }

        # 获取当前人格的事件配置，fallback到M-L-C
        events_config = PERSONALITY_EVENTS.get(personality_type, PERSONALITY_EVENTS["M-L-C"])

        # ============================================================
        # 人格影响“当天可承载的日程强度”
        # - 高敏感 + 高活跃（H-R）更容易睡不好：日程更少、更轻，减少社交/会议
        # - poor_quality（典型 E-H-R）进一步收缩
        # ============================================================
        def apply_schedule_capacity(min_e, max_e, weights_dict):
            min_e = int(min_e)
            max_e = int(max_e)
            weights_dict = dict(weights_dict or {})

            chronotype = dims.get("chronotype")
            stage_pattern = profile.get("stage_pattern", "")
            sens = dims.get("sensitivity")
            act = dims.get("brain_activity")

            # 作息偏好：夜型娱乐/社交略多，晨型运动略多
            if chronotype == "E":
                weights_dict["activity"] = int(weights_dict.get("activity", 0) * 1.25)
                weights_dict["meeting"] = int(weights_dict.get("meeting", 0) * 1.12)
                weights_dict["task"] = int(weights_dict.get("task", 0) * 0.8)
            else:
                weights_dict["activity"] = int(weights_dict.get("activity", 0) * 1.25)
                weights_dict["task"] = int(weights_dict.get("task", 0) * 1.05)
                weights_dict["meeting"] = int(weights_dict.get("meeting", 0) * 0.92)

            # 基础调整（高敏感稍减社交/会议）
            if sens == "H":
                weights_dict["meeting"] = max(0, int(weights_dict.get("meeting", 0) * 0.7))
                weights_dict["activity"] = int(weights_dict.get("activity", 0) * 1.05)

            # H-R：日程明显更少
            if sens == "H" and act == "R":
                min_e = max(0, min_e - 1)
                max_e = max(1, max_e - 2)
                weights_dict["meeting"] = max(0, int(weights_dict.get("meeting", 0) * 0.5))
                weights_dict["task"] = max(0, int(weights_dict.get("task", 0) * 0.85))
                weights_dict["activity"] = max(0, int(weights_dict.get("activity", 0) * 1.1))

            # poor_quality（E-H-R）最少
            if stage_pattern == "poor_quality":
                min_e = max(0, min_e - 1)
                max_e = max(1, max_e - 1)
                weights_dict["meeting"] = 0
                weights_dict["appointment"] = int(weights_dict.get("appointment", 0) * 0.9)

            # 兜底：权重不能全为0
            if sum(weights_dict.values()) <= 0:
                weights_dict = {"task": 25, "activity": 45, "meeting": 15, "appointment": 15}

            if min_e > max_e:
                min_e = max_e

            return min_e, max_e, weights_dict

        def _compute_cycle_target_events(total_days):
            """
            依据人格计算周期总事件数：
            - 夜型基线 40-45，晨型略少
            - 高敏感/高活跃分别 -2~-3
            - 低敏感/低活跃分别 +1~+2
            """
            chronotype = dims.get("chronotype", "M")
            sensitivity = dims.get("sensitivity", "L")
            brain_activity = dims.get("brain_activity", "C")

            base_min, base_max = (40, 45) if chronotype == "E" else (35, 40)
            target = random.randint(base_min, base_max)

            if sensitivity == "H":
                target -= random.randint(2, 3)
            else:
                target += random.randint(1, 2)

            if brain_activity == "R":
                target -= random.randint(2, 3)
            else:
                target += random.randint(1, 2)

            # 每天至少 2 条，且给足上限避免异常膨胀
            min_total = max(total_days * 2, 24)
            max_total = min(total_days * 5, 52)
            return max(min_total, min(max_total, target))

        def _build_daily_targets(total_days, total_target, per_day_cap):
            if total_days <= 0:
                return []

            cap = max(2, int(per_day_cap))
            hard_min_total = total_days * 2
            hard_max_total = total_days * cap
            total_target = max(hard_min_total, min(hard_max_total, int(total_target)))

            daily = [2 for _ in range(total_days)]
            remains = total_target - hard_min_total
            while remains > 0:
                candidates = [i for i, v in enumerate(daily) if v < cap]
                if not candidates:
                    break
                idx = random.choice(candidates)
                daily[idx] += 1
                remains -= 1
            return daily

        # ============================================================
        # 根据人格定义活动时间段
        # ============================================================
        if dims['chronotype'] == 'M':
            # 早起型：活动集中在上午和下午早段
            time_slots = {
                "task": {"start_hour": 8, "end_hour": 17},
                "activity": {"start_hour": 6, "end_hour": 18},
                "meeting": {"start_hour": 9, "end_hour": 16},
                "appointment": {"start_hour": 9, "end_hour": 17},
            }
        else:
            # 晚睡型：活动偏向下午和晚间
            time_slots = {
                "task": {"start_hour": 11, "end_hour": 23},
                "activity": {"start_hour": 13, "end_hour": 22},
                "meeting": {"start_hour": 11, "end_hour": 20},
                "appointment": {"start_hour": 11, "end_hour": 18},
            }

        schedule_data_list = []

        # 构建加权事件类型列表（先按人格调节容量与权重）
        weights = events_config["weights"]
        weighted_types = []
        for event_type, weight in weights.items():
            weighted_types.extend([event_type] * weight)

        # 每日总时长配置（若配置存在，会与默认预算取更小值）
        schedule_cfg = user_config.get('schedule', {}) if user_config else {}
        cfg_total_min = schedule_cfg.get('min_total_minutes_per_day')
        cfg_total_max = schedule_cfg.get('max_total_minutes_per_day')

        total_days = max(1, (end_date - start_date).days + 1)
        per_day_cap = 5
        if isinstance(max_events_per_day, int) and max_events_per_day > 0:
            per_day_cap = min(6, max_events_per_day)
        per_day_cap = max(3, per_day_cap)

        # 周期总量按人格生成，再分摊到每天
        cycle_target_events = _compute_cycle_target_events(total_days)
        daily_event_targets = _build_daily_targets(total_days, cycle_target_events, per_day_cap)

        # 人格维度用于调节事件类型权重
        _, _, weights = apply_schedule_capacity(2, per_day_cap, weights)

        # 用更新后的 weights 重建 weighted_types
        weighted_types = []
        for event_type, weight in weights.items():
            weighted_types.extend([event_type] * max(0, int(weight)))
        if not weighted_types:
            weighted_types = ["task", "activity", "meeting", "appointment"]

        def _pick_schedule_event_name(etype):
            pool = events_config[etype]
            # 以 SCHEDULE_ACTIVITY_CONSTRAINTS 为白名单，过滤 V2.4 文档未收录的活动
            constrained_pool = [e for e in pool if _lookup_activity_constraint(e) is not None]
            if not constrained_pool:
                constrained_pool = pool  # 兜底：整个池都被过滤时保留原池
            name = random.choice(constrained_pool)
            if dims["chronotype"] == "E":
                morning_markers = ("晨间", "早起", "清晨", "晨跑", "晨练", "早餐会", "早间")
                if any(m in name for m in morning_markers):
                    for _ in range(14):
                        name = random.choice(constrained_pool)
                        if not any(m in name for m in morning_markers):
                            break
            else:
                night_markers = ("深夜", "午夜", "凌晨加班", "子夜")
                if any(m in name for m in night_markers):
                    for _ in range(14):
                        name = random.choice(constrained_pool)
                        if not any(m in name for m in night_markers):
                            break
            # 过滤睡眠/中医相关事件
            if _is_excluded_event_name(name):
                for _ in range(20):
                    cand = random.choice(constrained_pool)
                    if not _is_excluded_event_name(cand):
                        name = cand
                        break
            return name

        sleep_related_keywords = (
            "睡前", "入睡", "睡眠", "助眠", "睡后", "睡醒", "午睡", "小憩",
            "白噪音", "ASMR", "冥想放松", "瑜伽尼德拉", "泡脚放松", "夜间泡脚"
        )
        tcm_related_keywords = (
            "中医", "针灸", "把脉", "体质调理", "经络", "艾灸", "推拿",
            "正骨", "中药", "刮痧", "拔罐"
        )

        def _is_sleep_related_event_name(event_name):
            nm = str(event_name or "").strip()
            if not nm:
                return False
            return any(k in nm for k in sleep_related_keywords)

        def _is_tcm_related_event_name(event_name):
            nm = str(event_name or "").strip()
            if not nm:
                return False
            return any(k in nm for k in tcm_related_keywords)

        def _is_excluded_event_name(event_name):
            return _is_sleep_related_event_name(event_name) or _is_tcm_related_event_name(event_name)

        # 事件关键词分类：约束时间段 + 时长（例如 KTV 必须夜间）
        time_duration_rules = [
            # 夜生活：禁止白天出现
            {
                "keywords": ("ktv", "酒吧", "夜市", "大排档", "午夜场", "深夜", "午夜", "夜间"),
                "slot": {"start_hour": 19, "end_hour": 24},
                "duration": (90, 220),
            },
            # 晚间社交
            {
                "keywords": ("聚会", "派对", "桌游", "狼人杀", "烧烤", "火锅局", "电影之夜"),
                "slot": {"start_hour": 18, "end_hour": 23},
                "duration": (90, 210),
            },
            # 医疗/政务办理类：白天
            {
                "keywords": ("体检", "门诊", "复诊", "牙科", "洁牙", "疫苗", "心理咨询", "理发", "配眼镜", "汽车保养", "维修"),
                "slot": {"start_hour": 9, "end_hour": 18},
                "duration": (30, 120),
            },
            # 便餐（与「下午茶」等区分，先匹配更长名称）
            {
                "keywords": ("工作餐", "早午餐", "便餐", "食堂"),
                "slot": {"start_hour": 11, "end_hour": 14},
                "duration": (25, 60),
            },
            {
                "keywords": ("午餐", "晚饭", "晚餐", "早餐", "聚餐", "家宴", "叙旧"),
                "slot": {"start_hour": 11, "end_hour": 21},
                "duration": (30, 90),
            },
            # 短同步 / 一对一
            {
                "keywords": ("站会", "简短", "同步会", "daily", "scrum"),
                "slot": {"start_hour": 9, "end_hour": 19},
                "duration": (10, 35),
            },
            {
                "keywords": ("一对一", "1对1", "两人沟通", "双人"),
                "slot": {"start_hour": 9, "end_hour": 20},
                "duration": (20, 55),
            },
            # 碎片事务
            {
                "keywords": ("邮件", "邮箱", "快递", "待办", "清单", "备忘录", "收件箱", "缴纳", "记账"),
                "slot": {"start_hour": 8, "end_hour": 21},
                "duration": (10, 35),
            },
            {
                "keywords": ("午休", "午睡"),
                "slot": {"start_hour": 12, "end_hour": 15}, # 午休时间
                "duration": (20, 45),
            },
            {
                "keywords": ("散步", "漫步", "遛狗", "闲逛", "走走"),
                "slot": {"start_hour": 7, "end_hour": 21},
                "duration": (20, 55),
            },
            # 会议/工作协作：工作时段
            {
                "keywords": ("会议", "周会", "评审", "沟通", "讨论", "对齐", "复盘", "读书会"),
                "slot": {"start_hour": 9, "end_hour": 19},
                "duration": (30, 120),
            },
            # 运动：早晚更常见
            {
                "keywords": ("跑步", "健身", "瑜伽", "游泳", "骑行", "徒步", "爬山", "太极", "力量训练"),
                "slot": {"start_hour": 6, "end_hour": 21},
                "duration": (20, 60),
            },
            # 早间类
            {
                "keywords": ("晨间", "早起", "清晨", "晨跑", "晨练", "早间", "日出"),
                "slot": {"start_hour": 6, "end_hour": 11},
                "duration": (20, 120),
            },
            # 下午茶
            {
                "keywords": ("下午茶",),
                "slot": {"start_hour": 14, "end_hour": 19},
                "duration": (30, 120),
            },
            # 明显长事件
            {
                "keywords": ("露营", "自驾游", "黑客马拉松", "项目冲刺"),
                "slot": {"start_hour": 9, "end_hour": 24},
                "duration": (120, 240),
            },
        ]

        def _match_rule(event_name):
            name_norm = str(event_name or "").strip().lower()
            for rule in time_duration_rules:
                if any(k in name_norm for k in rule["keywords"]):
                    return rule
            return None

        def _event_slot_by_name(event_type, event_name):
            # 优先使用约束表（精确 / 包含匹配）
            constraint = _lookup_activity_constraint(event_name)
            if constraint and constraint.get("slots"):
                return dict(random.choice(constraint["slots"]))
            # 其次使用关键词规则
            rule = _match_rule(event_name)
            if rule and rule.get("slot"):
                return dict(rule["slot"])
            return dict(time_slots[event_type])

        def _event_duration_by_name(event_type, event_name):
            # 优先使用约束表（精确 / 包含匹配）
            constraint = _lookup_activity_constraint(event_name)
            if constraint and constraint.get("duration"):
                lo, hi = constraint["duration"]
                return random.randint(int(lo), int(hi))
            # 其次使用关键词规则
            rule = _match_rule(event_name)
            if rule and rule.get("duration"):
                lo, hi = rule["duration"]
                return random.randint(int(lo), int(hi))

            if event_type == "meeting":
                if dims.get("sensitivity") == "H":
                    return random.randint(15, 40)
                # 70% 短会(15-45 min)，30% 较长(46-75 min)
                return random.randint(15, 45) if random.random() < 0.7 else random.randint(46, 75)
            if event_type == "task":
                # 60% 短任务(15-45 min)，40% 较长(46-90 min)
                return random.randint(15, 45) if random.random() < 0.6 else random.randint(46, 90)
            if event_type == "activity":
                return random.randint(20, 75)
            # appointment / 其他：15-50 min
            return random.randint(15, 50)

        def _refine_duration_for_event_name(d_in, event_name, event_type):
            """对已采样时长按事件名收紧，减少与名称常识明显不符的时长。"""
            nm = str(event_name or "")
            d = int(d_in)
            if any(k in nm for k in ("泡脚", "拉伸", "正念", "呼吸练习", "手指操")) and "课" not in nm and "课程" not in nm:
                d = min(d, random.randint(15, 40))
            if any(k in nm for k in ("瑜伽", "冥想")) and "课" not in nm and "课程" not in nm and "尼德拉" not in nm:
                d = min(d, random.randint(25, 55))
            if "周会" in nm or "月度" in nm or "季度" in nm or "规划讨论" in nm:
                d = max(40, min(d, 100))
            if event_type == "appointment" and any(
                k in nm for k in ("疫苗", "洁牙", "理发", "配眼镜", "配镜", "取件")
            ):
                d = min(d, random.randint(25, 75))
            return max(10, d)

        def _merge_minute_intervals(intervals):
            """合并分钟区间，避免重叠/相邻区间重复占位。"""
            if not intervals:
                return []
            ordered = sorted(intervals, key=lambda x: (x[0], x[1]))
            merged = [list(ordered[0])]
            for s, e in ordered[1:]:
                last = merged[-1]
                if s <= last[1]:
                    last[1] = max(last[1], e)
                else:
                    merged.append([s, e])
            return [(int(s), int(e)) for s, e in merged]

        def _build_sleep_blocked_slots_by_date():
            """
            从 health_data 的 raw_data 中提取睡眠禁排窗口（卧床->起床），
            UTC 转本地（+8）后按自然日拆分为分钟区间。
            """
            health_file = os.path.join(self.output_dir, f"{user_id}_health_data.json")
            if not os.path.exists(health_file):
                return {}
            try:
                with open(health_file, "r", encoding="utf-8") as f:
                    health_list = json.load(f)
            except Exception:
                return {}

            blocked = {}

            def _append_by_day(start_local, end_local):
                day_cursor = start_local.date()
                last_day = end_local.date()
                while day_cursor <= last_day:
                    day_start = datetime.combine(day_cursor, datetime.min.time())
                    day_end = day_start + timedelta(days=1)
                    seg_start = max(start_local, day_start)
                    seg_end = min(end_local, day_end)
                    if seg_end > seg_start:
                        s_min = int((seg_start - day_start).total_seconds() // 60)
                        e_min = int((seg_end - day_start).total_seconds() // 60)
                        s_min = max(0, min(24 * 60, s_min))
                        e_min = max(0, min(24 * 60, e_min))
                        if e_min > s_min:
                            day_key = day_cursor.strftime("%Y-%m-%d")
                            blocked.setdefault(day_key, []).append((s_min, e_min))
                    day_cursor += timedelta(days=1)

            for item in health_list if isinstance(health_list, list) else []:
                raw = item.get("raw_data", {}) if isinstance(item, dict) else {}
                if not raw:
                    continue

                bed_utc = raw.get("bed_time") or raw.get("sleep_time")
                wake_utc = raw.get("wake_up_time") or raw.get("wake_time")
                if not bed_utc or not wake_utc:
                    continue

                try:
                    bed_local = _parse_utc_iso_to_local_dt(str(bed_utc))
                    wake_local = _parse_utc_iso_to_local_dt(str(wake_utc))
                except Exception:
                    continue
                if not bed_local or not wake_local:
                    continue
                if wake_local <= bed_local:
                    wake_local += timedelta(days=1)

                _append_by_day(bed_local, wake_local)

            return {k: _merge_minute_intervals(v) for k, v in blocked.items()}

        sleep_blocked_slots_by_date = _build_sleep_blocked_slots_by_date()

        current_date = start_date
        day_index = 0
        while current_date <= end_date:
            target_events_for_day = daily_event_targets[day_index] if day_index < len(daily_event_targets) else 2
            # 根据目标条数给出更匹配的时长预算，避免被预算过早截断
            daily_minutes_budget_min = max(150, target_events_for_day * 45)
            daily_minutes_budget_max = max(daily_minutes_budget_min + 40, target_events_for_day * 110)
            daily_minutes_budget = random.randint(daily_minutes_budget_min, daily_minutes_budget_max)
            # 记录当天已占用的时间段 [(start_minutes, end_minutes), ...]
            date_key = current_date.strftime("%Y-%m-%d")
            occupied_slots = list(sleep_blocked_slots_by_date.get(date_key, []))
            # 当天已累计的总分钟数
            total_minutes_today = 0
            # 当天总分钟数上限（从配置读取，None 表示不限制）
            max_total_today = random.randint(cfg_total_min, cfg_total_max) if cfg_total_min and cfg_total_max else None
            if max_total_today is None:
                max_total_today = daily_minutes_budget
            else:
                max_total_today = min(max_total_today, daily_minutes_budget)

            generated_count_today = 0
            attempts = 0
            max_attempts = max(30, target_events_for_day * 15)
            while generated_count_today < target_events_for_day and attempts < max_attempts:
                attempts += 1
                # 按权重选择事件类型
                event_type = random.choice(weighted_types)
                event_name = _pick_schedule_event_name(event_type)
                if _is_excluded_event_name(event_name):
                    continue

                # 根据事件名分类时间段（如 KTV 强制夜间）
                slot = _event_slot_by_name(event_type, event_name)

                # 根据事件类型生成合理的持续时间
                duration_minutes = _event_duration_by_name(event_type, event_name)
                duration_minutes = _refine_duration_for_event_name(
                    duration_minutes, event_name, event_type
                )

                remaining_minutes = max_total_today - total_minutes_today
                if remaining_minutes < 25:
                    break
                if duration_minutes > remaining_minutes:
                    rule_fit = _match_rule(event_name)
                    hi_cap = duration_minutes
                    if rule_fit and rule_fit.get("duration"):
                        _, hi_r = rule_fit["duration"]
                        hi_cap = min(hi_cap, int(hi_r))
                    duration_minutes = min(int(hi_cap), int(remaining_minutes))
                    duration_minutes = max(10, int(duration_minutes))
                    if duration_minutes < 12:
                        continue

                # 尝试找一个不冲突的开始时间（最多重试20次）
                start_time = None
                for _ in range(20):
                    max_end_minutes = slot["end_hour"] * 60
                    latest_start_hour = slot["end_hour"] - max(1, math.ceil(duration_minutes / 60))
                    if latest_start_hour < slot["start_hour"]:
                        break

                    candidate_hour = random.randint(slot["start_hour"], latest_start_hour)
                    candidate_minute = random.choice([0, 15, 30, 45])
                    candidate_start = candidate_hour * 60 + candidate_minute
                    candidate_end = candidate_start + duration_minutes
                    if candidate_end > max_end_minutes or candidate_end > 24 * 60:
                        continue

                    # 检查是否与已有时间段冲突
                    conflict = any(
                        candidate_start < occ_end and candidate_end > occ_start
                        for occ_start, occ_end in occupied_slots
                    )
                    if not conflict:
                        start_time = f"{candidate_hour:02d}:{candidate_minute:02d}"
                        occupied_slots.append((candidate_start, candidate_end))
                        break

                # 20次都冲突则跳过这条事件
                if start_time is None:
                    continue

                # 超出当天总分钟数上限则跳过
                if max_total_today is not None and total_minutes_today + duration_minutes > max_total_today:
                    continue

                # 计算结束时间
                start_datetime = datetime.combine(current_date, datetime.strptime(start_time, '%H:%M').time())
                end_datetime = start_datetime + timedelta(minutes=duration_minutes)
                end_time = end_datetime.strftime('%H:%M')

                create_time = (datetime.now() - timedelta(hours=8)).isoformat() + "Z"

                schedule_data = {
                    "uid": user_id,
                    "event_date": current_date.strftime('%Y-%m-%d'),
                    "event_type": event_type,
                    "event_name": event_name,
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration_minutes": duration_minutes,
                    "create_time": create_time,
                    "update_time": create_time,
                    "language": "zh"
                }

                total_minutes_today += duration_minutes
                generated_count_today += 1
                schedule_data_list.append(schedule_data)

            current_date += timedelta(days=1)
            day_index += 1

        # 收尾补齐：若因冲突/预算导致总量偏低，补到人格目标总量
        def _hhmm_to_minutes(hhmm):
            try:
                hh, mm = map(int, str(hhmm).split(":"))
                return hh * 60 + mm
            except Exception:
                return 0

        if len(schedule_data_list) < cycle_target_events:
            daily_count_map = {}
            occupied_by_date = {}
            for item in schedule_data_list:
                dkey = item.get("event_date")
                if not dkey:
                    continue
                daily_count_map[dkey] = daily_count_map.get(dkey, 0) + 1
                s_min = _hhmm_to_minutes(item.get("start_time", "00:00"))
                e_min = _hhmm_to_minutes(item.get("end_time", "00:00"))
                if e_min <= s_min:
                    e_min += 24 * 60
                occupied_by_date.setdefault(dkey, []).append((s_min, e_min))

            # 合并当天睡眠禁排与已排活动占位
            for dkey, slots in sleep_blocked_slots_by_date.items():
                occupied_by_date.setdefault(dkey, []).extend(list(slots))
            for dkey, slots in list(occupied_by_date.items()):
                occupied_by_date[dkey] = _merge_minute_intervals(slots)

            gap = cycle_target_events - len(schedule_data_list)
            date_cursor = start_date
            all_dates = []
            while date_cursor <= end_date:
                all_dates.append(date_cursor)
                date_cursor += timedelta(days=1)
            random.shuffle(all_dates)

            # 夜型补齐时优先活动/社交，晨型优先活动/任务
            if dims.get("chronotype") == "E":
                topup_types = ["activity", "meeting", "task", "appointment"]
            else:
                topup_types = ["activity", "task", "meeting", "appointment"]

            for d in all_dates:
                if gap <= 0:
                    break
                dkey = d.strftime("%Y-%m-%d")
                used = daily_count_map.get(dkey, 0)
                if used >= per_day_cap:
                    continue

                slots = list(occupied_by_date.get(dkey, []))
                can_add = min(per_day_cap - used, gap)
                added_today = 0
                attempts = 0
                while added_today < can_add and attempts < can_add * 12:
                    attempts += 1
                    etype = random.choice(topup_types)
                    event_name = _pick_schedule_event_name(etype)
                    if _is_excluded_event_name(event_name):
                        continue

                    slot = _event_slot_by_name(etype, event_name)
                    duration_minutes = random.randint(20, 55)
                    duration_minutes = _refine_duration_for_event_name(
                        duration_minutes, event_name, etype
                    )
                    duration_minutes = max(15, min(75, int(duration_minutes)))

                    start_candidates = list(range(slot["start_hour"] * 60, slot["end_hour"] * 60 - duration_minutes + 1, 15))
                    random.shuffle(start_candidates)
                    placed = False
                    for cand_start in start_candidates[:40]:
                        cand_end = cand_start + duration_minutes
                        if cand_end > 24 * 60:
                            continue
                        conflict = any(
                            cand_start < occ_end and cand_end > occ_start
                            for occ_start, occ_end in slots
                        )
                        if conflict:
                            continue
                        start_time = f"{cand_start // 60:02d}:{cand_start % 60:02d}"
                        start_datetime = datetime.combine(d, datetime.strptime(start_time, '%H:%M').time())
                        end_datetime = start_datetime + timedelta(minutes=duration_minutes)
                        end_time = end_datetime.strftime('%H:%M')
                        create_time = (datetime.now() - timedelta(hours=8)).isoformat() + "Z"
                        schedule_data_list.append({
                            "uid": user_id,
                            "event_date": dkey,
                            "event_type": etype,
                            "event_name": event_name,
                            "start_time": start_time,
                            "end_time": end_time,
                            "duration_minutes": duration_minutes,
                            "create_time": create_time,
                            "update_time": create_time,
                            "language": "zh"
                        })
                        slots.append((cand_start, cand_end))
                        slots = _merge_minute_intervals(slots)
                        occupied_by_date[dkey] = slots
                        daily_count_map[dkey] = daily_count_map.get(dkey, 0) + 1
                        gap -= 1
                        added_today += 1
                        placed = True
                        break
                    if not placed:
                        continue

        return schedule_data_list


    def _generate_sleep_plan_with_qwen(self):
        """使用通义千问（DashScope）生成睡眠方案数据"""
        print("正在调用通义千问生成睡眠方案数据...")

        # 构造提示词
        prompt = render_prompt_template("generate_health_data__sleep_plan.md")

        # 调用 DashScope OpenAI 兼容接口
        api_key = _qwen_api_key()
        if not api_key:
            raise ValueError("DASHSCOPE_API_KEY 环境变量未设置（或兼容读取 DOUBAO_API_KEY）")
    
        model_name = _qwen_model_name()
    
        response = requests.post(
            _qwen_chat_url(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            json={
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.7,
                "max_tokens": _QWEN_MAX_TOKENS,
                "enable_thinking": False,
            },
            timeout=_QWEN_TIMEOUT,
        )
    
        # 解析响应
        response_data = response.json()
    
        # 检查是否有错误
        if 'error' in response_data:
            error_msg = response_data['error'].get('message', '未知错误')
            error_code = response_data['error'].get('code', '未知错误码')
            print(f"API错误: [{error_code}] {error_msg}")
            raise ValueError(f"通义千问 API 调用失败: {error_msg}")
    
        if 'choices' not in response_data or not response_data['choices']:
            print(f"错误：响应中没有'choices'字段，实际响应: {response_data}")
            raise ValueError("模型返回数据格式错误")
    
        content = response_data['choices'][0]['message']['content']
    
        # 清理可能的Markdown格式
        content = content.strip()
        if content.startswith('```json'):
            content = content[7:].strip()
        if content.endswith('```'):
            content = content[:-3].strip()
    
        # 解析JSON
        sleep_plan_data = json.loads(content)
    
        # 验证生成数据格式
        if not isinstance(sleep_plan_data, dict):
            raise ValueError("模型返回的数据不是JSON对象")
    
        print("已生成睡眠方案数据")
        return sleep_plan_data

    def generate_sleep_plan_data(self, session_id, uid, count):
        """生成睡眠改善方案数据
        
        方案是针对用户当前睡眠问题的改善建议，时间安排比用户实际作息更理想：
        - 晚睡型(E)用户：引导提前入睡
        - 高敏感(H)用户：更长的放松阶段帮助脱敏
        - 高活跃(R)用户：更长的放松过渡帮助大脑安静
        - 入睡阶段时长是改善目标，比实际潜伏期短
        
        阶段顺序：放松 → 入睡 → 守护 → 唤醒
        
        Args:
            session_id: 会话ID
            uid: 用户ID
            count: 生成数据的数量
            
        Returns:
            睡眠方案数据列表
        """
        # 检查睡眠方案数据是否已生成
        output_file = os.path.join(self.output_dir, f"{uid}_sleep_plan_data.json")
        if os.path.exists(output_file):
            print(f"用户 {uid} 的睡眠方案数据已生成，跳过")
            with open(output_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        # 查找用户配置
        user_config = None
        user_personality_type = None
        for user in self.users:
            if user.get('user_id') == uid:
                user_config = user
                personal_info = user.get('personalInformation', {})
                user_personality_type = personal_info.get('type', 'M-L-C')
                break
        
        if not user_personality_type:
            user_personality_type = 'M-L-C'
        
        mhr_codes = user_personality_type.split('-') if user_personality_type else []
        
        # 从 MongoDB 获取人格对应的 scenes 数据
        import pymongo

        mongodb_uri = os.getenv('MONGODB_URI')
        client = pymongo.MongoClient(mongodb_uri)
        db = client['Fullive']
        quiz_personalities_collection = db['quiz_personalities']
        
        personality_data = quiz_personalities_collection.find_one({"mhr_codes": mhr_codes})
        
        periods = []
        if personality_data:
            periods = personality_data.get('periods', [])
        else:
            print(f"未找到人格代码为 {mhr_codes} 的数据，使用默认数据")
            periods = [
                {"phase": "relax", "name": "睡前放松练习", "scenes": ["深呼吸练习", "渐进式肌肉放松"]},
                {"phase": "fall_asleep", "name": "规律作息时间表", "scenes": ["固定 bedtime", "睡前阅读"]},
                {"phase": "guard", "name": "环境优化建议", "scenes": ["保持房间黑暗", "适宜温度"]},
                {"phase": "wake", "name": "早晨唤醒", "scenes": ["自然光唤醒", "温和伸展"]}
            ]
        
        client.close()
        
        # 获取人格维度
        dims = parse_personality_code(user_personality_type)
        profile = get_personality_profile(user_personality_type)
        
        # 从用户配置中获取当前实际入睡时间范围
        sleep_time_range = user_config.get('sleepTime', {'min': ['22:00'], 'max': ['23:00']}) if user_config else {'min': ['22:00'], 'max': ['23:00']}
        min_sleep_str = sleep_time_range['min'][0]
        max_sleep_str = sleep_time_range['max'][0]
        min_sleep_hour, min_sleep_minute = map(int, min_sleep_str.split(':'))
        max_sleep_hour, max_sleep_minute = map(int, max_sleep_str.split(':'))
        
        # 从用户配置中获取当前实际起床时间范围
        awake_time_range = user_config.get('awakeTime', {'min': ['07:00'], 'max': ['08:00']}) if user_config else {'min': ['07:00'], 'max': ['08:00']}
        min_awake_str = awake_time_range['min'][0]
        max_awake_str = awake_time_range['max'][0]
        min_awake_hour, min_awake_minute = map(int, min_awake_str.split(':'))
        max_awake_hour, max_awake_minute = map(int, max_awake_str.split(':'))
        
        # ============================================================
        # 改善方案时间计算
        # ============================================================
        
        # 1. 计算改善目标入睡时间：E型用户提前30-60分钟
        if dims['chronotype'] == 'E':
            advance_minutes = random.randint(30, 60)
        else:
            advance_minutes = 0
        
        # 2. 放松阶段持续时间（改善方案需要充分的放松）
        if dims['sensitivity'] == 'H' and dims['brain_activity'] == 'R':
            # 高敏感+高活跃：最需要放松，方案给予最长放松时间
            relax_duration = random.randint(25, 35)
        elif dims['sensitivity'] == 'H':
            # 高敏感：需要环境脱敏和身体放松
            relax_duration = random.randint(20, 30)
        elif dims['brain_activity'] == 'R':
            # 高活跃：需要大脑降温过渡
            relax_duration = random.randint(15, 25)
        else:
            # 低敏感+低活跃：简单放松仪式即可
            relax_duration = random.randint(10, 15)
        
        # 3. 入睡阶段持续时间（改善目标，比实际潜伏期短）
        actual_latency = profile["sleep_latency"]
        target_latency_min = max(5, int(actual_latency["min"] * 0.5))
        target_latency_max = max(10, min(20, int(actual_latency["max"] * 0.7)))
        if target_latency_min > target_latency_max:
            target_latency_min = target_latency_max
        fall_asleep_duration = random.randint(target_latency_min, target_latency_max)
        
        # 4. 唤醒阶段持续时间
        if dims['chronotype'] == 'E':
            # 晚睡型起床困难，方案给予更长的渐进唤醒
            wake_duration = random.randint(20, 30)
        elif dims['sensitivity'] == 'H':
            # 高敏感：温和唤醒
            wake_duration = random.randint(15, 20)
        else:
            # 正常唤醒
            wake_duration = random.randint(10, 15)
        
        # 5. 计算改善目标起床时间：E型用户提前15-30分钟
        if dims['chronotype'] == 'E':
            wake_advance_minutes = random.randint(15, 30)
        else:
            wake_advance_minutes = 0
        
        phase_order = ["relax", "fall_asleep", "guard", "wake"]
        phase_names = {"relax": "放松", "fall_asleep": "入睡", "guard": "守护", "wake": "清醒"}
        
        sleep_plan_list = []
        
        for _ in range(count):
            # 方案取入睡范围较早端附近作为基准
            if min_sleep_hour <= max_sleep_hour:
                target_sleep_hour = min_sleep_hour
                target_sleep_minute = min_sleep_minute + random.randint(0, 15)
                if target_sleep_minute >= 60:
                    target_sleep_hour += 1
                    target_sleep_minute -= 60
            else:
                # 跨午夜，方案取较早端（即min端）
                target_sleep_hour = min_sleep_hour
                target_sleep_minute = min_sleep_minute + random.randint(0, 15)
                if target_sleep_minute >= 60:
                    target_sleep_hour += 1
                    if target_sleep_hour >= 24:
                        target_sleep_hour -= 24
                    target_sleep_minute -= 60
            
            # 应用提前量（E型用户的改善目标）
            base_date = datetime.now().replace(hour=target_sleep_hour, minute=target_sleep_minute, second=0, microsecond=0)
            target_sleep_time = base_date - timedelta(minutes=advance_minutes)
            
            # 放松阶段开始 = 目标入睡时间 - 放松时长 - 入睡时长
            relax_start = target_sleep_time - timedelta(minutes=relax_duration + fall_asleep_duration)
            
            # 入睡阶段开始 = 放松结束
            fall_asleep_start = relax_start + timedelta(minutes=relax_duration)
            
            # 守护阶段开始 = 入睡结束
            guard_start = fall_asleep_start + timedelta(minutes=fall_asleep_duration)
            
            # 生成改善目标起床时间（取范围较早端并应用提前量）
            target_awake_hour = min_awake_hour
            target_awake_minute = min_awake_minute + random.randint(0, 15)
            if target_awake_minute >= 60:
                target_awake_hour += 1
                target_awake_minute -= 60
            
            wake_up_datetime = (base_date + timedelta(days=1)).replace(hour=target_awake_hour, minute=target_awake_minute)
            wake_up_datetime = wake_up_datetime - timedelta(minutes=wake_advance_minutes)
            
            # 确保起床时间在入睡之后
            if wake_up_datetime < guard_start:
                wake_up_datetime += timedelta(days=1)
            
            # 唤醒阶段开始 = 起床时间 - 唤醒时长
            wake_start = wake_up_datetime - timedelta(minutes=wake_duration)
            
            # 守护阶段持续时间 = 唤醒开始 - 守护开始
            guard_duration = int((wake_start - guard_start).total_seconds() / 60)
            if guard_duration < 0:
                guard_duration += 24 * 60
            
            phase_times = {
                "relax": {"start": relax_start, "duration": relax_duration},
                "fall_asleep": {"start": fall_asleep_start, "duration": fall_asleep_duration},
                "guard": {"start": guard_start, "duration": guard_duration},
                "wake": {"start": wake_start, "duration": wake_duration},
            }
            
            phases = []
            for phase in phase_order:
                start_time = phase_times[phase]["start"].strftime('%H:%M')
                duration = phase_times[phase]["duration"]
                
                scenes = []
                for period in periods:
                    if period.get('phase') == phase:
                        scenes = period.get('scenes', [])
                        break
                
                phase_data = {
                    "phase": phase,
                    "phase_name": phase_names[phase],
                    "start_time": start_time,
                    "duration_minutes": duration,
                    "scenes": scenes
                }
                phases.append(phase_data)
            
            create_time = (datetime.now() - timedelta(hours=8)).isoformat() + "Z"
            
            complete_sleep_plan = {
                "session_id": "",
                "uid": uid,
                "title": "今晚专属睡眠方案",
                "subtitle": "基于今日高负荷,已为您定制针对性的恢复方案",
                "phases": phases,
                "is_started": True,
                "create_time": create_time,
                "update_time": create_time
            }
            
            sleep_plan_list.append(complete_sleep_plan)
        
        return sleep_plan_list
    
    def _generate_fitness_data_from_config(self, user_id, user, start_date, end_date):
        """基于配置文件中的体征信息生成体征数据

        使用人格编码驱动锚点基线与日间小幅波动；锚点固定，不按天随机游走，长期不会漂到区间两端。
        """
        fitness_config = user.get('fitness', {})
        personality_type = user.get('personalInformation', {}).get('type', 'M-L-C')
        dims = parse_personality_code(personality_type)

        # 提取配置范围
        hr_min = fitness_config.get('heartRate', {'min': [60], 'max': [100]})['min'][0]
        hr_max = fitness_config.get('heartRate', {'min': [60], 'max': [100]})['max'][0]
        sys_min = fitness_config.get('systolicPressure', {'min': [90], 'max': [140]})['min'][0]
        sys_max = fitness_config.get('systolicPressure', {'min': [90], 'max': [140]})['max'][0]
        dia_min = fitness_config.get('diastolicPressure', {'min': [60], 'max': [90]})['min'][0]
        dia_max = fitness_config.get('diastolicPressure', {'min': [60], 'max': [90]})['max'][0]
        bo_min = fitness_config.get('bloodOxygen', {'min': [90], 'max': [100]})['min'][0]
        bo_max = fitness_config.get('bloodOxygen', {'min': [90], 'max': [100]})['max'][0]

        # 根据人格编码确定基线值（在配置范围的中间偏向某一侧）
        # 高活跃(R)用户心率基线偏高，低活跃(C)偏低
        if dims['brain_activity'] == 'R':
            base_hr = int(hr_min + (hr_max - hr_min) * random.uniform(0.55, 0.75))
        else:
            base_hr = int(hr_min + (hr_max - hr_min) * random.uniform(0.3, 0.5))

        # 高敏感(H)用户血压基线偏高（交感神经活跃）
        if dims['sensitivity'] == 'H':
            base_sys = int(sys_min + (sys_max - sys_min) * random.uniform(0.5, 0.7))
            base_dia = int(dia_min + (dia_max - dia_min) * random.uniform(0.5, 0.7))
        else:
            base_sys = int(sys_min + (sys_max - sys_min) * random.uniform(0.3, 0.55))
            base_dia = int(dia_min + (dia_max - dia_min) * random.uniform(0.3, 0.55))

        # 血氧基线：所有用户血氧应保持在96以上
        # 低敏感用户血氧基线偏高且稳定
        if dims['sensitivity'] == 'L':
            base_bo = int(bo_min + (bo_max - bo_min) * random.uniform(0.6, 0.85))
        else:
            base_bo = int(bo_min + (bo_max - bo_min) * random.uniform(0.3, 0.6))

        # 根据人格编码确定日间波动幅度（在锚点周围小幅摆动，不按天累积漂移）
        if dims['sensitivity'] == 'H' and dims['brain_activity'] == 'R':
            hr_drift, sys_drift, dia_drift, bo_drift = 3, 6, 4, 2
        elif dims['sensitivity'] == 'H':
            hr_drift, sys_drift, dia_drift, bo_drift = 2, 5, 3, 1
        elif dims['brain_activity'] == 'R':
            hr_drift, sys_drift, dia_drift, bo_drift = 2, 4, 3, 1
        else:
            hr_drift, sys_drift, dia_drift, bo_drift = 1, 3, 2, 1

        anchor_hr, anchor_sys, anchor_dia, anchor_bo = base_hr, base_sys, base_dia, base_bo

        fitness_data_list = []
        current_date = start_date

        while current_date <= end_date:
            stress_day = random.random() < 0.055
            hr_pulse = random.randint(6, 14) if stress_day else 0
            sys_pulse = random.randint(4, 11) if stress_day else 0
            dia_pulse = random.randint(2, 6) if stress_day else 0

            heart_rate = max(
                hr_min,
                min(hr_max, anchor_hr + hr_pulse + random.randint(-hr_drift, hr_drift)),
            )
            systolic = max(
                sys_min,
                min(sys_max, anchor_sys + sys_pulse + random.randint(-sys_drift, sys_drift)),
            )
            diastolic = max(
                dia_min,
                min(dia_max, anchor_dia + dia_pulse + random.randint(-dia_drift, dia_drift)),
            )
            blood_oxy = max(bo_min, min(bo_max, anchor_bo + random.randint(-bo_drift, bo_drift)))

            if systolic <= diastolic:
                systolic = diastolic + random.randint(20, 40)
                systolic = min(sys_max, systolic)

            fitness_data_list.append({
                "record_date": current_date.strftime('%Y-%m-%d'),
                "heart_rate": heart_rate,
                "systolic_pressure": systolic,
                "diastolicPressure": diastolic,
                "bloodOxygen": blood_oxy
            })

            current_date += timedelta(days=1)

        print(f"已生成用户 {user_id} 的体征数据，共{len(fitness_data_list)}条")
        return fitness_data_list

    def generate_fitness_data(self, user_id, user, start_date=None, end_date=None):
        """基于配置文件生成体征数据

        Args:
            user_id: 用户ID
            user: 用户配置信息
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            体征数据列表
        """
        # 检查体征数据是否已生成
        output_file = os.path.join(self.output_dir, f"{user_id}_fitness_data.json")
        if os.path.exists(output_file):
            print(f"用户 {user_id} 的体征数据已生成，跳过")
            with open(output_file, 'r', encoding='utf-8') as f:
                return json.load(f)

        fitness_config = user.get('fitness', {})
        personality_type = user.get('personalInformation', {}).get('type', 'M-L-C')
        dims = parse_personality_code(personality_type)

        # 提取配置范围
        hr_min = fitness_config.get('heartRate', {'min': [60], 'max': [100]})['min'][0]
        hr_max = fitness_config.get('heartRate', {'min': [60], 'max': [100]})['max'][0]
        sys_min = fitness_config.get('systolicPressure', {'min': [90], 'max': [140]})['min'][0]
        sys_max = fitness_config.get('systolicPressure', {'min': [90], 'max': [140]})['max'][0]
        dia_min = fitness_config.get('diastolicPressure', {'min': [60], 'max': [90]})['min'][0]
        dia_max = fitness_config.get('diastolicPressure', {'min': [60], 'max': [90]})['max'][0]
        bo_min = fitness_config.get('bloodOxygen', {'min': [90], 'max': [100]})['min'][0]
        bo_max = fitness_config.get('bloodOxygen', {'min': [90], 'max': [100]})['max'][0]

        if start_date and end_date:
            # 使用配置驱动的方法生成多天数据
            generated_data = self._generate_fitness_data_from_config(user_id, user, start_date, end_date)

            fitness_data_list = []
            for item in generated_data:
                record_date = item.get('record_date')
                if not record_date:
                    continue

                current_date = datetime.strptime(record_date, '%Y-%m-%d')
                if dims.get("chronotype") == "E":
                    hour = random.randint(9, 11)
                else:
                    hour = random.randint(6, 9)
                minute = random.randint(0, 59)
                second = random.randint(0, 59)
                timestamp = current_date.replace(hour=hour, minute=minute, second=second)
                timestamp_utc = timestamp - timedelta(hours=8)

                fitness_data = {
                    "uid": user_id,
                    "record_date": record_date,
                    "timestamp": timestamp_utc.isoformat() + "Z",
                    "raw_data": {
                        "heart_rate": item.get('heart_rate', 75),
                        "systolic_pressure": item.get('systolic_pressure', 110),
                        "diastolicPressure": item.get('diastolicPressure', 70),
                        "bloodOxygen": item.get('bloodOxygen', 95)
                    }
                }
                fitness_data_list.append(fitness_data)

            return fitness_data_list
        else:
            # 生成单个数据点，根据人格编码选取基线
            if dims['brain_activity'] == 'R':
                heart_rate = int(hr_min + (hr_max - hr_min) * random.uniform(0.55, 0.75))
            else:
                heart_rate = int(hr_min + (hr_max - hr_min) * random.uniform(0.3, 0.5))

            if dims['sensitivity'] == 'H':
                systolic_pressure = int(sys_min + (sys_max - sys_min) * random.uniform(0.5, 0.7))
                diastolic_pressure = int(dia_min + (dia_max - dia_min) * random.uniform(0.5, 0.7))
            else:
                systolic_pressure = int(sys_min + (sys_max - sys_min) * random.uniform(0.3, 0.55))
                diastolic_pressure = int(dia_min + (dia_max - dia_min) * random.uniform(0.3, 0.55))

            if dims['sensitivity'] == 'L':
                blood_oxygen = int(bo_min + (bo_max - bo_min) * random.uniform(0.6, 0.85))
            else:
                blood_oxygen = int(bo_min + (bo_max - bo_min) * random.uniform(0.3, 0.6))

            # 确保收缩压 > 舒张压
            if systolic_pressure <= diastolic_pressure:
                systolic_pressure = diastolic_pressure + random.randint(20, 40)
                systolic_pressure = min(sys_max, systolic_pressure)

            timestamp = datetime.now()
            timestamp_utc = timestamp - timedelta(hours=8)

            fitness_data = {
                "uid": user_id,
                "record_date": timestamp.strftime('%Y-%m-%d'),
                "timestamp": timestamp_utc.isoformat() + "Z",
                "raw_data": {
                    "heart_rate": heart_rate,
                    "systolic_pressure": systolic_pressure,
                    "diastolicPressure": diastolic_pressure,
                    "bloodOxygen": blood_oxygen
                }
            }

            return [fitness_data]
    
    def generate_health_data(self):
        """生成健康数据"""
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        
        for user in self.users:
            user_id = user.get('user_id')
            
            # 检查健康数据是否已生成
            output_file = os.path.join(self.output_dir, f"{user_id}_health_data.json")
            if os.path.exists(output_file):
                print(f"用户 {user_id} 的健康数据已生成，跳过")
                continue
            
            user_profile = user.get('sleepProfile', '健康')
            
            # 获取用户配置的日期范围
            date_config = user.get('date', {})
            start_date_str = date_config.get('start', None)
            end_date_str = date_config.get('end', None)
            
            if start_date_str and end_date_str:
                # 解析日期范围
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
                
                # 生成日期范围内的所有日期
                current_date = start_date
                health_data_list = []
                personality_type = user.get("personalInformation", {}).get("type", "M-L-C")
                sleep_outlier_by_date = build_sleep_outlier_mode_by_date(
                    start_date, end_date, personality_type
                )
                prev_day_front_rem = False

                def _has_front_rem_in_idf(day_data):
                    idf = day_data.get("idf_data") or []
                    raw = day_data.get("raw_data") or {}
                    sleep_iso = raw.get("sleep_time")
                    wake_iso = raw.get("wake_time")
                    if not idf or not sleep_iso or not wake_iso:
                        return False
                    try:
                        # raw_data 中时间是 UTC；idf_data 的 HH:MM 是本地时区（+8）
                        sleep_dt = datetime.fromisoformat(sleep_iso.replace("Z", "")) + timedelta(hours=8)
                        wake_dt = datetime.fromisoformat(wake_iso.replace("Z", "")) + timedelta(hours=8)
                    except Exception:
                        return False
                    if wake_dt <= sleep_dt:
                        wake_dt += timedelta(days=1)
                    half_dt = sleep_dt + (wake_dt - sleep_dt) / 2
                    anchor = sleep_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                    sleep_hm = sleep_dt.hour * 60 + sleep_dt.minute

                    for seg in idf:
                        if seg.get("stage") != "rem":
                            continue
                        try:
                            sh, sm = map(int, str(seg.get("start", "00:00")).split(":"))
                        except Exception:
                            continue
                        s_minutes = sh * 60 + sm
                        s_dt = anchor + timedelta(minutes=s_minutes)
                        if s_minutes < sleep_hm:
                            s_dt += timedelta(days=1)
                        if s_dt < half_dt:
                            return True
                    return False

                def _clock_minutes_between(start_hhmm, end_hhmm):
                    try:
                        sh, sm = map(int, str(start_hhmm).split(":"))
                        eh, em = map(int, str(end_hhmm).split(":"))
                    except Exception:
                        return 0
                    s = sh * 60 + sm
                    e = eh * 60 + em
                    if e < s:
                        e += 24 * 60
                    return max(0, e - s)

                def _idf_to_timeline(idf):
                    timeline = []
                    for seg in idf:
                        d = _clock_minutes_between(seg.get("start", "00:00"), seg.get("end", "00:00"))
                        if d > 0:
                            timeline.extend([seg.get("stage", "light")] * d)
                    return timeline

                def _timeline_to_idf(timeline, first_start_hhmm):
                    if not timeline:
                        return []
                    try:
                        h, m = map(int, str(first_start_hhmm).split(":"))
                    except Exception:
                        h, m = 22, 0
                    base = h * 60 + m
                    out = []
                    i = 0
                    n = len(timeline)
                    while i < n:
                        st = timeline[i]
                        j = i + 1
                        while j < n and timeline[j] == st:
                            j += 1
                        s = (base + i) % (24 * 60)
                        e = (base + j) % (24 * 60)
                        out.append(
                            {
                                "stage": st,
                                "start": f"{s // 60:02d}:{s % 60:02d}",
                                "end": f"{e // 60:02d}:{e % 60:02d}",
                            }
                        )
                        i = j
                    # 消除极短非 awake 片段（< 3 分钟）
                    _MIN_SEG = 3

                    def _dur2(seg):
                        sh2, sm2 = map(int, seg["start"].split(":"))
                        eh2, em2 = map(int, seg["end"].split(":"))
                        d2 = (eh2 * 60 + em2) - (sh2 * 60 + sm2)
                        return d2 if d2 >= 0 else d2 + 1440

                    changed = True
                    while changed:
                        changed = False
                        k = 0
                        while k < len(out):
                            if out[k]["stage"] != "awake" and _dur2(out[k]) < _MIN_SEG:
                                if k > 0:
                                    out[k - 1]["end"] = out[k]["end"]
                                    out.pop(k)
                                    if k <= len(out) - 1 and out[k - 1]["stage"] == out[k]["stage"]:
                                        out[k - 1]["end"] = out[k]["end"]
                                        out.pop(k)
                                elif k + 1 < len(out):
                                    out[k + 1]["start"] = out[k]["start"]
                                    out.pop(k)
                                else:
                                    k += 1
                                    continue
                                changed = True
                            else:
                                k += 1
                    return out

                def _front_window(day_data, timeline_len):
                    raw = day_data.get("raw_data") or {}
                    idf = day_data.get("idf_data") or []
                    sleep_iso = raw.get("sleep_time")
                    wake_iso = raw.get("wake_time")
                    if not sleep_iso or not wake_iso or not idf:
                        return (0, 0)
                    try:
                        # raw_data 中时间是 UTC；与 idf_data 的本地 HH:MM 比较时先转本地时区（+8）
                        sleep_dt = datetime.fromisoformat(sleep_iso.replace("Z", "")) + timedelta(hours=8)
                        wake_dt = datetime.fromisoformat(wake_iso.replace("Z", "")) + timedelta(hours=8)
                        sh, sm = map(int, str(idf[0].get("start", "22:00")).split(":"))
                    except Exception:
                        return (0, 0)
                    if wake_dt <= sleep_dt:
                        wake_dt += timedelta(days=1)
                    sleep_window = int((wake_dt - sleep_dt).total_seconds() / 60)
                    # 以前半夜窗口按“idf首段起点 -> sleep_time”的实际分钟差定位，避免 sleep_latency 与分段重排后错位
                    first_start = sleep_dt.replace(hour=sh, minute=sm, second=0, microsecond=0)
                    if first_start > sleep_dt:
                        first_start -= timedelta(days=1)
                    fs = max(0, int((sleep_dt - first_start).total_seconds() / 60))
                    fe = min(timeline_len, fs + max(1, sleep_window // 2))
                    return (fs, fe)

                def _find_front_rem_runs(timeline, fs, fe):
                    runs = []
                    i = fs
                    while i < fe:
                        if timeline[i] == "rem":
                            j = i + 1
                            while j < fe and timeline[j] == "rem":
                                j += 1
                            runs.append((i, j))
                            i = j
                        else:
                            i += 1
                    return runs

                def _remove_front_rem(day_data):
                    idf = day_data.get("idf_data") or []
                    if not idf:
                        return False
                    timeline = _idf_to_timeline(idf)
                    if not timeline:
                        return False
                    fs, fe = _front_window(day_data, len(timeline))
                    changed = False
                    for i in range(fs, fe):
                        if timeline[i] == "rem":
                            timeline[i] = "light"
                            changed = True
                    if changed:
                        day_data["idf_data"] = _timeline_to_idf(timeline, idf[0].get("start", "22:00"))
                    return changed

                def _ensure_front_rem_1_2(day_data):
                    idf = day_data.get("idf_data") or []
                    if not idf:
                        return False
                    timeline = _idf_to_timeline(idf)
                    if not timeline:
                        return False
                    fs, fe = _front_window(day_data, len(timeline))
                    if fe - fs < 10:
                        return False

                    target_runs = 1 if random.random() < 0.72 else 2
                    runs = _find_front_rem_runs(timeline, fs, fe)

                    # 过多则裁到 2 段
                    if len(runs) > 2:
                        for a, b in runs[2:]:
                            for i in range(a, b):
                                timeline[i] = "light"
                        runs = _find_front_rem_runs(timeline, fs, fe)

                    # 不足则补
                    tries = 0
                    while len(runs) < target_runs and tries < 220:
                        tries += 1
                        dur = random.randint(3, 6)
                        pos = random.randint(fs + 1, max(fs + 1, fe - dur - 1))
                        if timeline[pos - 1] == "rem" or timeline[pos + dur] == "rem":
                            continue
                        block = timeline[pos:pos + dur]
                        if not block:
                            continue
                        # 优先在 light 上插入短 rem，保持结构稳定
                        if any(s != "light" for s in block):
                            continue
                        timeline[pos:pos + dur] = ["rem"] * dur
                        runs = _find_front_rem_runs(timeline, fs, fe)

                    # 仍不足时，放宽到 deep/light 混合块
                    tries = 0
                    while len(runs) < target_runs and tries < 180:
                        tries += 1
                        dur = random.randint(3, 5)
                        pos = random.randint(fs + 1, max(fs + 1, fe - dur - 1))
                        if timeline[pos - 1] == "rem" or timeline[pos + dur] == "rem":
                            continue
                        if any(s == "awake" for s in timeline[pos:pos + dur]):
                            continue
                        timeline[pos:pos + dur] = ["rem"] * dur
                        runs = _find_front_rem_runs(timeline, fs, fe)

                    # 保底裁成最多 2 段
                    runs = _find_front_rem_runs(timeline, fs, fe)
                    if len(runs) > 2:
                        for a, b in runs[2:]:
                            for i in range(a, b):
                                timeline[i] = "light"

                    # 终极兜底：若前半夜仍无 REM，强制插入 1 段短 REM（3-5 分钟，避开 awake）
                    runs = _find_front_rem_runs(timeline, fs, fe)
                    if not runs:
                        dur = 4
                        placed = False
                        for pos in range(fs + 2, max(fs + 2, fe - dur - 1)):
                            if timeline[pos - 1] == "rem" or timeline[pos + dur] == "rem":
                                continue
                            block = timeline[pos:pos + dur]
                            if len(block) < dur:
                                continue
                            if any(s == "awake" for s in block):
                                continue
                            timeline[pos:pos + dur] = ["rem"] * dur
                            placed = True
                            break
                        if not placed and fe - fs >= 3:
                            # 最后退路：即便前半夜很拥挤，也强制放一个极短 REM 段
                            pos = min(fe - 3, fs + 2)
                            timeline[pos:pos + 3] = ["rem", "rem", "rem"]

                    day_data["idf_data"] = _timeline_to_idf(timeline, idf[0].get("start", "22:00"))
                    return True

                while current_date <= end_date:
                    # 生成当天的睡眠数据
                    rec = current_date.strftime("%Y-%m-%d")
                    mode = sleep_outlier_by_date.get(rec)
                    sleep_data = self.generate_sleep_data(
                        user_id,
                        user_profile,
                        user,
                        current_date,
                        sleep_outlier_mode=mode,
                        prev_front_rem=prev_day_front_rem,
                    )
                    health_data_list.append(sleep_data)
                    prev_day_front_rem = _has_front_rem_in_idf(sleep_data)
                    
                    # 移动到下一天
                    current_date += timedelta(days=1)

                # 强制规则：恰好 40 条数据在前半夜出现 1-2 段 REM，且按日期不连续
                target_front_rem_days = min(40, len(health_data_list))
                n_days = len(health_data_list)
                selected = set()
                available = set(range(n_days))
                while len(selected) < target_front_rem_days and available:
                    i = random.choice(tuple(available))
                    selected.add(i)
                    for j in (i - 1, i, i + 1):
                        if j in available:
                            available.remove(j)

                # 若随机贪心未凑够，按顺序补齐到上限（仍保证不连续）
                if len(selected) < target_front_rem_days:
                    for i in range(n_days):
                        if len(selected) >= target_front_rem_days:
                            break
                        if i in selected or (i - 1) in selected or (i + 1) in selected:
                            continue
                        selected.add(i)

                # 对选中日期强制 1-2 段前半夜 REM；未选中日期清掉前半夜 REM
                for i, day in enumerate(health_data_list):
                    if i in selected:
                        _ensure_front_rem_1_2(day)
                    else:
                        _remove_front_rem(day)

                strip_sleep_calendar_flags_from_health_records(health_data_list)
                # 保存数据到文件
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(health_data_list, f, ensure_ascii=False, indent=2)

                print(f"已生成用户 {user_id} 的健康数据，共{len(health_data_list)}条，保存到 {output_file}")
            else:
                # 如果没有配置日期范围，只生成一条数据
                # sleep_data = self.generate_sleep_data(user_id, user_profile, user)
                # 
                # # 保存数据到文件
                # output_file = os.path.join(self.output_dir, f"{user_id}_health_data.json")
                # with open(output_file, 'w', encoding='utf-8') as f:
                #     json.dump(sleep_data, f, ensure_ascii=False, indent=2)
                # 
                # print(f"已生成用户 {user_id} 的健康数据，保存到 {output_file}")
                pass
        
        print("健康数据生成完成！")

    def load_sleep_data(self, user_id):
        sleep_data_path = f'output/{user_id}_health_data.json'
        if os.path.exists(sleep_data_path):
            with open(sleep_data_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []

    def load_fitness_data(self, user_id):
        fitness_data_path = f'output/{user_id}_fitness_data.json'
        if os.path.exists(fitness_data_path):
            with open(fitness_data_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []

    def generate_single_fitness_data(self, user_id, record_date, config):
        """基于配置文件为指定日期生成体征数据"""
        user_profile = None
        for profile in config['user_profiles']:
            if profile['user_id'] == user_id:
                user_profile = profile
                break

        if not user_profile:
            return None

        fitness_config = user_profile.get('fitness', {})
        personality_type = user_profile.get('personalInformation', {}).get('type', 'M-L-C')
        dims = parse_personality_code(personality_type)

        hr_min = fitness_config.get('heartRate', {'min': [60], 'max': [100]})['min'][0]
        hr_max = fitness_config.get('heartRate', {'min': [60], 'max': [100]})['max'][0]
        sys_min = fitness_config.get('systolicPressure', {'min': [90], 'max': [140]})['min'][0]
        sys_max = fitness_config.get('systolicPressure', {'min': [90], 'max': [140]})['max'][0]
        dia_min = fitness_config.get('diastolicPressure', {'min': [60], 'max': [90]})['min'][0]
        dia_max = fitness_config.get('diastolicPressure', {'min': [60], 'max': [90]})['max'][0]
        bo_min = fitness_config.get('bloodOxygen', {'min': [90], 'max': [100]})['min'][0]
        bo_max = fitness_config.get('bloodOxygen', {'min': [90], 'max': [100]})['max'][0]

        # 根据人格编码选取基线
        if dims['brain_activity'] == 'R':
            heart_rate = int(hr_min + (hr_max - hr_min) * random.uniform(0.55, 0.75))
        else:
            heart_rate = int(hr_min + (hr_max - hr_min) * random.uniform(0.3, 0.5))

        if dims['sensitivity'] == 'H':
            systolic_pressure = int(sys_min + (sys_max - sys_min) * random.uniform(0.5, 0.7))
            diastolic_pressure = int(dia_min + (dia_max - dia_min) * random.uniform(0.5, 0.7))
        else:
            systolic_pressure = int(sys_min + (sys_max - sys_min) * random.uniform(0.3, 0.55))
            diastolic_pressure = int(dia_min + (dia_max - dia_min) * random.uniform(0.3, 0.55))

        if dims['sensitivity'] == 'L':
            blood_oxygen = int(bo_min + (bo_max - bo_min) * random.uniform(0.6, 0.85))
        else:
            blood_oxygen = int(bo_min + (bo_max - bo_min) * random.uniform(0.3, 0.6))

        # 确保收缩压 > 舒张压
        if systolic_pressure <= diastolic_pressure:
            systolic_pressure = diastolic_pressure + random.randint(20, 40)
            systolic_pressure = min(sys_max, systolic_pressure)

        return {
            "heart_rate": heart_rate,
            "systolic_pressure": systolic_pressure,
            "diastolicPressure": diastolic_pressure,
            "bloodOxygen": blood_oxygen
        }

    def calculate_body_motion_level(self, turnover_count, personality_type='M-L-C'):
        # 根据翻身次数和人格编码生成体动幅度
        # 量级与文档对齐：natural_movement 15-35，once_movement 25-60，movement(异常) 40-85
        dims = parse_personality_code(personality_type)
        is_sensitive = dims['sensitivity'] == 'H'

        # 基础体动等级（根据翻身次数，对应文档量级）
        if turnover_count < 15:
            base_level = 18   # 自然微动区间下沿
        elif turnover_count < 25:
            base_level = 28   # 单次体动低端
        elif turnover_count < 35:
            base_level = 38   # 单次体动中段
        elif turnover_count < 45:
            base_level = 50   # 姿势切换中段
        elif turnover_count < 55:
            base_level = 62   # 肢体动作高段
        elif turnover_count < 65:
            base_level = 72   # 肢体动作顶端
        else:
            base_level = 80   # 异常体动区间

        # 高敏感用户体动幅度+10（微觉醒导致的额外体动）
        if is_sensitive:
            base_level = min(85, base_level + 10)

        return base_level

    def generate_hrv(self, sleep_data, personality_type='M-L-C'):
        # 根据睡眠健康数据和人格编码生成 5 分钟短程 HRV 指标（1.5-2.0）
        recent_sleep = sleep_data[0] if sleep_data else {}
        raw_data = recent_sleep.get('raw_data', {})
        
        average_heartbeat = raw_data.get('average_heartbeat', 70)
        sleep_score = raw_data.get('sleep_score', 70)
        deep_sleep_ratio = raw_data.get('deep_sleep_ratio', 20)
        awake_ratio = raw_data.get('awake_ratio', 10)

        hrv_lo, hrv_hi = 1.5, 2.0

        # 基线取人格配置中心值，再叠加当晚睡眠质量修正（适配设备间隔采样）
        base_hrv = (hrv_lo + hrv_hi) / 2.0
        heart_rate_factor = (70.0 - float(average_heartbeat)) * 0.003
        sleep_score_factor = (float(sleep_score) - 75.0) * 0.0015
        deep_sleep_factor = (float(deep_sleep_ratio) - 20.0) * 0.004
        awake_factor = (8.0 - float(awake_ratio)) * 0.004

        # 人格调整因子原先是整数档位，缩放到短程 HRV 指标量级
        hrv_adj = float(get_hrv_adjustment(personality_type)) * 0.004

        hrv = base_hrv + heart_rate_factor + sleep_score_factor + deep_sleep_factor + awake_factor + hrv_adj
        hrv = max(hrv_lo, min(hrv_hi, hrv))
        return round(hrv, 3)

    def generate_vital_signs(self, sleep_data, user_id, config):
        # 检查体征数据（vitals_data）是否已生成
        output_file = os.path.join(self.output_dir, f"{user_id}_vitals_data.json")
        if os.path.exists(output_file):
            print(f"用户 {user_id} 的体征数据 (vitals_data) 已生成，跳过")
            # 读取并返回已生成的数据
            with open(output_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        vital_signs = []
        
        # 获取用户的配置信息
        user_profile = None
        for profile in config['user_profiles']:
            if profile['user_id'] == user_id:
                user_profile = profile
                break
        
        if not user_profile:
            return []
        
        personality_type = user_profile.get('personalInformation', {}).get('type', 'M-L-C')
        fitness_config = user_profile.get('fitness', {})
        allowed_codes = allowed_sleep_event_codes_from_profile(user_profile)

        hr_lo = fitness_config.get('heartRate', {'min': [60], 'max': [100]})['min'][0]
        hr_hi = fitness_config.get('heartRate', {'min': [60], 'max': [100]})['max'][0]
        sys_lo = fitness_config.get('systolicPressure', {'min': [90], 'max': [140]})['min'][0]
        sys_hi = fitness_config.get('systolicPressure', {'min': [90], 'max': [140]})['max'][0]
        dia_lo = fitness_config.get('diastolicPressure', {'min': [60], 'max': [90]})['min'][0]
        dia_hi = fitness_config.get('diastolicPressure', {'min': [60], 'max': [90]})['max'][0]
        bo_lo = fitness_config.get('bloodOxygen', {'min': [90], 'max': [100]})['min'][0]
        bo_hi = fitness_config.get('bloodOxygen', {'min': [90], 'max': [100]})['max'][0]
        rr_lo = fitness_config.get('breathingRate', {'min': [12], 'max': [20]})['min'][0]
        rr_hi = fitness_config.get('breathingRate', {'min': [12], 'max': [20]})['max'][0]
        mot_lo = fitness_config.get('bodyMovement', {'min': [1], 'max': [100]})['min'][0]
        mot_hi = fitness_config.get('bodyMovement', {'min': [1], 'max': [100]})['max'][0]
        if hr_hi < hr_lo:
            hr_lo, hr_hi = hr_hi, hr_lo
        if sys_hi < sys_lo:
            sys_lo, sys_hi = sys_hi, sys_lo
        if dia_hi < dia_lo:
            dia_lo, dia_hi = dia_hi, dia_lo
        if bo_hi < bo_lo:
            bo_lo, bo_hi = bo_hi, bo_lo
        if rr_hi < rr_lo:
            rr_lo, rr_hi = rr_hi, rr_lo
        if mot_hi < mot_lo:
            mot_lo, mot_hi = mot_hi, mot_lo

        # 加载体征数据
        fitness_data = self.load_fitness_data(user_id)
        # 创建日期到体征数据的映射
        fitness_data_by_date = {}
        for item in fitness_data:
            fitness_data_by_date[item['record_date']] = item['raw_data']
        
        for sleep_record in sleep_data:
            record_date = sleep_record['record_date']
            raw_data = sleep_record['raw_data']
            
            # 生成多条数据（时刻落在卧床～起床，本地采样；collected_at 输出 UTC）
            num_records = 15
            base_date = datetime.strptime(record_date, '%Y-%m-%d')
            sleep_window_start, sleep_window_end = _extract_local_sleep_window(
                raw_data, start_key='bed_time', end_key='wake_up_time'
            )
            if sleep_window_start and sleep_window_end:
                collected_local_dts = _sample_local_dts_sleep_window_spaced(
                    sleep_window_start,
                    sleep_window_end,
                    num_records,
                    min_gap_minutes=MIN_VITAL_ENV_COLLECTED_AT_GAP_MINUTES,
                )
            else:
                g_min = MIN_VITAL_ENV_COLLECTED_AT_GAP_MINUTES
                collected_local_dts = [
                    base_date + timedelta(hours=2) + timedelta(minutes=g_min * i)
                    for i in range(num_records)
                ]

            idf_for_align = sleep_record.get("idf_data") or []
            if idf_for_align and sleep_window_start and sleep_window_end:
                collected_local_dts = _align_collected_local_dts_last_to_idf_end(
                    collected_local_dts,
                    idf_for_align,
                    sleep_window_start,
                    sleep_window_end,
                )

            hr_spike_idxs = set()
            if _profile_allows_hr_spike_vitals(allowed_codes) and collected_local_dts:
                mid_start = sleep_window_start + timedelta(hours=1) if sleep_window_start else None
                mid_end = sleep_window_end - timedelta(hours=1) if sleep_window_end else None
                mid_pool = []
                if mid_start and mid_end and mid_end > mid_start:
                    for j, dt0 in enumerate(collected_local_dts):
                        if mid_start <= dt0 <= mid_end:
                            mid_pool.append(j)
                # apnea_count 越高，心率脉冲点越多（模拟觉醒后心率上升）
                _apnea_extra = int(raw_data.get('apnea_count', 0) or 0)
                _n_spikes = 2 + (1 if _apnea_extra >= 5 else 0) + (1 if _apnea_extra >= 10 else 0)
                _n_spikes = min(_n_spikes, len(mid_pool) if mid_pool else len(collected_local_dts))
                if len(mid_pool) >= _n_spikes:
                    hr_spike_idxs.update(random.sample(mid_pool, _n_spikes))
                elif len(mid_pool) >= 1:
                    hr_spike_idxs.update(mid_pool)
                elif len(collected_local_dts) >= 2:
                    hr_spike_idxs.update(
                        random.sample(range(len(collected_local_dts)), min(_n_spikes, len(collected_local_dts)))
                    )
                else:
                    hr_spike_idxs.add(0)
            
            # 从睡眠数据中提取呼吸率，并以人格配置范围约束
            respiration_rate = raw_data.get('average_respiration', (rr_lo + rr_hi) / 2.0)
            respiration_rate = max(rr_lo, min(rr_hi, float(respiration_rate)))
            
            # 从体征数据中获取心率、血氧、血压
            fitness_record = fitness_data_by_date.get(record_date, {})
            
            # 如果没有找到对应日期的体征数据，按规则生成体征数据
            if not fitness_record:
                print(f"为用户 {user_id} 的日期 {record_date} 生成体征数据...")
                fitness_record = self.generate_single_fitness_data(user_id, record_date, config)
                if fitness_record:
                    # 将生成的数据添加到映射中，以便后续使用
                    fitness_data_by_date[record_date] = fitness_record
                else:
                    # 如果生成失败，使用默认值
                    fitness_record = {}
            
            heart_rate = max(hr_lo, min(hr_hi, fitness_record.get('heart_rate', 70)))
            blood_oxygen = max(bo_lo, min(bo_hi, fitness_record.get('bloodOxygen', 95)))
            blood_pressure_systolic = max(sys_lo, min(sys_hi, fitness_record.get('systolic_pressure', 120)))
            blood_pressure_diastolic = max(dia_lo, min(dia_hi, fitness_record.get('diastolicPressure', 80)))
            
            # 生成体动幅度（当夜基线；每条采样再轻微抖动）
            turnover_count = raw_data.get('turnover_count', 0)
            base_motion = self.calculate_body_motion_level(turnover_count, personality_type)

            # 呼吸暂停次数：≥5次时夜间血氧整体下移、心率脉冲更频繁
            apnea_count = int(raw_data.get('apnea_count', 0) or 0)
            # 血氧基线偏移：轻度(5-9次) -1，中度(10-19次) -2，重度(≥20次) -3
            if apnea_count >= 20:
                apnea_bo_offset = -3
            elif apnea_count >= 10:
                apnea_bo_offset = -2
            elif apnea_count >= 5:
                apnea_bo_offset = -1
            else:
                apnea_bo_offset = 0

            # 按当晚睡眠生成 5 分钟短程 HRV（避免整条时间轴都用第一晚）
            hrv_lo, hrv_hi = 1.5, 2.0
            hrv_base = self.generate_hrv([sleep_record], personality_type)

            stage_windows = {}
            if sleep_window_start and sleep_window_end:
                stage_windows = _build_stage_windows_from_idf(
                    sleep_record.get("idf_data") or [],
                    sleep_window_start,
                    sleep_window_end,
                )
            awake_windows = _idf_awake_windows_minutes(sleep_record.get("idf_data") or []) + _raw_awake_windows_minutes(raw_data)

            idf_end_utc = None
            if idf_for_align and sleep_window_start and sleep_window_end:
                idf_end_utc = _idf_last_segment_end_utc_iso_z(
                    idf_for_align, sleep_window_start, sleep_window_end
                )
            n_collect = len(collected_local_dts)

            bed_for_session = sleep_window_start
            sleep_onset_local = _sleep_onset_local_naive(raw_data, bed_for_session)
            wake_end_local = sleep_window_end

            # 为每个时间点生成一条记录（分期驱动心率/呼吸/HRV/体动形态，并夹在配置范围内）
            for idx, collected_at_local in enumerate(collected_local_dts):
                stage = _sleep_stage_at_local_dt(collected_at_local, stage_windows)
                if not stage and awake_windows and _is_in_awake_window(collected_at_local, awake_windows):
                    stage = "awake"
                # 参见 docs/睡眠阶段对体征影响的指导性文档.md：清醒高 HR 低 HRV；入睡过渡降 HR 升 HRV；
                # 浅睡稳定基线；深睡最低 HR/RR、高 HRV；REM 心率与 HRV 高波动。
                hr_stage_adj = {"deep": -22, "light": -8, "rem": 6, "awake": 18}
                rr_stage_adj = {"deep": -2.0, "light": -0.35, "rem": 1.8, "awake": 2.2}
                hrv_stage_adj = {"deep": 0.11, "light": 0.025, "rem": 0.0, "awake": -0.11}
                mot_stage_adj = {"deep": -12, "light": -2, "rem": 2, "awake": 20}

                hr_adj = hr_stage_adj.get(stage, -6)
                rr_adj = rr_stage_adj.get(stage, -0.25)
                hrv_adj = hrv_stage_adj.get(stage, 0.01)
                mot_adj = mot_stage_adj.get(stage, -2)

                circ_t = _circadian_progress_t_rel(
                    collected_at_local,
                    sleep_onset_local,
                    wake_end_local,
                    bed_for_session,
                )
                circ = _circadian_vitals_offsets(circ_t)
                pre = _presleep_transition_offsets(
                    collected_at_local, bed_for_session, sleep_onset_local
                )

                hr_noise = random.randint(-2, 2)
                rr_noise = random.uniform(-0.75, 0.75)
                hrv_noise = random.uniform(-0.03, 0.03)
                if stage == "rem":
                    hr_noise += int(round(random.uniform(-8, 10)))
                    rr_noise += random.uniform(-3.2, 3.8)
                    hrv_noise += random.uniform(-0.09, 0.11)
                elif stage == "deep":
                    hr_noise = int(round(random.uniform(-1.5, 1.5)))
                    rr_noise *= 0.45
                    hrv_noise *= 0.35
                elif stage == "light":
                    hr_noise = int(round(random.uniform(-2, 2)))
                elif stage == "awake":
                    hr_noise += int(round(random.uniform(-3, 5)))
                    hrv_noise += random.uniform(-0.025, 0.025)

                row_hr = max(
                    hr_lo,
                    min(
                        hr_hi,
                        heart_rate
                        + hr_adj
                        + hr_noise
                        + circ["hr"]
                        + pre["hr"],
                    ),
                )
                if idx in hr_spike_idxs:
                    row_hr = max(
                        hr_lo,
                        min(hr_hi, row_hr + random.randint(14, 30)),
                    )

                row_bo = max(bo_lo, min(bo_hi, blood_oxygen + apnea_bo_offset + random.randint(-1, 1)))
                sys_stage_adj = {"deep": -10, "light": -6, "rem": 5, "awake": 8}
                dia_stage_adj = {"deep": -6, "light": -4, "rem": 4, "awake": 5}
                s_adj = sys_stage_adj.get(stage, -4)
                d_adj = dia_stage_adj.get(stage, -3)
                row_sys = max(
                    sys_lo,
                    min(
                        sys_hi,
                        int(
                            round(
                                blood_pressure_systolic
                                + s_adj
                                + random.randint(-2, 2)
                                + circ["sys"]
                            )
                        ),
                    ),
                )
                row_dia = max(
                    dia_lo,
                    min(
                        dia_hi,
                        int(
                            round(
                                blood_pressure_diastolic
                                + d_adj
                                + random.randint(-2, 2)
                                + circ["dia"]
                            )
                        ),
                    ),
                )
                if row_sys <= row_dia:
                    row_sys = min(sys_hi, row_dia + random.randint(20, 35))

                row_rr = max(
                    rr_lo,
                    min(
                        rr_hi,
                        int(
                            round(
                                respiration_rate
                                + rr_adj
                                + rr_noise
                                + circ["rr"]
                                + pre["rr"]
                            )
                        ),
                    ),
                )
                row_hrv = round(
                    max(
                        hrv_lo,
                        min(
                            hrv_hi,
                            hrv_base
                            + hrv_adj
                            + hrv_noise
                            + circ["hrv"]
                            + pre["hrv"],
                        ),
                    ),
                    3,
                )
                mot_noise = random.randint(-5, 5)
                if stage == "rem":
                    mot_noise = random.randint(0, 10)
                row_motion = max(
                    mot_lo,
                    min(
                        mot_hi,
                        base_motion
                        + mot_adj
                        + mot_noise
                        + circ["motion"]
                        + pre["motion"],
                    ),
                )

                create_time = collected_at_local + timedelta(seconds=1)
                update_time = create_time
                collected_at_utc_z = local_naive_dt_to_utc_iso_z(collected_at_local)
                if idf_end_utc and n_collect > 0 and idx == n_collect - 1:
                    collected_at_utc_z = idf_end_utc

                metrics_core = {
                    "respiration_rate": row_rr,
                    "heart_rate": row_hr,
                    "body_motion_level": row_motion,
                    "blood_oxygen": row_bo,
                    "blood_pressure_systolic": row_sys,
                    "blood_pressure_diastolic": row_dia,
                    "hrv": row_hrv,
                }

                vital_sign = {
                    "uid": user_id,
                    "record_date": record_date,
                    "collected_at": collected_at_utc_z,
                    "data_source": "radar",
                    "metrics": metrics_core,
                    "device_id": "",
                    "create_time": create_time.strftime('%Y-%m-%d %H:%M:%S'),
                    "update_time": update_time.strftime('%Y-%m-%d %H:%M:%S')
                }

                vital_signs.append(vital_sign)
        
        return vital_signs

def generate_personality_user_mapping():
    """生成personality_user_mapping数据"""
    # 确保output目录存在
    os.makedirs('output', exist_ok=True)
    
    # 加载配置文件
    config_file = 'config/config.json'
    if not os.path.exists(config_file):
        print(f"配置文件 {config_file} 不存在")
        return
    
    with open(config_file, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 遍历所有用户
    for user in config.get('user_profiles', []):
        user_id = user.get('user_id')
        
        # 检查问卷结果文件是否存在
        quiz_result_file = f'output/{user_id}_quiz_result.json'
        if os.path.exists(quiz_result_file):
            with open(quiz_result_file, 'r', encoding='utf-8') as f:
                quiz_result = json.load(f)
            # 从问卷结果中获取mhr_codes和survey_code
            mhr_codes = quiz_result.get('mhr_codes', [])
            survey_code = quiz_result.get('survey_code', 'somni_001')
        else:
            # 默认值
            mhr_codes = []
            survey_code = 'somni_001'
        
        # 生成create_time和update_time
        create_time = generate_iso_date()
        update_time = create_time
        
        # 构建personality_user_mapping数据
        personality_user_mapping = {
            "mhr_codes": mhr_codes,
            "uid": user_id,
            "survey_code": survey_code,
            "create_time": create_time,
            "update_time": update_time
        }
        
        # 保存到文件
        output_file = f'output/{user_id}_personality_user_mapping.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(personality_user_mapping, f, ensure_ascii=False, indent=2)
        
        print(f"已生成用户 {user_id} 的personality_user_mapping数据，保存到 {output_file}")



# 生成真正的 MongoDB ObjectId
def generate_object_id():
    """生成一个真正的 MongoDB ObjectId"""
    return str(ObjectId())

# 模拟 MongoDB ISODate
def generate_iso_date():
    """生成一个 ISODate 格式的字符串"""
    return datetime.now().isoformat() + "Z"

# 解析时间字符串为 datetime 对象
def parse_time(time_str, format='%H:%M'):
    """解析时间字符串为 datetime 对象"""
    return datetime.strptime(time_str, format)

# 格式化 datetime 对象为时间字符串
def format_time(dt, format='%H:%M'):
    """格式化 datetime 对象为时间字符串"""
    return dt.strftime(format)


def _parse_utc_iso_to_local_dt(utc_iso_str):
    """将形如 2026-04-10T15:30:00Z 的 UTC 字符串转换为本地 naive datetime(UTC+8)。"""
    if not utc_iso_str:
        return None
    dt_utc = datetime.fromisoformat(utc_iso_str.replace('Z', ''))
    return dt_utc + timedelta(hours=8)


def local_naive_dt_to_utc_iso_z(dt_local):
    """东八区墙钟 naive 时间转 UTC，输出带 Z 的 ISO 字符串（供 collected_at）。"""
    if dt_local is None:
        return ""
    dt_utc = dt_local - timedelta(hours=8)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def collected_at_to_local_naive_dt(collected_at_str):
    """解析 vitals/environment 的 collected_at：支持 UTC ISO(Z) 或历史本地 'YYYY-MM-DD HH:MM:SS'。"""
    if not collected_at_str or not str(collected_at_str).strip():
        return None
    s = str(collected_at_str).strip()
    if s.endswith("Z") or "T" in s:
        return _parse_utc_iso_to_local_dt(s)
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def format_sleep_event_local_timestamp(dt_local):
    """睡眠事件 event_timestamp：本地「几点几分」（与体征/环境同一墙钟时刻的时分）。"""
    if dt_local is None:
        return ""
    return dt_local.strftime("%H:%M")


def parse_sleep_event_timestamp_to_dt(event):
    """将睡眠事件的 event_timestamp 转为可排序的 datetime（本地语义）。"""
    ts = (event.get("event_timestamp") or "").strip()
    rd = event.get("record_date") or ""
    if len(ts) >= 16 and ts[4] == "-" and (" " in ts or "T" in ts):
        try:
            return datetime.strptime(ts.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    if len(ts) == 5 and ts[2] == ":":
        try:
            return datetime.strptime(f"{rd} {ts}", "%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return datetime.min


def session_anchor_event_local_dt(event, sleep_start=None, window_end=None):
    """
    event.record_date 与健康记录日一致（跨午夜仍用入睡当日）；event_timestamp（HH:MM）
    通过 ±1 日候选对齐到睡眠窗内的本地绝对时间。
    无睡眠窗信息时，退化为 record_date 当日 + 时分。
    """
    ts = (event.get("event_timestamp") or "").strip()
    rd = event.get("record_date") or ""
    if len(ts) >= 16 and ts[4] == "-" and (" " in ts or "T" in ts):
        try:
            return datetime.strptime(ts.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    if len(ts) != 5 or ts[2] != ":" or not rd:
        return parse_sleep_event_timestamp_to_dt(event)
    try:
        tpart = datetime.strptime(ts, "%H:%M").time()
        d0 = datetime.strptime(rd[:10], "%Y-%m-%d").date()
        c0 = datetime.combine(d0, tpart)
    except ValueError:
        return parse_sleep_event_timestamp_to_dt(event)
    if sleep_start is None or window_end is None:
        return c0
    candidates = [c0, c0 + timedelta(days=1), c0 - timedelta(days=1)]
    in_win = [c for c in candidates if sleep_start <= c <= window_end]
    if len(in_win) == 1:
        return in_win[0]
    if len(in_win) > 1:
        return min(in_win)
    return min(candidates, key=lambda c: abs((c - sleep_start).total_seconds()))


def _sample_local_dt_in_sleep_interval(sleep_start_local, sleep_end_local):
    """在真实入睡区间 [sleep_start_local, sleep_end_local] 内均匀采样（支持跨午夜）。"""
    if not sleep_start_local or not sleep_end_local:
        return None
    span = (sleep_end_local - sleep_start_local).total_seconds()
    if span <= 0:
        return sleep_start_local
    off = random.randint(0, int(span))
    return sleep_start_local + timedelta(seconds=off)


def _sample_local_dts_sleep_window_spaced(
    sleep_start_local, sleep_end_local, n, min_gap_minutes=MIN_VITAL_ENV_COLLECTED_AT_GAP_MINUTES
):
    """
    在睡眠窗内生成 n 个本地时刻，相邻两条间隔至少 min_gap_minutes（默认与体征/环境采样一致）。
    若睡眠窗过短不足以放下 n 条，则自动减少条数以满足最小间隔，而不是缩短间隔。
    """
    if n <= 0:
        return []
    if not sleep_start_local or not sleep_end_local:
        return []
    span_sec = (sleep_end_local - sleep_start_local).total_seconds()
    if span_sec <= 0:
        return [sleep_start_local] * n
    min_gap_sec = float(min_gap_minutes) * 60.0
    if min_gap_sec <= 0:
        min_gap_sec = float(MIN_VITAL_ENV_COLLECTED_AT_GAP_MINUTES) * 60.0
    if n > 1 and min_gap_sec > 0:
        max_n = int(span_sec // min_gap_sec) + 1
        max_n = max(1, max_n)
        if n > max_n:
            n = max_n
    if n == 1:
        t = _sample_local_dt_in_sleep_interval(sleep_start_local, sleep_end_local)
        return [t if t is not None else sleep_start_local]
    max_total_gap = (n - 1) * min_gap_sec
    slack = span_sec - max_total_gap
    if slack < 0:
        slack = 0.0
    pieces = [random.random() for _ in range(n + 1)]
    s = sum(pieces) or 1.0
    pieces = [p / s * slack for p in pieces]
    t = sleep_start_local + timedelta(seconds=pieces[0])
    out = [t]
    for i in range(1, n):
        t = t + timedelta(seconds=min_gap_sec + pieces[i])
        if t > sleep_end_local:
            t = sleep_end_local
        out.append(t)
    return out


def _sample_local_dt_on_record_date(record_date_str, window_start_local, window_end_local):
    """
    在 [window_start_local, window_end_local] 的“时间点范围”内采样，
    并将日期固定为 record_date（满足 collected_at 年月日对齐 record_date）。
    """
    base_date = datetime.strptime(record_date_str, '%Y-%m-%d')
    if not window_start_local or not window_end_local:
        return base_date + timedelta(hours=2, minutes=32)

    start_sec = (
        window_start_local.hour * 3600
        + window_start_local.minute * 60
        + window_start_local.second
    )
    end_sec = (
        window_end_local.hour * 3600
        + window_end_local.minute * 60
        + window_end_local.second
    )

    # 跨午夜窗口，例如 23:30 -> 07:10
    if end_sec < start_sec:
        span = (24 * 3600 - start_sec) + end_sec
        if span <= 0:
            picked_sec = start_sec
        else:
            offset = random.randint(0, span)
            picked_sec = (start_sec + offset) % (24 * 3600)
    else:
        span = end_sec - start_sec
        picked_sec = start_sec if span <= 0 else random.randint(start_sec, end_sec)

    hour = picked_sec // 3600
    minute = (picked_sec % 3600) // 60
    second = picked_sec % 60
    return base_date.replace(hour=hour, minute=minute, second=second, microsecond=0)


def _extract_local_sleep_window(raw_data, start_key='sleep_time', end_key='wake_up_time'):
    """从 sleep raw_data 提取本地睡眠窗口，起点可选 bed_time/sleep_time，终点可选 wake_time/wake_up_time。"""
    raw_data = raw_data or {}
    sleep_local = _parse_utc_iso_to_local_dt(raw_data.get(start_key, ''))
    if not sleep_local and start_key != 'sleep_time':
        sleep_local = _parse_utc_iso_to_local_dt(raw_data.get('sleep_time', ''))
    end_local = _parse_utc_iso_to_local_dt(raw_data.get(end_key, ''))
    if not sleep_local:
        return None, None
    if not end_local:
        end_local = _parse_utc_iso_to_local_dt(raw_data.get('wake_time', ''))
    if not end_local:
        return sleep_local, sleep_local
    return sleep_local, end_local


def random_dt_in_sleep_window(sleep_start, sleep_end, margin_minutes=15.0):
    """在真实入睡区间 [sleep_start, sleep_end] 内生成随机时刻。"""
    span_min = (sleep_end - sleep_start).total_seconds() / 60.0
    if span_min <= 0:
        return sleep_start
    m = min(margin_minutes, max(0.0, span_min / 4.0))
    if span_min <= 2 * m + 1.0 / 60.0:
        return sleep_start + timedelta(minutes=span_min / 2.0)
    return sleep_start + timedelta(minutes=random.uniform(m, span_min - m))


def clamp_dt_to_sleep_window(dt, sleep_start, sleep_end):
    if dt < sleep_start:
        return sleep_start
    if dt > sleep_end:
        return sleep_end
    return dt


def clamp_dt_for_sleeping_event(dt, bed_time_local, sleep_time, sleep_end):
    """
    入睡困难事件锚点：与全体睡眠事件一致，约束在入睡时刻～起床时刻（睡眠过程）内。
    bed_time_local 保留为兼容参数，不再作为下界（避免早于入睡时刻）。
    """
    lo = sleep_time
    if lo and dt < lo:
        dt = lo
    if sleep_end and dt > sleep_end:
        dt = sleep_end
    return dt


def enforce_posture_switch_in_sleep_window(ev, sleep_start, sleep_end, session_record_date: str):
    """睡眠姿势切换仅允许落在 [sleep_start, sleep_end]（入睡～起床，睡眠过程内）。"""
    if (ev or {}).get("code") != "posture_switch" or not sleep_start or not sleep_end:
        return
    ad = session_anchor_event_local_dt(ev, sleep_start, sleep_end)
    if ad != datetime.min and sleep_start <= ad <= sleep_end:
        return
    new_t = clamp_dt_to_sleep_window(
        random_dt_in_sleep_window(sleep_start, sleep_end, margin_minutes=10.0),
        sleep_start,
        sleep_end,
    )
    ev["event_timestamp"] = format_sleep_event_local_timestamp(new_t)
    ev["record_date"] = session_record_date


_CONTINUOUS_NOISE_SLEEP_EVENT_CODES = frozenset(
    {
        "appliance_continuous",
        "environment_continuous",
        "neighbor_continuous",
        "nature_continuous",
    }
)
_DISPOSABLE_NOISE_SLEEP_EVENT_CODES = frozenset(
    {
        "sudden_impact",
        "sudden_traffic",
        "voice_doorbell",
        "nature_sudden",
        "object_sudden",
    }
)
_ALL_NOISE_SLEEP_EVENT_CODES = (
    _CONTINUOUS_NOISE_SLEEP_EVENT_CODES | _DISPOSABLE_NOISE_SLEEP_EVENT_CODES
)

LIKELY_SLEEP_EVENT_LABEL_TO_CODE = {
    "入睡困难": "sleeping",
    "家电持续声": "appliance_continuous",
    "环境持续声": "environment_continuous",
    "邻里持续声": "neighbor_continuous",
    "自然持续声": "nature_continuous",
    "突发撞击声": "sudden_impact",
    "突发交通声": "sudden_traffic",
    "人声/门铃声": "voice_doorbell",
    "自然突发声": "nature_sudden",
    "物品突发声": "object_sudden",
    "异常体动": "movement",
    "噩梦应激": "nightmare",
    "心率上升": "heart_rate_increase",
    "梦话": "sleep_talking",
    "睡眠姿势切换": "posture_switch",
    "肢体动作": "limb_movements",
    "打鼾": "snoring",
    "咳嗽": "cough_clearing",
    "吞咽": "swallow",
    "单次体动": "once_movement",
    "自然微动": "natural_movement",
    "呼吸声": "breathing",
    "安静": "silence",
}


def allowed_sleep_event_codes_from_profile(user_profile):
    """
    从 config 中 likelyNightSleepEvents（中文标签）解析为事件 code 集合。
    未配置或解析结果为空时返回 None，表示不按名单限制（兼容旧数据）。
    """
    if not user_profile:
        return None
    labels = user_profile.get("likelyNightSleepEvents")
    if not labels or not isinstance(labels, (list, tuple)):
        return None
    codes = set()
    for lb in labels:
        key = (lb or "").strip()
        c = LIKELY_SLEEP_EVENT_LABEL_TO_CODE.get(key)
        if c:
            codes.add(c)
    return frozenset(codes) if codes else None


def _parse_hhmm_to_minutes(hhmm):
    if not hhmm or not isinstance(hhmm, str):
        return None
    parts = hhmm.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None


def bedtime_position_in_sleep_window(sleep_local, sleep_time_cfg):
    """
    实际入睡时刻落在 config sleepTime [min, max] 内的相对位置 0~1（0 靠 min，1 靠 max）。
    支持跨零点就寝窗口。无法解析时返回 None。
    """
    if sleep_local is None or not sleep_time_cfg:
        return None
    mn = sleep_time_cfg.get("min")
    mx = sleep_time_cfg.get("max")
    if not mn or not mx:
        return None
    smin = mn[0] if isinstance(mn, list) else str(mn)
    smax = mx[0] if isinstance(mx, list) else str(mx)
    min_m = _parse_hhmm_to_minutes(smin)
    max_m = _parse_hhmm_to_minutes(smax)
    if min_m is None or max_m is None:
        return None
    sleep_m = sleep_local.hour * 60 + sleep_local.minute
    if min_m <= max_m:
        span = max_m - min_m
        if span <= 0:
            return 0.5
        if sleep_m < min_m:
            return 0.0
        if sleep_m > max_m:
            return 1.0
        return (sleep_m - min_m) / span
    span = (1440 - min_m) + max_m
    if span <= 0:
        return 0.5
    if sleep_m >= min_m:
        off = sleep_m - min_m
    elif sleep_m <= max_m:
        off = (1440 - min_m) + sleep_m
    else:
        return None
    return off / span


def bedtime_supports_sleep_difficulty(sleep_local, sleep_time_cfg, personality_type):
    """
    入睡时间点是否易与「入睡困难」并存：夜型(E)在配置窗口偏左段、晨型(M)偏右段；
    与 new.md 中「夜型仍早躺 / 晨型拖晚」一致。无窗口或算不出位置时不拦截（返回 True）。
    """
    pos = bedtime_position_in_sleep_window(sleep_local, sleep_time_cfg)
    if pos is None:
        return True
    parts = (personality_type or "M-L-C").split("-")
    chronotype = (parts[0] or "M").upper()
    if chronotype == "E":
        return pos <= 0.42
    if chronotype == "M":
        return pos >= 0.58
    return pos <= 0.35 or pos >= 0.65


def _profile_allows_sleep_event_code(allowed_codes, code):
    if allowed_codes is None:
        return True
    return code in allowed_codes


def _profile_allows_sleeping_difficulty(allowed_codes):
    return _profile_allows_sleep_event_code(allowed_codes, "sleeping")


def _profile_allows_noise_spike_env(allowed_codes):
    if allowed_codes is None:
        return True
    return bool(allowed_codes & _ALL_NOISE_SLEEP_EVENT_CODES)


def _profile_allows_hr_spike_vitals(allowed_codes):
    return _profile_allows_sleep_event_code(allowed_codes, "heart_rate_increase")


_ABNORMAL_SLEEP_EVENT_CODES = frozenset(
    {
        *_ALL_NOISE_SLEEP_EVENT_CODES,
        "sleeping",
        "nightmare",
        "movement",
        "heart_rate_increase",
    }
)


def _noise_intervention_scheme_alias(event_code):
    """噪音子类型映射到原干预模板键（持续性 / 一次性）。"""
    if event_code in _CONTINUOUS_NOISE_SLEEP_EVENT_CODES:
        return "continuous_noise"
    if event_code in _DISPOSABLE_NOISE_SLEEP_EVENT_CODES:
        return "disposable_noise"
    return event_code


def weight_sleep_event_code(code, dims, profile):
    """按 new.md 睡眠结构与人格三维（敏感/活跃/夜间觉醒）为事件码加权。"""
    sens, act = dims["sensitivity"], dims["brain_activity"]
    na = profile.get("night_awakenings", {"min": 0, "max": 1})
    na_mid = (na["min"] + na["max"]) / 2.0

    if code in _ABNORMAL_SLEEP_EVENT_CODES:
        w = 1.0
        if code in _CONTINUOUS_NOISE_SLEEP_EVENT_CODES:
            w *= (3.0 if sens == "H" else 0.45) * 0.5 / len(_CONTINUOUS_NOISE_SLEEP_EVENT_CODES)
        elif code in _DISPOSABLE_NOISE_SLEEP_EVENT_CODES:
            w *= (3.0 if sens == "H" else 0.45) * 0.5 / len(_DISPOSABLE_NOISE_SLEEP_EVENT_CODES)
        elif code == "movement":
            w *= 2.5 if sens == "H" else 1.0
            w *= 1.0 + na_mid * 0.22
        elif code == "nightmare":
            w *= 2.0 if sens == "H" else 1.0
            w *= 1.45 if act == "R" else 0.9
            w *= 0.3 if sens == "L" and act == "C" else 1.0
        elif code == "sleeping":
            w *= 3.5 if act == "R" else 0.4
            w *= 0.22 if act == "C" else 1.0
        elif code == "heart_rate_increase":
            w *= 2.2 if act == "R" else 0.95
            w *= 1.35 if sens == "H" else 1.0
        return max(w, 0.08)

    w = 1.0
    if code in ("posture_switch", "once_movement", "limb_movements"):
        t = profile.get("turnover_count", {"min": 20, "max": 35})
        tm = (t["min"] + t["max"]) / 2.0
        w *= 0.55 + tm / 55.0
    if code in ("natural_movement", "swallow"):
        w *= 1.4 if sens == "L" and act == "C" else 1.0
    elif code == "sleep_talking":
        w *= 1.5 if act == "R" else 1.05
    elif code == "snoring":
        w *= 0.9 if sens == "L" and act == "C" else 1.05
    elif code == "breathing":
        w *= 1.15 if sens == "H" else 1.0
    elif code == "silence":
        w *= 0.85
    return max(w, 0.08)


def weighted_pick_sleep_event_code(codes, dims, profile):
    if not codes:
        return None
    weights = [weight_sleep_event_code(c, dims, profile) for c in codes]
    return random.choices(codes, weights=weights, k=1)[0]


def personality_biased_event_time(event_code, sleep_start, sleep_end, dims, profile):
    """
    结合睡眠周期（前 1/3 浅睡与入睡适应、中段深睡、后段 REM 增多）与人格，抽样事件时刻。
    """
    span_min = max(0.0, (sleep_end - sleep_start).total_seconds() / 60.0)
    if span_min <= 0:
        return sleep_start

    def clamp_subwindow(lo, hi):
        lo = max(lo, sleep_start)
        hi = min(hi, sleep_end)
        if hi <= lo:
            return sleep_start, sleep_end
        return lo, hi

    latency = profile.get("sleep_latency", {"min": 15, "max": 20})
    lat_max = float(latency["max"])
    sens, act = dims["sensitivity"], dims["brain_activity"]

    early_end = sleep_start + timedelta(
        minutes=min(span_min * 0.34, max(32.0, lat_max + 18.0))
    )
    late_start = sleep_start + timedelta(
        minutes=max(span_min * 0.48, min(span_min * 0.70, span_min - 40.0))
    )

    if event_code == "nightmare":
        lo, hi = clamp_subwindow(late_start, sleep_end)
        return random_dt_in_sleep_window(lo, hi, margin_minutes=5.0)
    if event_code == "sleep_talking":
        lo, hi = clamp_subwindow(late_start, sleep_end)
        return random_dt_in_sleep_window(lo, hi, margin_minutes=3.0)
    if event_code == "snoring":
        lo = sleep_start + timedelta(minutes=span_min * 0.14)
        hi = sleep_start + timedelta(minutes=span_min * 0.58)
        lo, hi = clamp_subwindow(lo, hi)
        return random_dt_in_sleep_window(lo, hi, margin_minutes=8.0)
    if event_code == "cough_clearing":
        if sens == "H":
            lo = sleep_start + timedelta(minutes=span_min * 0.22)
            lo, hi = clamp_subwindow(lo, sleep_end)
            return random_dt_in_sleep_window(lo, hi, margin_minutes=5.0)
        return random_dt_in_sleep_window(sleep_start, sleep_end, margin_minutes=10.0)
    if event_code == "sleeping":
        lo, hi = clamp_subwindow(sleep_start + timedelta(minutes=2), early_end)
        return random_dt_in_sleep_window(lo, hi, margin_minutes=1.5)
    if event_code == "heart_rate_increase" and act == "R":
        lo, hi = clamp_subwindow(sleep_start + timedelta(minutes=2), early_end)
        return random_dt_in_sleep_window(lo, hi, margin_minutes=2.0)
    if event_code in _ALL_NOISE_SLEEP_EVENT_CODES and sens == "H":
        lo = sleep_start + timedelta(minutes=span_min * 0.10)
        hi = sleep_start + timedelta(minutes=span_min * 0.90)
        lo, hi = clamp_subwindow(lo, hi)
        return random_dt_in_sleep_window(lo, hi, margin_minutes=8.0)
    if event_code == "movement" and sens == "H":
        return random_dt_in_sleep_window(sleep_start, sleep_end, margin_minutes=12.0)
    if event_code in ("natural_movement", "swallow", "breathing", "silence"):
        lo = sleep_start + timedelta(minutes=span_min * 0.10)
        hi = sleep_start + timedelta(minutes=span_min * 0.80)
        lo, hi = clamp_subwindow(lo, hi)
        return random_dt_in_sleep_window(lo, hi, margin_minutes=6.0)
    if event_code == "posture_switch":
        dt = random_dt_in_sleep_window(sleep_start, sleep_end, margin_minutes=10.0)
        return clamp_dt_to_sleep_window(dt, sleep_start, sleep_end)
    if event_code in ("once_movement", "limb_movements"):
        return random_dt_in_sleep_window(sleep_start, sleep_end, margin_minutes=10.0)

    return random_dt_in_sleep_window(sleep_start, sleep_end, margin_minutes=12.0)


_SLEEP_EVENT_STAGE_PREFERENCES = {
    # abnormal
    "appliance_continuous": ("light", "deep", "rem"),
    "environment_continuous": ("light", "deep", "rem"),
    "neighbor_continuous": ("light", "deep", "rem"),
    "nature_continuous": ("light", "deep", "rem"),
    "sudden_impact": ("light", "rem", "deep"),
    "sudden_traffic": ("light", "rem", "deep"),
    "voice_doorbell": ("light", "rem", "deep"),
    "nature_sudden": ("light", "rem", "deep"),
    "object_sudden": ("light", "rem", "deep"),
    "sleeping": ("awake",),
    "nightmare": ("rem",),
    "movement": ("light", "awake"),
    "heart_rate_increase": ("rem",),
    # normal
    "snoring": ("light", "deep", "rem"),
    "sleep_talking": ("light", "rem"),
    "once_movement": ("light", "awake"),
    "natural_movement": ("light", "deep", "rem"),
    "posture_switch": ("light", "awake"),
    "cough_clearing": ("light", "awake"),
    "swallow": ("light", "awake"),
    "limb_movements": ("light", "deep"),
    "breathing": ("light", "deep", "rem"),
    "silence": ("light", "deep"),
}


def _intersect_dt_interval(lo, hi, st, ed):
    """[lo,hi] 与 [st,ed] 的交集（闭区间语义与睡眠窗一致）；空则返回 None。"""
    if not lo or not hi or not st or not ed:
        return None
    a = max(lo, st)
    b = min(hi, ed)
    if b <= a:
        return None
    return (a, b)


def _weighted_dt_from_clock_windows(sleep_start, sleep_end, windows):
    """
    windows: [(interval_start, interval_end, weight), ...]
    与 [sleep_start, sleep_end] 求交后按 weight*重叠秒数加权，再在选中子区间内均匀取点。
    """
    slices = []
    for st, ed, w in windows:
        hit = _intersect_dt_interval(sleep_start, sleep_end, st, ed)
        if not hit:
            continue
        eff = max(1.0, (hit[1] - hit[0]).total_seconds()) * float(w)
        slices.append((hit[0], hit[1], eff))
    if not slices:
        return None
    total = sum(s[2] for s in slices)
    if total <= 0:
        return None
    r = random.uniform(0, total)
    acc = 0.0
    chosen = slices[-1]
    for s in slices:
        acc += s[2]
        if r <= acc:
            chosen = s
            break
    st, ed, _ = chosen
    span = max(0.0, (ed - st).total_seconds())
    if span <= 1.0:
        return st
    return st + timedelta(seconds=random.uniform(0.0, span))


def _policy_preferred_anchor_dt(event_code, sleep_start, sleep_end, idf_data):
    """
    无环境锚点时的「墙钟 + 睡眠窗」偏好时刻；与 idf 分期投影在 pick_event_time_by_stage 中结合。
    若策略不适用（与睡眠窗无交）则返回 None，回退人格/分期逻辑。
    """
    if not sleep_start or not sleep_end or sleep_end <= sleep_start:
        return None
    d0 = sleep_start.date()
    d_wake = sleep_end.date()
    if event_code == "sudden_traffic":
        wins = [
            (datetime.combine(d0, dt_time(21, 0)), datetime.combine(d0, dt_time(23, 0)), 65.0),
            (
                datetime.combine(d0, dt_time(23, 0)),
                datetime.combine(d0 + timedelta(days=1), dt_time(7, 0)),
                35.0,
            ),
            (
                datetime.combine(d_wake, dt_time(7, 0)),
                datetime.combine(d_wake, dt_time(9, 30)),
                70.0,
            ),
        ]
        return _weighted_dt_from_clock_windows(sleep_start, sleep_end, wins)
    if event_code == "appliance_continuous":
        wins = [
            (datetime.combine(d0, dt_time(21, 0)), datetime.combine(d0, dt_time(22, 30)), 65.0),
        ]
        return _weighted_dt_from_clock_windows(sleep_start, sleep_end, wins)
    if event_code == "neighbor_continuous":
        wins = [
            (datetime.combine(d0, dt_time(21, 0)), datetime.combine(d0, dt_time(22, 0)), 50.0),
            (
                datetime.combine(d_wake, dt_time(8, 0)),
                datetime.combine(d_wake, dt_time(9, 30)),
                65.0,
            ),
        ]
        return _weighted_dt_from_clock_windows(sleep_start, sleep_end, wins)
    if event_code == "nature_continuous":
        wins = [
            (datetime.combine(d0, dt_time(21, 0)), datetime.combine(d0, dt_time(23, 0)), 45.0),
        ]
        return _weighted_dt_from_clock_windows(sleep_start, sleep_end, wins)
    if event_code == "voice_doorbell":
        wins = [
            (
                datetime.combine(d_wake, dt_time(8, 0)),
                datetime.combine(d_wake, dt_time(9, 30)),
                35.0,
            ),
            (datetime.combine(d0, dt_time(21, 0)), datetime.combine(d0, dt_time(22, 0)), 15.0),
        ]
        return _weighted_dt_from_clock_windows(sleep_start, sleep_end, wins)
    if event_code in ("sudden_impact", "nature_sudden"):
        return random_dt_in_sleep_window(sleep_start, sleep_end, margin_minutes=8.0)
    return None


def _nightmare_policy_one_to_four_slices(sleep_start, sleep_end):
    """每日 01:00～04:00 与睡眠窗的交集（可多日）。"""
    out = []
    cur = sleep_start.date()
    end_d = sleep_end.date()
    while cur <= end_d:
        a = datetime.combine(cur, dt_time(1, 0))
        b = datetime.combine(cur, dt_time(4, 0))
        hit = _intersect_dt_interval(sleep_start, sleep_end, a, b)
        if hit:
            out.append(hit)
        cur += timedelta(days=1)
    return out


def _pick_nightmare_anchor_policy(sleep_start, sleep_end, idf_data):
    """
    噩梦：锚在 01:00～04:00 与睡眠窗交集中；
    分期加权：深睡 0.2、REM 0.65、浅睡 0.15（与重叠时长相乘后抽样）。
    """
    night = _nightmare_policy_one_to_four_slices(sleep_start, sleep_end)
    if not night:
        return None
    weights_map = {"deep": 0.2, "rem": 0.65, "light": 0.15}
    pieces = []
    for ns, ne in night:
        for seg in idf_data or []:
            stg = (seg or {}).get("stage")
            if stg not in weights_map:
                continue
            wseg = _idf_seg_best_aligned_window(seg, sleep_start, sleep_end)
            if not wseg:
                continue
            wst, wed = wseg
            hit = _intersect_dt_interval(ns, ne, wst, wed)
            if not hit:
                continue
            wt = weights_map[stg] * max(1.0, (hit[1] - hit[0]).total_seconds())
            pieces.append((hit[0], hit[1], wt))
    if not pieces:
        return None
    total = sum(p[2] for p in pieces)
    if total <= 0:
        return None
    r = random.uniform(0, total)
    acc = 0.0
    chosen = pieces[-1]
    for p in pieces:
        acc += p[2]
        if r <= acc:
            chosen = p
            break
    st, ed, _ = chosen
    span = max(0.0, (ed - st).total_seconds())
    if span <= 2.0:
        return st
    lo_m = min(max(30.0, span * 0.08), span * 0.45)
    hi_m = max(lo_m + 1.0, span * 0.92)
    return st + timedelta(seconds=random.uniform(lo_m, hi_m))


def _first_light_end_for_snoring_policy(idf_data, sleep_start, sleep_end):
    """打鼾不得早于「首个浅睡段结束」；无浅睡则退化为首个非清醒睡眠段末。"""
    if not isinstance(idf_data, list) or not idf_data:
        return sleep_start + timedelta(minutes=3)
    for seg in idf_data:
        if (seg or {}).get("stage") != "light":
            continue
        w = _idf_seg_best_aligned_window(seg, sleep_start, sleep_end)
        if not w:
            continue
        return min(w[1], sleep_end)
    for seg in idf_data:
        if (seg or {}).get("stage") in ("light", "deep", "rem"):
            w = _idf_seg_best_aligned_window(seg, sleep_start, sleep_end)
            if w:
                return min(w[1], sleep_end)
    return sleep_start + timedelta(minutes=5)


def _weighted_snoring_anchor_dt(
    idf_data, sleep_start, sleep_end, dims, profile, fixed_local_dt=None
):
    """浅睡/深睡权重大、REM 权重低；且不早于首个浅睡段结束。"""
    flo = _first_light_end_for_snoring_policy(idf_data, sleep_start, sleep_end)
    sw = _build_stage_windows_from_idf(idf_data or [], sleep_start, sleep_end)
    wins_clip = []
    stage_w = {"light": 1.0, "deep": 1.0, "rem": 0.35}
    triples = []
    for stg, wt in stage_w.items():
        for st, ed in sw.get(stg, []):
            st2 = max(st, flo)
            if ed <= st2:
                continue
            wins_clip.append((st2, ed))
            dur = max(1.0, (ed - st2).total_seconds())
            triples.append((st2, ed, dur * wt))
    if not triples:
        return None
    if fixed_local_dt is not None and wins_clip:
        t0 = _project_dt_to_windows(fixed_local_dt, wins_clip)
        return clamp_dt_to_sleep_window(t0, sleep_start, sleep_end)
    total = sum(t[2] for t in triples)
    r = random.uniform(0, total)
    acc = 0.0
    st, ed = triples[-1][0], triples[-1][1]
    for t in triples:
        acc += t[2]
        if r <= acc:
            st, ed = t[0], t[1]
            break
    span = max(0.0, (ed - st).total_seconds())
    if span <= 2.0:
        t0 = st
    else:
        t0 = st + timedelta(seconds=random.uniform(1.0, max(2.0, span - 1.0)))
    return clamp_dt_to_sleep_window(t0, sleep_start, sleep_end)


def _env_noise_code_clock_weight(code, local_dt, sleep_start, sleep_end):
    """环境触发行时刻下各噪声类事件的相对权重（用于 pool 内抽样）。"""
    if local_dt is None:
        return 1.0
    d0 = sleep_start.date()
    d_wake = sleep_end.date()

    def _wins(cw_list):
        s = 0.0
        for a, b, w in cw_list:
            if a <= local_dt <= b:
                s = max(s, float(w))
        return s

    if code == "sudden_traffic":
        cw = [
            (datetime.combine(d0, dt_time(21, 0)), datetime.combine(d0, dt_time(23, 0)), 65.0),
            (
                datetime.combine(d0, dt_time(23, 0)),
                datetime.combine(d0 + timedelta(days=1), dt_time(7, 0)),
                35.0,
            ),
            (
                datetime.combine(d_wake, dt_time(7, 0)),
                datetime.combine(d_wake, dt_time(9, 30)),
                70.0,
            ),
        ]
        v = _wins(cw)
        return v if v > 0 else 0.04
    if code == "appliance_continuous":
        cw = [
            (datetime.combine(d0, dt_time(21, 0)), datetime.combine(d0, dt_time(22, 30)), 65.0),
        ]
        v = _wins(cw)
        return v if v > 0 else 0.04
    if code == "neighbor_continuous":
        cw = [
            (datetime.combine(d0, dt_time(21, 0)), datetime.combine(d0, dt_time(22, 0)), 50.0),
            (
                datetime.combine(d_wake, dt_time(8, 0)),
                datetime.combine(d_wake, dt_time(9, 30)),
                65.0,
            ),
        ]
        v = _wins(cw)
        return v if v > 0 else 0.04
    if code == "nature_continuous":
        cw = [
            (datetime.combine(d0, dt_time(21, 0)), datetime.combine(d0, dt_time(23, 0)), 45.0),
        ]
        v = _wins(cw)
        return v if v > 0 else 0.04
    if code == "voice_doorbell":
        cw = [
            (
                datetime.combine(d_wake, dt_time(8, 0)),
                datetime.combine(d_wake, dt_time(9, 30)),
                35.0,
            ),
            (datetime.combine(d0, dt_time(21, 0)), datetime.combine(d0, dt_time(22, 0)), 15.0),
        ]
        v = _wins(cw)
        return v if v > 0 else 0.04
    if code == "sudden_impact":
        return 0.2
    if code == "nature_sudden":
        return 0.15
    return 1.0


def _pick_env_noise_sleep_event_code(local_dt, pool, sleep_start, sleep_end):
    if not pool:
        return None
    weights = [
        max(0.02, _env_noise_code_clock_weight(c, local_dt, sleep_start, sleep_end)) for c in pool
    ]
    return random.choices(pool, weights=weights, k=1)[0]


def _sleep_clock_to_datetime_on_date(clock_str, base_date):
    """将 HH:MM(:SS) 转为 base_date 当天 datetime。"""
    if not isinstance(clock_str, str):
        return None
    s = clock_str.strip()
    if not _SLEEP_CLOCK_HHMM_RE.match(s):
        return None
    try:
        if len(s) <= 5:
            tm = datetime.strptime(s, "%H:%M").time()
        else:
            tm = datetime.strptime(s, "%H:%M:%S").time()
    except ValueError:
        return None
    return datetime.combine(base_date, tm)


def _idf_seg_best_aligned_window(seg, sleep_start, sleep_end):
    """
    将 idf_data 单段(start/end)对齐到本次睡眠窗口附近，返回与窗口重叠最优的区间。
    """
    if not seg or not sleep_start or not sleep_end:
        return None
    sd = sleep_start.date()
    best = None
    best_score = (-1, float("-inf"))  # (overlap_sec, -distance_to_start_sec)
    for shift in (-1, 0, 1):
        base_d = sd + timedelta(days=shift)
        st = _sleep_clock_to_datetime_on_date(seg.get("start"), base_d)
        ed = _sleep_clock_to_datetime_on_date(seg.get("end"), base_d)
        if st is None or ed is None:
            continue
        if ed <= st:
            ed += timedelta(days=1)
        overlap_start = max(st, sleep_start)
        overlap_end = min(ed, sleep_end)
        overlap_sec = max(0, int((overlap_end - overlap_start).total_seconds()))
        dist_sec = abs((st - sleep_start).total_seconds())
        score = (overlap_sec, -dist_sec)
        if score > best_score:
            best_score = score
            best = (st, ed)
    return best


def _build_stage_windows_from_idf(idf_data, sleep_start, sleep_end):
    """把 idf_data 转为 stage -> [(start_dt, end_dt)]，并裁剪到当前睡眠窗口。"""
    out = {}
    if not isinstance(idf_data, list) or not sleep_start or not sleep_end:
        return out
    for seg in idf_data:
        stage = (seg or {}).get("stage")
        if stage not in {"awake", "light", "deep", "rem"}:
            continue
        window = _idf_seg_best_aligned_window(seg, sleep_start, sleep_end)
        if not window:
            continue
        st, ed = window
        st = max(st, sleep_start)
        ed = min(ed, sleep_end)
        if ed <= st:
            continue
        out.setdefault(stage, []).append((st, ed))
    return out


def _sleep_stage_at_local_dt(local_dt, stage_windows):
    """给定本地时刻，返回 idf 分期（awake/light/deep/rem）；无匹配则 None。"""
    if not local_dt or not stage_windows:
        return None
    for stage, wins in stage_windows.items():
        for st, ed in wins:
            if st <= local_dt < ed:
                return stage
    return None


def _sleep_onset_local_naive(raw_data, bed_window_start_local):
    """
    本地入睡时刻：优先 sleep_time（UTC→本地），否则 bed_time + sleep_latency。
    与 docs/睡眠阶段对体征影响的指导性文档.md 中「入睡过渡」时间轴对齐。
    """
    raw_data = raw_data or {}
    st = _parse_utc_iso_to_local_dt(raw_data.get("sleep_time", ""))
    if st:
        return st
    lat = int(raw_data.get("sleep_latency") or 0)
    if bed_window_start_local is not None:
        return bed_window_start_local + timedelta(minutes=max(0, lat))
    return None


def _circadian_progress_t_rel(
    sample_local, sleep_onset_local, wake_up_local, bed_fallback_local=None
):
    """
    入睡→起床归一进度 t∈[0,1]；相对「入睡时刻」之前为负秒→0。
    若无 sleep_time，则用 bed_fallback_local（本窗 bed_time）作为节律起点，避免误用当前采样点作 onset。
    """
    if sample_local is None or wake_up_local is None:
        return 0.5
    onset = sleep_onset_local or bed_fallback_local
    if onset is None:
        onset = sample_local
    if wake_up_local <= onset:
        return 0.5
    sec = (sample_local - onset).total_seconds()
    if sec <= 0:
        return 0.0
    tot = (wake_up_local - onset).total_seconds()
    return max(0.0, min(1.0, sec / tot))


def _circadian_vitals_offsets(t_rel):
    """
    夜间节律的温和加性调制（与 idf 分期叠加）。
    参照 docs/睡眠阶段对体征影响的指导性文档.md：夜间心率谷值、清晨回升；HRV 中段偏高；RR 夜间略慢；末段体动略增。
    """
    t = max(0.0, min(1.0, float(t_rel)))
    if t < 0.5:
        hr = -2.0 * (t / 0.5)
    else:
        hr = -2.0 + 3.5 * ((t - 0.5) / 0.5)
    peak_t = 0.42
    hrv = 0.055 * max(0.0, 1.0 - min(abs(t - peak_t) / 0.40, 1.0))
    rr = -0.30 * max(0.0, 1.0 - min(abs(t - 0.45) / 0.42, 1.0))
    if t <= 0.58:
        bp = -2.5 * (t / 0.58)
    else:
        u = (t - 0.58) / 0.42
        bp = -2.5 + 3.0 * (u ** 1.1)
    dia_bp = bp * 0.6
    mot = 1.5 + 8.0 * (t ** 2.6)
    return {"hr": hr, "hrv": hrv, "rr": rr, "sys": bp, "dia": dia_bp, "motion": mot}


def _presleep_transition_offsets(sample_local, bed_local, sleep_onset_local):
    """
    卧床→入睡：HR 逐步下降、HRV 上升、RR 略降、体动减少（文档「入睡过渡」趋势）。
    """
    if sample_local is None or bed_local is None or sleep_onset_local is None:
        return {"hr": 0.0, "hrv": 0.0, "rr": 0.0, "motion": 0.0}
    if sample_local >= sleep_onset_local:
        return {"hr": 0.0, "hrv": 0.0, "rr": 0.0, "motion": 0.0}
    span_sec = (sleep_onset_local - bed_local).total_seconds()
    if span_sec <= 60:
        p = 1.0
    else:
        p = max(0.0, min(1.0, (sample_local - bed_local).total_seconds() / span_sec))
    return {
        "hr": -5.0 * p,
        "hrv": 0.04 * p,
        "rr": -0.35 * p,
        "motion": -6.0 * p,
    }


def _apple_health_extended_metrics_sleep(stage):
    """已废弃，保留函数签名以防旧调用，返回空字典。"""
    return {}


def _middle_awake_windows_from_idf(idf_data, sleep_start, sleep_end):
    """
    获取 idf_data 中“半夜觉醒阶段”的窗口：
    仅取索引范围 [1, len-2] 且 stage=awake 的分段。
    """
    out = []
    if not isinstance(idf_data, list) or len(idf_data) < 3:
        return out
    if not sleep_start or not sleep_end:
        return out
    n = len(idf_data)
    for idx, seg in enumerate(idf_data):
        if idx < 1 or idx > n - 2:
            continue
        if (seg or {}).get("stage") != "awake":
            continue
        window = _idf_seg_best_aligned_window(seg, sleep_start, sleep_end)
        if not window:
            continue
        st, ed = window
        st = max(st, sleep_start)
        ed = min(ed, sleep_end)
        if ed > st:
            out.append((st, ed))
    return out


def _idf_last_segment_end_local_datetime(idf_data, sleep_start, sleep_end):
    """idf 最后一段 end 对齐睡眠窗后的本地 datetime（与 idf['end'] 墙钟一致，经 _idf_seg_best_aligned_window 定日）。"""
    if not isinstance(idf_data, list) or not idf_data or not sleep_start or not sleep_end:
        return None
    w = _idf_seg_best_aligned_window(idf_data[-1], sleep_start, sleep_end)
    if not w:
        return None
    _, ed = w
    return ed


def _idf_last_segment_end_utc_iso_z(idf_data, sleep_start, sleep_end):
    """idf 最后一段 end 对应的 UTC ISO（...Z），与睡眠数据该段 end 一一对应。"""
    te = _idf_last_segment_end_local_datetime(idf_data, sleep_start, sleep_end)
    if te is None:
        return None
    return local_naive_dt_to_utc_iso_z(te)


def _align_collected_local_dts_last_to_idf_end(collected_local_dts, idf_data, sleep_start, sleep_end):
    """将本地采样时刻列表的最后一条对齐到 idf 最后一段 end；倒数第二条与末条间隔至少 MIN_VITAL_ENV_COLLECTED_AT_GAP_MINUTES。"""
    if not collected_local_dts or not idf_data:
        return collected_local_dts
    te = _idf_last_segment_end_local_datetime(idf_data, sleep_start, sleep_end)
    if te is None:
        return collected_local_dts
    min_sep = timedelta(minutes=MIN_VITAL_ENV_COLLECTED_AT_GAP_MINUTES)
    out = sorted(collected_local_dts, key=lambda x: x)
    n = len(out)
    out[-1] = te
    if n < 2:
        return out
    hi_bound = te - min_sep
    lo_bound = out[-3] + min_sep if n >= 3 else out[0]
    if hi_bound < lo_bound:
        out[-2] = max(out[0], hi_bound)
        if out[-2] < lo_bound:
            out[-2] = lo_bound
        if out[-2] >= te:
            out[-2] = max(out[0], te - min_sep)
        return out
    out[-2] = min(max(out[-2], lo_bound), hi_bound)
    return out


def _sleep_stage_at_event_anchor(local_dt, idf_data, sleep_start, sleep_end):
    """
    事件锚点所属分期：与 idf 顺序一致；非末段用半开 [st, ed)；末段用 [st, ed] 以覆盖 end 与 idf 对齐的采样点。
    """
    if not local_dt or not isinstance(idf_data, list) or not idf_data:
        return None
    n = len(idf_data)
    for i, seg in enumerate(idf_data):
        w = _idf_seg_best_aligned_window(seg, sleep_start, sleep_end)
        if not w:
            continue
        st, ed = w
        st = max(st, sleep_start)
        ed = min(ed, sleep_end)
        if ed <= st:
            continue
        stage = (seg or {}).get("stage")
        if stage not in {"awake", "light", "deep", "rem"}:
            continue
        if i == n - 1:
            if st <= local_dt <= ed:
                return stage
        else:
            if st <= local_dt < ed:
                return stage
    return None


def eligible_sleeping_windows(
    idf_data,
    sleep_start,
    sleep_end,
    sleep_latency_minutes=None,
    bed_time_local=None,
    latency_exclusive_min: int = 20,
):
    """
    入睡困难只允许落在 idf_data[0]（第一个阶段）内；且该段须为 awake，否则不生成入睡困难。
    另须 raw 入睡潜伏期 sleep_latency_minutes > latency_exclusive_min（默认 20），否则返回空。
    窗口为 idf 首段对齐睡眠窗后的区间 ∩ [sleep_start, sleep_end]（入睡后至起床前）；
    若首段清醒完全落在入睡之前则返回空（不再用上床时间扩展下界）。
    """
    lat = int(sleep_latency_minutes or 0)
    thr = int(latency_exclusive_min)
    if lat <= thr:
        return []
    if not isinstance(idf_data, list) or not idf_data or not sleep_start or not sleep_end:
        return []
    if (idf_data[0] or {}).get("stage") != "awake":
        return []
    w0 = _idf_seg_best_aligned_window(idf_data[0], sleep_start, sleep_end)
    if not w0:
        return []
    st, ed = w0
    st = max(st, sleep_start)
    ed = min(ed, sleep_end)
    if ed <= st:
        return []
    return [(st, ed)]


def _pick_random_dt_in_stage_windows(windows, fallback_dt):
    """按各窗口时长加权随机选点。"""
    if not windows:
        return fallback_dt
    durs = [max(1.0, (ed - st).total_seconds()) for st, ed in windows]
    st, ed = random.choices(windows, weights=durs, k=1)[0]
    span_sec = max(0.0, (ed - st).total_seconds())
    if span_sec <= 1.0:
        return st
    margin_sec = min(300.0, span_sec * 0.2)
    if span_sec <= 2 * margin_sec:
        return st + timedelta(seconds=span_sec / 2.0)
    return st + timedelta(seconds=random.uniform(margin_sec, span_sec - margin_sec))


def _pick_heart_rate_anchor_in_rem_idf(idf_data, sleep_start, sleep_end, hint_dt=None):
    """心率上升锚点：严格落在 idf REM 窗口与睡眠窗交集内；无 REM 则返回 None。"""
    if not sleep_start or not sleep_end:
        return None
    sw = _build_stage_windows_from_idf(idf_data or [], sleep_start, sleep_end)
    rem_wins = sw.get("rem") or []
    if not rem_wins:
        return None
    fb = hint_dt if hint_dt is not None else sleep_start
    fb = clamp_dt_to_sleep_window(fb, sleep_start, sleep_end)
    return clamp_dt_to_sleep_window(
        _pick_random_dt_in_stage_windows(rem_wins, fb),
        sleep_start,
        sleep_end,
    )


def _rem_slices_before_nightmare_dt(
    nightmare_dt, sleep_start, sleep_end, idf_data, min_gap_min, max_gap_min
):
    """
    噩梦锚点之前 [min_gap_min, max_gap_min] 分钟内、且落在 REM 与睡眠窗内的子区间并集，
    供「噩梦前联动心率上升」采样（交感升高略早于噩梦应激锚点，且心率时刻严格早于噩梦）。
    """
    if nightmare_dt is None or not sleep_start or not sleep_end:
        return []
    t_lo = clamp_dt_to_sleep_window(
        nightmare_dt - timedelta(minutes=float(max_gap_min)), sleep_start, sleep_end
    )
    t_hi = clamp_dt_to_sleep_window(
        nightmare_dt - timedelta(minutes=float(min_gap_min)), sleep_start, sleep_end
    )
    if t_hi <= t_lo:
        return []
    sw = _build_stage_windows_from_idf(idf_data or [], sleep_start, sleep_end)
    out = []
    for st, ed in sw.get("rem") or []:
        a = max(st, t_lo)
        b = min(ed, t_hi)
        if b > a:
            out.append((a, b))
    return out


def _project_dt_to_windows(dt, windows):
    """将 dt 投影到最近窗口内，保持事件与触发时刻尽量接近。"""
    if not windows:
        return dt
    best_dt = None
    best_dist = None
    for st, ed in windows:
        if st <= dt <= ed:
            return dt
        cand = st if dt < st else ed
        dist = abs((cand - dt).total_seconds())
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_dt = cand
    return best_dt if best_dt is not None else dt


def _union_light_deep_rem_windows(idf_data, sleep_start, sleep_end):
    """打鼾等「仅睡眠分期」事件用的非清醒窗口并集。"""
    sw = _build_stage_windows_from_idf(idf_data or [], sleep_start, sleep_end)
    wins = []
    for stg in ("light", "deep", "rem"):
        wins.extend(sw.get(stg, []))
    return wins


def _ensure_snoring_anchor_not_in_awake_stage(
    dt, idf_data, sleep_start, sleep_end, dims, profile
):
    """
    打鼾锚点必须落在 light/deep/rem（有 idf 时严格按分期；无 idf 时用睡眠窗内侧留白，避开典型入睡/起床清醒区）。
    """
    if dt is None or not sleep_start or not sleep_end:
        return dt
    dt = clamp_dt_to_sleep_window(dt, sleep_start, sleep_end)
    idf = idf_data if isinstance(idf_data, list) else []

    def _heuristic_sleep_core_bounds():
        span = max(0.0, (sleep_end - sleep_start).total_seconds())
        if span <= 0:
            return sleep_start, sleep_end
        margin_lo = min(max(120.0, span * 0.06), span * 0.28)
        margin_hi = min(max(420.0, span * 0.10), span * 0.32)
        lo = sleep_start + timedelta(seconds=margin_lo)
        hi = sleep_end - timedelta(seconds=margin_hi)
        if hi <= lo:
            lo = sleep_start + timedelta(minutes=5)
            hi = sleep_end - timedelta(minutes=15)
        if hi <= lo:
            return sleep_start, sleep_end
        return lo, hi

    if not idf:
        lo, hi = _heuristic_sleep_core_bounds()
        if dt < lo:
            return lo
        if dt > hi:
            return hi
        return dt

    wins = _union_light_deep_rem_windows(idf, sleep_start, sleep_end)
    if not wins:
        lo, hi = _heuristic_sleep_core_bounds()
        if dt < lo:
            return lo
        if dt > hi:
            return hi
        return dt

    stg = _sleep_stage_at_event_anchor(dt, idf, sleep_start, sleep_end)
    if stg in ("light", "deep", "rem"):
        return dt

    snapped = clamp_dt_to_sleep_window(
        _project_dt_to_windows(dt, wins), sleep_start, sleep_end
    )
    if _sleep_stage_at_event_anchor(snapped, idf, sleep_start, sleep_end) in (
        "light",
        "deep",
        "rem",
    ):
        return snapped

    fb = personality_biased_event_time("snoring", sleep_start, sleep_end, dims, profile)
    fb = clamp_dt_to_sleep_window(fb, sleep_start, sleep_end)
    picked = clamp_dt_to_sleep_window(
        _pick_random_dt_in_stage_windows(wins, fb), sleep_start, sleep_end
    )
    if _sleep_stage_at_event_anchor(picked, idf, sleep_start, sleep_end) not in (
        "light",
        "deep",
        "rem",
    ):
        picked = clamp_dt_to_sleep_window(
            _pick_random_dt_in_stage_windows(wins, picked), sleep_start, sleep_end
        )
    return picked


def _repair_primary_snoring_out_of_awake_idf(
    events, idf_data, sleep_start, sleep_end, dims, profile
):
    """assign_duration 等可能把打鼾锚点挤入清醒段；返回前再校正主打鼾事件（不含 AI 行）。"""
    idf = idf_data if isinstance(idf_data, list) else []
    for ev in events:
        if ev.get("code") != "snoring" or ev.get("event_type") == "AI主动干预":
            continue
        anchor = session_anchor_event_local_dt(ev, sleep_start, sleep_end)
        if anchor == datetime.min:
            continue
        stg = _sleep_stage_at_event_anchor(anchor, idf, sleep_start, sleep_end)
        if stg in ("light", "deep", "rem"):
            continue
        new_t = _ensure_snoring_anchor_not_in_awake_stage(
            anchor, idf, sleep_start, sleep_end, dims, profile
        )
        if new_t is not None:
            ev["event_timestamp"] = format_sleep_event_local_timestamp(new_t)


def _repair_primary_heart_rate_out_of_rem_idf(events, idf_data, sleep_start, sleep_end):
    """assign_duration 等可能把心率先 REM 锚点挤出 REM；返回前尽量拉回 REM 窗（无 REM 则保持原样）。"""
    idf = idf_data if isinstance(idf_data, list) else []
    pair_key_to_nightmare_dt = {}
    for ev in events:
        if ev.get("code") != "nightmare" or ev.get("event_type") == "AI主动干预":
            continue
        pk = ev.get("_nightmare_pair_key")
        if not pk:
            continue
        ndt = session_anchor_event_local_dt(ev, sleep_start, sleep_end)
        if ndt != datetime.min:
            pair_key_to_nightmare_dt[str(pk)] = ndt
    for ev in events:
        if ev.get("code") != "heart_rate_increase" or ev.get("event_type") == "AI主动干预":
            continue
        anchor = session_anchor_event_local_dt(ev, sleep_start, sleep_end)
        if anchor == datetime.min:
            continue
        stg = _sleep_stage_at_event_anchor(anchor, idf, sleep_start, sleep_end)
        if stg == "rem":
            continue
        pk = str(ev.get("_nightmare_pair_key") or "")
        nightmare_dt = pair_key_to_nightmare_dt.get(pk) if pk else None
        new_t = None
        if nightmare_dt is not None:
            gmin = float(os.getenv("SLEEP_EVENTS_NIGHTMARE_HR_MIN_GAP_MIN", "1.25"))
            gmax = float(os.getenv("SLEEP_EVENTS_NIGHTMARE_HR_MAX_GAP_MIN", "5.0"))
            slices = _rem_slices_before_nightmare_dt(
                nightmare_dt, sleep_start, sleep_end, idf, gmin, gmax
            )
            if not slices:
                slices = _rem_slices_before_nightmare_dt(
                    nightmare_dt,
                    sleep_start,
                    sleep_end,
                    idf,
                    gmin,
                    min(12.0, gmax + 7.0),
                )
            if slices:
                new_t = clamp_dt_to_sleep_window(
                    _pick_random_dt_in_stage_windows(slices, nightmare_dt),
                    sleep_start,
                    sleep_end,
                )
                if new_t is not None and new_t >= nightmare_dt:
                    new_t = None
        if new_t is None:
            new_t = _pick_heart_rate_anchor_in_rem_idf(
                idf, sleep_start, sleep_end, hint_dt=anchor
            )
        if new_t is not None:
            ev["event_timestamp"] = format_sleep_event_local_timestamp(new_t)


def stage_aware_sleep_event_time(
    event_code,
    sleep_start,
    sleep_end,
    dims,
    profile,
    idf_data=None,
    preferred_dt=None,
    sleep_latency_minutes=None,
    bed_time_local=None,
    latency_exclusive_min: int = 20,
):
    """
    基于 aaa.md 的阶段映射 + 当天 idf_data 阶段区间选点；
    若 idf_data 缺失或无可用区间，则回退到人格化时间分布。
    sleep_latency_minutes：仅入睡困难分支保留参数；落点规则见 eligible_sleeping_windows。
    """
    fallback_dt = personality_biased_event_time(
        event_code, sleep_start, sleep_end, dims, profile
    )
    fallback_dt = clamp_dt_to_sleep_window(fallback_dt, sleep_start, sleep_end)
    stage_order = _SLEEP_EVENT_STAGE_PREFERENCES.get(event_code)
    if not stage_order:
        return (
            clamp_dt_to_sleep_window(preferred_dt, sleep_start, sleep_end)
            if preferred_dt is not None
            else fallback_dt
        )

    stage_windows = _build_stage_windows_from_idf(idf_data or [], sleep_start, sleep_end)
    middle_awake_windows = _middle_awake_windows_from_idf(
        idf_data or [], sleep_start, sleep_end
    )
    # “半夜觉醒阶段”仅使用中段 awake；若没有则视为无 awake 候选（允许回退到其它可选阶段）
    stage_windows["awake"] = middle_awake_windows if middle_awake_windows else []
    candidate_windows = []
    for stg in stage_order:
        candidate_windows.extend(stage_windows.get(stg, []))

    # 入睡困难：仅 idf 首段且为 awake（见 eligible_sleeping_windows）
    if event_code == "sleeping":
        lat = int(sleep_latency_minutes or 0)
        sleeping_windows = eligible_sleeping_windows(
            idf_data or [],
            sleep_start,
            sleep_end,
            lat,
            bed_time_local=bed_time_local,
            latency_exclusive_min=latency_exclusive_min,
        )
        if sleeping_windows:
            candidate_windows = sleeping_windows

    if not candidate_windows:
        result = (
            clamp_dt_to_sleep_window(preferred_dt, sleep_start, sleep_end)
            if preferred_dt is not None
            else fallback_dt
        )
    elif preferred_dt is not None:
        result = clamp_dt_to_sleep_window(
            _project_dt_to_windows(preferred_dt, candidate_windows), sleep_start, sleep_end
        )
    else:
        result = clamp_dt_to_sleep_window(
            _pick_random_dt_in_stage_windows(candidate_windows, fallback_dt),
            sleep_start,
            sleep_end,
        )

    if event_code == "snoring":
        result = _ensure_snoring_anchor_not_in_awake_stage(
            result, idf_data, sleep_start, sleep_end, dims, profile
        )
    return result


def personality_extra_event_count(dims, profile):
    """夜间觉醒多、高敏感时略增加片段事件数，贴近 new.md 碎片化描述。"""
    na = profile.get("night_awakenings", {"min": 1, "max": 2})
    na_sample = random.randint(na["min"], na["max"])
    base = 1 + min(2, na_sample)
    if dims["sensitivity"] == "H":
        base += 1
    if profile.get("stage_pattern") == "poor_quality":
        base += 1
    # 略增随机夜内片段（含异常/正常），上限温和抬高，避免一夜过于拥挤
    return random.randint(base, min(base + 3, 8))


_SLEEP_EVENT_DURATION_SEC_RANGES = {
    # abnormal
    "appliance_continuous": (45, 180),
    "environment_continuous": (45, 180),
    "neighbor_continuous": (45, 180),
    "nature_continuous": (45, 180),
    "sudden_impact": (8, 45),
    "sudden_traffic": (8, 45),
    "voice_doorbell": (8, 45),
    "nature_sudden": (8, 45),
    "object_sudden": (8, 45),
    "sleeping": (120, 480),
    "nightmare": (30, 180),
    "movement": (15, 120),
    "heart_rate_increase": (20, 150),
    # normal
    "snoring": (7, 8),
    "sleep_talking": (5, 40),
    "once_movement": (3, 20),
    "natural_movement": (3, 25),
    "posture_switch": (6, 45),
    "cough_clearing": (3, 20),
    "swallow": (2, 10),
    "limb_movements": (6, 60),
    "breathing": (20, 120),
    "silence": (30, 240),
}


def _pick_sleep_event_duration_sec(event):
    code = (event or {}).get("code", "")
    lo, hi = _SLEEP_EVENT_DURATION_SEC_RANGES.get(code, (10, 60))
    # AI主动干预事件通常短于其对应原始事件
    if (event or {}).get("event_type") == "AI主动干预":
        lo = max(6, int(lo * 0.55))
        hi = max(lo + 4, int(hi * 0.8))
    return random.randint(lo, hi)


def _scale_durations_to_window(durations, window_sec):
    if not durations:
        return []
    w = max(1, int(window_sec))
    total = sum(durations)
    if total <= w:
        return list(durations)
    scale = w / float(total)
    out = [max(1, int(d * scale)) for d in durations]
    # 修正四舍五入后超额
    while sum(out) > w:
        idx = max(range(len(out)), key=lambda i: out[i])
        if out[idx] > 1:
            out[idx] -= 1
        else:
            break
    # 若仍超额（极端情况），从尾部压缩
    while sum(out) > w:
        for i in range(len(out) - 1, -1, -1):
            if out[i] > 1:
                out[i] -= 1
                if sum(out) <= w:
                    break
        else:
            break
    return out


def assign_duration_and_retime_sleep_events(events, sleep_start, sleep_end, retime=True):
    """
    为睡眠事件补充 duration_sec，并重排时间以满足：
    1) 后一个事件起点不落入前一个事件 [start, start+duration_sec) 区间（打鼾除外，可与前一事件时刻重叠）；
    2) 最后一个事件的结束时间不超过 sleep_end（起床时刻，睡眠过程结束）；
    3) 每条事件起点不早于 sleep_start（入睡时刻）。
    """
    if not events or not sleep_start or not sleep_end:
        return events

    ordered = sorted(events, key=lambda x: session_anchor_event_local_dt(x, sleep_start, sleep_end))
    window_sec = max(1, int((sleep_end - sleep_start).total_seconds()))
    durations = [_pick_sleep_event_duration_sec(e) for e in ordered]
    durations = _scale_durations_to_window(durations, window_sec)

    prefix_remaining = [0] * len(durations)
    running = 0
    for i in range(len(durations) - 1, -1, -1):
        running += durations[i]
        prefix_remaining[i] = running

    prev_end = None
    for i, ev in enumerate(ordered):
        desired = session_anchor_event_local_dt(ev, sleep_start, sleep_end)
        if desired == datetime.min:
            desired = sleep_start

        start = desired
        if not retime:
            ev["duration_sec"] = int(durations[i])
            prev_end = start + timedelta(seconds=ev["duration_sec"])
            continue
        if start < sleep_start:
            start = sleep_start
        is_snoring = ev.get("code") == "snoring"
        if prev_end and start < prev_end and not is_snoring:
            start = prev_end

        latest_start = sleep_end - timedelta(seconds=prefix_remaining[i])
        if start > latest_start:
            start = latest_start
        if start < sleep_start:
            start = sleep_start

        ev["duration_sec"] = int(durations[i])
        ev["event_timestamp"] = format_sleep_event_local_timestamp(start)
        ev_end = start + timedelta(seconds=ev["duration_sec"])
        if is_snoring:
            if prev_end is None:
                prev_end = ev_end
            else:
                prev_end = max(prev_end, ev_end)
        else:
            prev_end = ev_end

    return ordered


_SLEEP_INTERVENTION_SCHEME_PATH = os.path.join(
    PROJECT_ROOT,
    "docs",
    "异常事件干预方案.md",
)
_sleep_intervention_scheme_cache = None

_CONTINUOUS_NOISE_SOURCES = [
    "空调",
    "冰箱",
    "新风",
    "加湿器",
    "风扇持续嗡嗡声",
    "马路车流",
    "小区施工",
    "地铁 / 轨道持续轰鸣",
    "邻居持续说话",
    "看电视",
    "打牌",
    "走动",
    "持续大雨",
    "大风",
    "连续虫鸣",
]

_DISPOSABLE_NOISE_SOURCES_BY_CODE = {
    "sudden_impact": [
        "关门砰响",
        "楼上掉东西",
        "家具磕碰",
        "重物撞击",
        "物品倾倒",
    ],
    "sudden_traffic": [
        "汽车鸣笛",
        "急刹车",
        "摩托车突然轰鸣",
        "救护车警报",
        "货车轰鸣驶过",
    ],
    "voice_doorbell": [
        "敲门声",
        "门铃",
        "突然咳嗽或大喊",
        "宠物突然叫",
        "婴儿啼哭",
    ],
    "nature_sudden": [
        "炸雷",
        "突然风声骤起",
        "大雨骤降",
        "闪电雷鸣",
    ],
    "object_sudden": [
        "杯子掉落",
        "物品倾倒",
        "抽屉开合声",
        "书本落地",
    ],
}

_EVENT_CODE_TO_INTERVENTION_FOCUS = {**{
    c: "持续性噪音干预方案" for c in _CONTINUOUS_NOISE_SLEEP_EVENT_CODES
}, **{
    c: "一次性噪音干预方案" for c in _DISPOSABLE_NOISE_SLEEP_EVENT_CODES
}, **{
    "sleeping": "难以入睡干预方案",
    "nightmare": "噩梦应激干预方案",
    "movement": "异常体动干预",
    "heart_rate_increase": "心率上升干预方案",
}}


def _get_sleep_intervention_scheme_text():
    global _sleep_intervention_scheme_cache
    if _sleep_intervention_scheme_cache is not None:
        return _sleep_intervention_scheme_cache
    try:
        with open(_SLEEP_INTERVENTION_SCHEME_PATH, "r", encoding="utf-8") as f:
            _sleep_intervention_scheme_cache = f.read()
    except OSError:
        _sleep_intervention_scheme_cache = ""
    return _sleep_intervention_scheme_cache


def _format_noise_trigger_cause(event_code):
    label = {
        "appliance_continuous": "家电持续声",
        "environment_continuous": "环境持续声",
        "neighbor_continuous": "邻里持续声",
        "nature_continuous": "自然持续声",
        "sudden_impact": "突发撞击声",
        "sudden_traffic": "突发交通声",
        "voice_doorbell": "人声/门铃声",
        "nature_sudden": "自然突发声",
        "object_sudden": "物品突发声",
    }.get(event_code)
    if not label:
        return "检测到环境噪音干扰"
    if event_code in _CONTINUOUS_NOISE_SLEEP_EVENT_CODES:
        obj = random.choice(_CONTINUOUS_NOISE_SOURCES)
        kind = "持续性环境声"
        db = random.randint(58, 82)   # 持续噪声类：58–82 dB（与文档事件表一致）
    else:
        sources = _DISPOSABLE_NOISE_SOURCES_BY_CODE.get(event_code) or list(
            {s for pool in _DISPOSABLE_NOISE_SOURCES_BY_CODE.values() for s in pool}
        )
        obj = random.choice(sources)
        kind = "突发性环境声"
        db = random.randint(62, 88)   # 突发噪声类：62–88 dB（与文档事件表一致）
    return f"检测到{label}（{kind}），约{db}分贝（疑似{obj}）"


def generate_ai_intervention_action_via_doubao(event_code, event_name_zh, trigger_context):
    """
    结合《异常事件干预方案》与事件类型生成 AI 干预事件的 action_taken 文案（本地模板，不调用大模型）。
    文案要求：动词主导的叙述句（如释放、播放、开启），而非参数式设备日志。
    """
    focus = _EVENT_CODE_TO_INTERVENTION_FOCUS.get(event_code)
    if not focus:
        return ""
    _get_sleep_intervention_scheme_text()
    return _fallback_intervention_action_taken(event_code)


def _fallback_intervention_action_taken(event_code):
    event_code = _noise_intervention_scheme_alias(event_code)
    variants = {
        "continuous_noise": [
            "调暗环境光，约在半秒内开始播放布朗噪音掩蔽环境声，音量不高于环境音低5分贝，并关闭香氛释放。",
            "保持暗光环境，开启布朗噪音并随环境声抬升，始终比环境音低约5分贝，同时关闭嗅觉输出。",
            "转入暗环境，播放布朗噪音稳态跟随环境，峰值不超过环境低5分贝，香氛维持关闭。",
        ],
        "disposable_noise": [
            "调暗环境光，约在半秒内播放短时布朗噪音，峰值低于突发声10分贝且整体不超过35分贝，约30秒后渐弱关闭。",
            "开启布朗噪音短时掩蔽突发声响，电平不超过35分贝、峰值克制在突发声下10分贝，数十秒后缓缓淡出。",
            "在暗环境下播放布朗噪音抑制突发声，满足不高于35分贝与峰值-10分贝关系，随后平滑退场。",
        ],
        "sleeping": [
            "释放薰衣草香氛间歇5秒/30秒，播放约22分贝布朗噪音，并开启呼吸引导光同步至约每分钟6次。",
            "开启呼吸引导光同步节律，同时播放22分贝布朗噪音，并按5秒开、30秒停间歇释放薰衣草香氛。",
            "播放22分贝布朗噪音，间歇释放薰衣草香氛5秒/30秒，配合呼吸引导光同步约每分钟6次。",
        ],
        "nightmare": [
            "保持环境昏暗，将布朗噪音在10秒内渐升至约25分贝再平滑回落，并按1秒/10秒间歇释放薰衣草香氛。",
            "播放布朗噪音并调至约25分贝，10秒内淡入淡出，同时以1秒开、10秒停的节奏释放薰衣草香氛，光线维持暗淡。",
            "在暗光下提升布朗噪音至约25分贝（10秒渐变），并间歇释放薰衣草香氛1秒/10秒以稳定情绪。",
        ],
        "movement": [
            "关闭主照明，在约3秒内淡入播放22至25分贝布朗噪音并保持稳定，同时关闭香氛释放。",
            "在无光环境下以22到25分贝布朗噪音安抚体动，3秒内完成淡入淡出过渡，香氛保持关闭。",
            "熄灭灯光，播放稳定在22至25分贝之间的布朗噪音，3秒内渐变到位，并停止释放香氛。",
        ],
        "heart_rate_increase": [
            "关闭照明，播放约30分贝布朗噪音，并以不高于0.1%浓度在3秒内淡入淡出地缓释薰衣草香氛。",
            "在无光环境下维持30分贝布朗噪音，低浓度薰衣草香氛缓慢释放且浓度不超过0.1%，声光渐变过渡。",
            "将布朗噪音稳定在30分贝，配合极低浓度薰衣草香氛缓释，灯光关闭并在3秒内完成淡入淡出。",
        ],
    }
    pool = variants.get(event_code)
    if not pool:
        return "按干预方案依次调节灯光、播放布朗噪音并视需要释放香氛。"
    return random.choice(pool)[:120]


def _ai_intervention_trigger_cause_for_code(event_code, event_name):
    alias = _noise_intervention_scheme_alias(event_code)
    if alias == "continuous_noise":
        return "系统检测到持续性环境噪音警报"
    if alias == "disposable_noise":
        return "系统检测到一次性环境噪音警报"
    if event_code == "sleeping":
        return "系统检测到入睡困难警报"
    if event_code == "nightmare":
        return "系统检测到噩梦应激警报"
    if event_code == "movement":
        return "系统检测到异常体动警报"
    if event_code == "heart_rate_increase":
        return "系统检测到心率异常上升警报"
    return f"系统检测到{event_name}警报"


def _ai_intervention_result_summary_for_code(event_code):
    alias = _noise_intervention_scheme_alias(event_code)
    return {
        "continuous_noise": "噪音干扰已缓解，睡眠保持稳定",
        "disposable_noise": "突发噪音已掩蔽，睡眠保持稳定",
        "sleeping": "成功诱导入睡，进入深度睡眠状态",
        "nightmare": "成功平复情绪，恢复正常睡眠状态",
        "movement": "成功减少体动，提升睡眠质量",
        "heart_rate_increase": "成功稳定心率，保障睡眠安全",
    }.get(alias, "成功保护睡眠，状态恢复正常")


def build_ai_intervention_detail_for_abnormal(
    event_code, event_info, parent_detail, use_doubao=False
):
    """为「AI主动干预」事件生成 detail。
    action_taken 均为本地模板。use_doubao 仅保留分支与旧调用兼容（True/False 均不再请求大模型）。
    """
    trigger_ctx = (parent_detail or {}).get("trigger_cause", "")
    if use_doubao:
        action = generate_ai_intervention_action_via_doubao(
            event_code, event_info["name"], trigger_ctx
        )
        if not action:
            action = _fallback_intervention_action_taken(event_code)
    else:
        action = _fallback_intervention_action_taken(event_code)
    return {
        "trigger_cause": _ai_intervention_trigger_cause_for_code(
            event_code, event_info["name"]
        ),
        "action_taken": action,
        "result_summary": _ai_intervention_result_summary_for_code(event_code),
    }


def finalize_sleep_event_detail(event_code, event_info):
    """复制事件模板 detail，并对噪音类写入 trigger_cause 模板文案。"""
    detail = dict(event_info["detail"])
    if event_code in _ALL_NOISE_SLEEP_EVENT_CODES:
        detail["trigger_cause"] = _format_noise_trigger_cause(event_code)
    return detail


# 保存睡眠事件到文件
def save_sleep_events_to_file(events, user_id):
    """
    将睡眠事件保存到JSON文件
    
    Args:
        events: 睡眠事件列表
        user_id: 用户ID
    """
    # 确保output目录存在
    os.makedirs('output', exist_ok=True)
    
    # 生成文件名
    filename = f"{user_id}_sleep_events.json"
    filepath = os.path.join('output', filename)
    
    atomic_write_json(filepath, events)

    print(f"睡眠事件数据已保存到: {filepath}")
    return filepath


def _sleep_events_aux_rows_by_date(user_id):
    """
    每个 user_id 只解析一次 output 下体征/环境大 JSON，并建立 record_date -> 行列表。
    供 generate_sleep_events 按日 O(1) 取数，避免「天数 × 全文件」重复 load。
    """
    if user_id in _SLEEP_EVENTS_AUX_INDEX_CACHE:
        return _SLEEP_EVENTS_AUX_INDEX_CACHE[user_id]

    def _build(path):
        rows = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    rows = data
            except Exception:
                rows = []
        by_d = {}
        for r in rows:
            d = r.get("record_date")
            if d:
                by_d.setdefault(d, []).append(r)
        return by_d

    out = {
        "vitals": _build(os.path.join("output", f"{user_id}_vitals_data.json")),
        "environment": _build(os.path.join("output", f"{user_id}_environment_data.json")),
    }
    _SLEEP_EVENTS_AUX_INDEX_CACHE[user_id] = out
    return out


# 生成睡眠事件
def generate_sleep_events(
    sleep_data,
    user_id,
    record_date,
    sleep_report_data=None,
    personality_type="M-L-C",
    use_doubao_for_intervention=None,
    allowed_sleep_event_codes=None,
    sleep_time_cfg_override=None,
    generation_options=None,
):
    """
    根据睡眠数据生成睡眠事件数据
    
    Args:
        sleep_data: 睡眠数据，包含入睡时间和起床时间
        user_id: 用户ID
        record_date: 与健康数据一致的记录日（YYYY-MM-DD）；跨午夜时刻仍用该日。
        sleep_report_data: 已废弃，传 None 即可（听觉事件不再依赖报告中的 audios）
        personality_type: 人格编码（如 M-H-R），驱动事件类型与时刻分布，与 new.md 睡眠画像一致
        use_doubao_for_intervention: 传入 build 的 use_doubao 分支；None 时用 sleepEventsAI。干预文案均为本地模板，不请求大模型
        allowed_sleep_event_codes: 已弃用（不再按 likelyNightSleepEvents 过滤事件）
        sleep_time_cfg_override: 若提供则用作 sleepTime（min/max 列表），不再从 config.json 解析该用户。
        generation_options: 来自 health_data_personas_config 的 generation 合并子树；可含 environment、vitals、event_triggers。
    
    Returns:
        睡眠事件列表，可能为空。每条事件含 duration_sec（事件持续秒数）。
    
    入睡困难（sleeping）由环境数据触发时：需「环境压力」（室温≥27℃ 或 噪音≥60dB）且「入睡时刻」落在
    config 中 sleepTime 窗口下对人格不利的一侧（晨型偏晚、夜型偏早）；时刻落在入睡后 50 分钟内，且每天最多一条。
    入睡困难仅当 raw_data.sleep_latency > 20（分钟）时才可出现；且仅落在 idf 第一个阶段（须为 awake），
    事件锚点分期须为 awake。
    无环境/体征锚点时，若干异常类型按「墙钟时段 × 权重」在入睡～起床窗内取偏好时刻，再与 idf 分期投影结合
    （突发交通声/家电持续声/邻里持续声/自然持续声/人声门铃等；突发撞击/自然突发另有低概率门控）。
    噩梦应激优先落在 01:00～04:00 与睡眠窗交集内，并按深睡/REM/浅睡加权，且每晚至多 1 条；
    心率上升不单独生成：仅在噩梦应激成功插入后，以约 40% 概率在其前 1.25～5 分钟（可扩至约 12 分钟）内的 REM 段插入一条，锚点时刻恒早于噩梦应激，且每晚至多 1 条；
    打鼾不早于首个浅睡段结束，且浅睡/深睡权重大于 REM。
    每一睡眠分期（light/deep/rem/awake）每晚最多锚定一条睡眠事件（AI 干预时刻不计入该配额）。
    主事件条数：异常类（type=abnormal）每晚至多 5 条、正常类（type=normal）至多 8 条（均不含 AI 主动干预）；
    可用环境变量 SLEEP_EVENTS_MAX_ABNORMAL_PRIMARY、SLEEP_EVENTS_MAX_NORMAL_PRIMARY 调整。
    事件收尾阶段会统一校正：后一个事件不与前一个 duration_sec 区间重叠，最后一个事件结束不晚于起床时间（入睡～wake_time）。
    """
    intervention_doubao = (
        sleepEventsAI if use_doubao_for_intervention is None else use_doubao_for_intervention
    )
    # 睡眠过程：入睡（sleep_time）→ 起床（wake_time）；无 wake_time 时退化为 wake_up_time
    sleep_time_str = sleep_data.get("raw_data", {}).get("sleep_time", "")
    wake_up_time_str = sleep_data.get("raw_data", {}).get("wake_up_time", "")
    wake_time_str = sleep_data.get("raw_data", {}).get("wake_time", "")

    if not sleep_time_str or (not wake_time_str and not wake_up_time_str):
        return []

    sleep_time = _parse_utc_iso_to_local_dt(sleep_time_str)
    window_end = _parse_utc_iso_to_local_dt(wake_time_str) or _parse_utc_iso_to_local_dt(
        wake_up_time_str
    )
    if not sleep_time or not window_end:
        return []
    if window_end < sleep_time:
        window_end += timedelta(days=1)

    bed_time_local = _parse_utc_iso_to_local_dt(
        (sleep_data.get("raw_data") or {}).get("bed_time", "") or ""
    )
    if bed_time_local and sleep_time and bed_time_local > sleep_time:
        bed_time_local = None

    dims = parse_personality_code(personality_type)
    profile = get_personality_profile(personality_type)

    # 定义事件类型映射（code / event_type / type 与 aaa.md 一致）
    event_mapping = {
        "appliance_continuous": {
            "type": "abnormal",
            "name": "家电持续声",
            "detail": {
                "trigger_cause": "",
                "action_taken": "触发噪音监测分析",
                "result_summary": "判定为持续性环境声干扰",
            },
        },
        "environment_continuous": {
            "type": "abnormal",
            "name": "环境持续声",
            "detail": {
                "trigger_cause": "",
                "action_taken": "触发噪音监测分析",
                "result_summary": "判定为持续性环境声干扰",
            },
        },
        "neighbor_continuous": {
            "type": "abnormal",
            "name": "邻里持续声",
            "detail": {
                "trigger_cause": "",
                "action_taken": "触发噪音监测分析",
                "result_summary": "判定为持续性环境声干扰",
            },
        },
        "nature_continuous": {
            "type": "abnormal",
            "name": "自然持续声",
            "detail": {
                "trigger_cause": "",
                "action_taken": "触发噪音监测分析",
                "result_summary": "判定为持续性环境声干扰",
            },
        },
        "sudden_impact": {
            "type": "abnormal",
            "name": "突发撞击声",
            "detail": {
                "trigger_cause": "",
                "action_taken": "触发噪音监测分析",
                "result_summary": "判定为突发性环境声干扰",
            },
        },
        "sudden_traffic": {
            "type": "abnormal",
            "name": "突发交通声",
            "detail": {
                "trigger_cause": "",
                "action_taken": "触发噪音监测分析",
                "result_summary": "判定为突发性环境声干扰",
            },
        },
        "voice_doorbell": {
            "type": "abnormal",
            "name": "人声/门铃声",
            "detail": {
                "trigger_cause": "",
                "action_taken": "触发噪音监测分析",
                "result_summary": "判定为突发性环境声干扰",
            },
        },
        "nature_sudden": {
            "type": "abnormal",
            "name": "自然突发声",
            "detail": {
                "trigger_cause": "",
                "action_taken": "触发噪音监测分析",
                "result_summary": "判定为突发性环境声干扰",
            },
        },
        "object_sudden": {
            "type": "abnormal",
            "name": "物品突发声",
            "detail": {
                "trigger_cause": "",
                "action_taken": "触发噪音监测分析",
                "result_summary": "判定为突发性环境声干扰",
            },
        },
        "sleeping": {
            "type": "abnormal",
            "name": "入睡困难",
            "detail": {
                "trigger_cause": "检测到入睡困难情况",
                "action_taken": "触发睡眠状态分析",
                "result_summary": "判定为入睡干扰问题",
            },
        },
        "nightmare": {
            "type": "abnormal",
            "name": "噩梦应激",
            "detail": {
                "trigger_cause": "检测到噩梦相关生理反应",
                "action_taken": "触发情绪状态分析",
                "result_summary": "判定为情绪干扰影响",
            },
        },
        "movement": {
            "type": "abnormal",
            "name": "异常体动",
            "detail": {
                "trigger_cause": "检测到异常体动模式",
                "action_taken": "触发体动模式分析",
                "result_summary": "判定为体动干扰问题",
            },
        },
        "heart_rate_increase": {
            "type": "abnormal",
            "name": "心率上升",
            "detail": {
                "trigger_cause": "检测到心率异常上升",
                "action_taken": "触发心脏状态分析",
                "result_summary": "判定为心率干扰影响",
            },
        },
        "snoring": {
            "type": "normal",
            "name": "打鼾",
            "detail": {
                "trigger_cause": "检测到用户打鼾",
                "action_taken": "触发身体指标分析",
                "result_summary": "判定为唤醒干扰",
            },
        },
        "sleep_talking": {
            "type": "normal",
            "name": "梦话",
            "detail": {
                "trigger_cause": "检测到梦话现象",
                "action_taken": "触发睡眠状态分析",
                "result_summary": "判定为轻微干扰情况",
            },
        },
        "once_movement": {
            "type": "normal",
            "name": "单次体动",
            "detail": {
                "trigger_cause": "检测到单次体动",
                "action_taken": "触发体动情况分析",
                "result_summary": "判定为正常干扰现象",
            },
        },
        "natural_movement": {
            "type": "normal",
            "name": "自然微动",
            "detail": {
                "trigger_cause": "检测到自然微动",
                "action_taken": "触发睡眠状态分析",
                "result_summary": "判定为无干扰情况",
            },
        },
        "posture_switch": {
            "type": "normal",
            "name": "睡眠姿势切换",
            "detail": {
                "trigger_cause": "检测到睡眠姿势切换",
                "action_taken": "触发姿势变化分析",
                "result_summary": "判定为姿势干扰影响",
            },
        },
        "cough_clearing": {
            "type": "normal",
            "name": "咳嗽",
            "detail": {
                "trigger_cause": "检测到咳嗽",
                "action_taken": "触发呼吸道状态分析",
                "result_summary": "判定为生理干扰情况",
            },
        },
        "swallow": {
            "type": "normal",
            "name": "吞咽",
            "detail": {
                "trigger_cause": "检测到正常吞咽动作",
                "action_taken": "触发吞咽频率分析",
                "result_summary": "判定为无干扰现象",
            },
        },
        "limb_movements": {
            "type": "normal",
            "name": "肢体动作",
            "detail": {
                "trigger_cause": "检测到肢体动作",
                "action_taken": "触发动作模式分析",
                "result_summary": "判定为正常干扰情况",
            },
        },
        "breathing": {
            "type": "normal",
            "name": "呼吸声",
            "detail": {
                "trigger_cause": "检测到呼吸声",
                "action_taken": "触发呼吸节律分析",
                "result_summary": "判定为正常干扰现象",
            },
        },
        "silence": {
            "type": "normal",
            "name": "安静",
            "detail": {
                "trigger_cause": "检测到环境安静",
                "action_taken": "触发环境声学分析",
                "result_summary": "判定为无显著干扰",
            },
        },
    }

    resolved_user_profile = None
    sleep_time_cfg = None
    if sleep_time_cfg_override is not None:
        sleep_time_cfg = sleep_time_cfg_override
    else:
        try:
            config_path = os.path.join(PROJECT_ROOT, "config", "config.json")
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                for p in cfg.get("user_profiles", []):
                    if p.get("user_id") == user_id:
                        resolved_user_profile = p
                        break
        except Exception:
            pass
        sleep_time_cfg = (resolved_user_profile or {}).get("sleepTime")

    _go = generation_options if isinstance(generation_options, dict) else {}
    _sod = (_go.get("event_triggers") or {}).get("sleep_onset_difficulty") or {}
    _sleep_latency_exclusive_min = 20
    if "sleep_latency_minutes_min" in _sod:
        try:
            _sleep_latency_exclusive_min = int(_sod["sleep_latency_minutes_min"])
        except (TypeError, ValueError):
            _sleep_latency_exclusive_min = 20
    _env_gen = _go.get("environment") or {}
    try:
        _sleep_pressure_temp_c = float(_env_gen.get("sleep_onset_hot_temp_c", 27))
    except (TypeError, ValueError):
        _sleep_pressure_temp_c = 27.0
    try:
        _sleep_pressure_noise_min = int(_env_gen.get("sleep_pressure_noise_min", 60))
    except (TypeError, ValueError):
        _sleep_pressure_noise_min = 60
    _vit_gen = _go.get("vitals") or {}
    if _vit_gen.get("night_hr_abnormal_min") is not None:
        try:
            _nightmare_vitals_hr_min = int(_vit_gen["night_hr_abnormal_min"])
        except (TypeError, ValueError):
            _nightmare_vitals_hr_min = None
    else:
        _nightmare_vitals_hr_min = None

    idf_data = sleep_data.get("idf_data") or []
    sleep_latency_minutes = int(
        sleep_data.get("raw_data", {}).get("sleep_latency", 0) or 0
    )
    stage_windows = _build_stage_windows_from_idf(idf_data, sleep_time, window_end)
    middle_awake_windows = _middle_awake_windows_from_idf(
        idf_data, sleep_time, window_end
    )
    if middle_awake_windows:
        stage_windows["awake"] = middle_awake_windows
    else:
        stage_windows["awake"] = []
    used_event_stage_counts = {"light": 0, "deep": 0, "rem": 0, "awake": 0}
    # rem=2：允许「噩梦应激 + 联动心率上升」同晚均锚在 REM（仍受异常主事件总数等约束）
    stage_event_caps = {"light": 1, "deep": 1, "rem": 2, "awake": 1}
    abnormal_stage_counts = {"light": 0, "deep": 0, "rem": 0, "awake": 0}
    abnormal_stage_caps = {"light": 1, "deep": 1, "rem": 2, "awake": 1}
    max_abnormal_per_code = max(
        1, int(os.getenv("SLEEP_EVENTS_MAX_ABNORMAL_PER_CODE", "2"))
    )
    sleeping_windows = eligible_sleeping_windows(
        idf_data,
        sleep_time,
        window_end,
        sleep_latency_minutes,
        bed_time_local=bed_time_local,
        latency_exclusive_min=_sleep_latency_exclusive_min,
    )

    def try_reserve_event_anchor(local_dt):
        """若锚点落在某分期且该分期仍有配额，占用并返回该时刻；否则返回 None。"""
        if local_dt is None:
            return None
        local_dt = clamp_dt_to_sleep_window(local_dt, sleep_time, window_end)
        stg = _sleep_stage_at_event_anchor(local_dt, idf_data, sleep_time, window_end)
        if not stg:
            return None
        if used_event_stage_counts.get(stg, 0) >= stage_event_caps.get(stg, 1):
            return None
        used_event_stage_counts[stg] = used_event_stage_counts.get(stg, 0) + 1
        return local_dt

    def _pick_event_dt_for_stage_once(stage_order, preferred_dt=None):
        available_stage_order = [
            s
            for s in stage_order
            if used_event_stage_counts.get(s, 0) < stage_event_caps.get(s, 1)
        ]
        if not available_stage_order:
            return None
        candidate_windows = []
        for stg in available_stage_order:
            candidate_windows.extend(stage_windows.get(stg, []))
        if not candidate_windows:
            return None
        if preferred_dt is not None:
            dt = _project_dt_to_windows(preferred_dt, candidate_windows)
        else:
            fallback_dt = clamp_dt_to_sleep_window(sleep_time, sleep_time, window_end)
            dt = _pick_random_dt_in_stage_windows(candidate_windows, fallback_dt)
        dt = clamp_dt_to_sleep_window(dt, sleep_time, window_end)

        selected_stage = _sleep_stage_at_event_anchor(
            dt, idf_data, sleep_time, window_end
        )
        if not selected_stage or selected_stage not in available_stage_order:
            nearest = None
            nearest_dist = None
            nearest_stg = None
            for stg in available_stage_order:
                for st, ed in stage_windows.get(stg, []):
                    cand = dt if st <= dt <= ed else (st if dt < st else ed)
                    dist = abs((cand - dt).total_seconds())
                    if nearest_dist is None or dist < nearest_dist:
                        nearest_dist = dist
                        nearest = cand
                        nearest_stg = stg
            if nearest_stg is None:
                return None
            dt = clamp_dt_to_sleep_window(nearest, sleep_time, window_end)
            selected_stage = nearest_stg
        if used_event_stage_counts.get(selected_stage, 0) >= stage_event_caps.get(
            selected_stage, 1
        ):
            return None
        used_event_stage_counts[selected_stage] = (
            used_event_stage_counts.get(selected_stage, 0) + 1
        )
        return dt

    def pick_sleeping_event_time(preferred_dt=None):
        wins = sleeping_windows
        if not wins:
            return None

        def _early_bias_windows(src_windows):
            """
            入睡困难锚点偏向首个 awake 段起点：
            - 仍严格位于第一个 awake start~end 内
            - 仅在前 35% 或前 8 分钟（取更小）窗口内取点
            """
            out = []
            for st, ed in src_windows:
                span = max(1.0, (ed - st).total_seconds())
                early_span = min(span * 0.35, 8 * 60.0)
                early_span = max(60.0, early_span)
                e_end = min(ed, st + timedelta(seconds=early_span))
                if e_end <= st:
                    e_end = st + timedelta(seconds=1.0)
                out.append((st, e_end))
            return out

        early_wins = _early_bias_windows(wins)

        def _awake_cap_ok():
            return used_event_stage_counts.get("awake", 0) < stage_event_caps.get(
                "awake", 1
            )

        def _try_commit_if_awake_anchor(dt):
            stg = _sleep_stage_at_event_anchor(dt, idf_data, sleep_time, window_end)
            if stg != "awake" or not _awake_cap_ok():
                return None
            used_event_stage_counts["awake"] = used_event_stage_counts.get("awake", 0) + 1
            return dt

        if preferred_dt is not None:
            dt = clamp_dt_for_sleeping_event(
                _project_dt_to_windows(preferred_dt, early_wins),
                bed_time_local,
                sleep_time,
                window_end,
            )
            committed = _try_commit_if_awake_anchor(dt)
            if committed is not None:
                return committed
            st0, ed0 = early_wins[0]
            span0 = max(0.0, (ed0 - st0).total_seconds())
            if span0 > 2.0:
                for frac in (0.08, 0.16, 0.24, 0.32, 0.45):
                    alt = st0 + timedelta(seconds=max(1.0, (span0 - 1.5) * frac))
                    alt = clamp_dt_for_sleeping_event(
                        alt, bed_time_local, sleep_time, window_end
                    )
                    committed = _try_commit_if_awake_anchor(alt)
                    if committed is not None:
                        return committed
        for _ in range(48):
            st, ed = random.choice(early_wins)
            span_sec = max(0.0, (ed - st).total_seconds())
            if span_sec <= 1.0:
                dt = st
            else:
                dt = st + timedelta(
                    seconds=random.uniform(1.0, max(2.0, span_sec - 0.5))
                )
            dt = clamp_dt_for_sleeping_event(
                dt, bed_time_local, sleep_time, window_end
            )
            committed = _try_commit_if_awake_anchor(dt)
            if committed is not None:
                return committed
        return None

    def pick_event_time_by_stage(event_code, preferred_dt=None):
        if event_code == "sleeping":
            return pick_sleeping_event_time(preferred_dt=preferred_dt)

        if event_code == "nightmare":
            dt_pol = _pick_nightmare_anchor_policy(sleep_time, window_end, idf_data)
            if dt_pol is not None:
                stg = _sleep_stage_at_event_anchor(dt_pol, idf_data, sleep_time, window_end)
                if stg and used_event_stage_counts.get(stg, 0) < stage_event_caps.get(stg, 1):
                    used_event_stage_counts[stg] = used_event_stage_counts.get(stg, 0) + 1
                    return dt_pol
            return _pick_event_dt_for_stage_once(
                _SLEEP_EVENT_STAGE_PREFERENCES.get(
                    "nightmare", ("rem", "deep", "light")
                ),
                preferred_dt=preferred_dt,
            )

        stage_order = _SLEEP_EVENT_STAGE_PREFERENCES.get(event_code)
        if stage_order:
            return _pick_event_dt_for_stage_once(stage_order, preferred_dt=preferred_dt)
        return stage_aware_sleep_event_time(
            event_code,
            sleep_time,
            window_end,
            dims,
            profile,
            idf_data=idf_data,
            preferred_dt=preferred_dt,
            sleep_latency_minutes=sleep_latency_minutes,
            bed_time_local=bed_time_local,
            latency_exclusive_min=_sleep_latency_exclusive_min,
        )

    # 生成事件时间点（同一 code 可重复出现，如多次打鼾/多段噪声）
    events = []
    max_abnormal_primary = SLEEP_EVENTS_MAX_ABNORMAL_PRIMARY
    max_normal_primary = SLEEP_EVENTS_MAX_NORMAL_PRIMARY
    # 噩梦应激后 AI 干预锚点（分钟）：与噩梦主事件时间对齐
    nightmare_awake_offset_min = 2.5
    sleeping_primary_emitted = False
    nightmare_pair_seq = 0

    def _pick_nightmare_following_awake_anchor(nightmare_dt, search_minutes=30):
        """从噩梦后的 awake 窗口中挑一个锚点；若无可用 awake 窗口则返回 None。"""
        if nightmare_dt is None:
            return None
        awake_wins = list(stage_windows.get("awake") or [])
        if not awake_wins:
            return None
        preferred = clamp_dt_to_sleep_window(
            nightmare_dt + timedelta(minutes=nightmare_awake_offset_min),
            sleep_time,
            window_end,
        )
        search_end = nightmare_dt + timedelta(minutes=max(1, int(search_minutes)))
        candidates = []
        for st, ed in awake_wins:
            if ed <= nightmare_dt or st >= search_end:
                continue
            lo = max(st, nightmare_dt)
            hi = min(ed, search_end)
            if lo <= hi:
                candidates.append((lo, hi))
        if not candidates:
            return None
        for lo, hi in candidates:
            if lo <= preferred <= hi:
                return preferred
        return candidates[0][0]

    def _primary_events():
        return [e for e in events if e.get("event_type") != "AI主动干预"]

    def _primary_events_count():
        return len(_primary_events())

    def _abnormal_primary_count():
        return sum(1 for e in _primary_events() if e.get("type") == "abnormal")

    def _normal_primary_count():
        return sum(1 for e in _primary_events() if e.get("type") == "normal")

    def _abnormal_code_count(code):
        return sum(
            1
            for e in events
            if e.get("type") == "abnormal"
            and e.get("event_type") != "AI主动干预"
            and e.get("code") == code
        )

    def append_abnormal_event_pair(
        event_code,
        event_time_local,
        _from_nightmare=False,
        _nightmare_pair_key=None,
        _from_nightmare_link=False,
    ):
        """生成异常主事件；AI 干预在函数末尾按一对一规则统一补齐。"""
        nonlocal sleeping_primary_emitted, nightmare_pair_seq
        if _abnormal_primary_count() >= max_abnormal_primary:
            return False
        if event_code == "heart_rate_increase" and not _from_nightmare_link:
            return False
        if event_code == "sleeping" and sleeping_primary_emitted:
            return False
        # 每种异常事件（按 code）每晚最多发生 2 次；噩梦应激、心率上升各仅允许 1 次
        _abnormal_cap = (
            1
            if event_code in ("heart_rate_increase", "nightmare")
            else max_abnormal_per_code
        )
        if _abnormal_code_count(event_code) >= _abnormal_cap:
            return False
        if event_code == "sudden_impact" and random.random() >= 0.20:
            return False
        if event_code == "nature_sudden" and random.random() >= 0.15:
            return False
        if event_code == "movement" and random.random() >= 0.40:
            return False
        event_info = event_mapping[event_code]
        if event_time_local is None:
            pol_dt = _policy_preferred_anchor_dt(
                event_code, sleep_time, window_end, idf_data
            )
            if pol_dt is not None:
                event_time_local = pol_dt
        event_time_local = pick_event_time_by_stage(
            event_code, preferred_dt=event_time_local
        )
        if event_time_local is None:
            return False
        abnormal_stage = _sleep_stage_at_event_anchor(
            event_time_local, idf_data, sleep_time, window_end
        )
        if not abnormal_stage:
            return False
        if event_code == "heart_rate_increase" and abnormal_stage != "rem":
            return False
        if abnormal_stage_counts.get(abnormal_stage, 0) >= abnormal_stage_caps.get(
            abnormal_stage, 1
        ):
            return False
        event_timestamp = format_sleep_event_local_timestamp(event_time_local)
        detail = finalize_sleep_event_detail(event_code, event_info)
        if event_code == "nightmare" and not _nightmare_pair_key:
            nightmare_pair_seq += 1
            _nightmare_pair_key = f"nightmare_pair_{nightmare_pair_seq}"
        event = {
            "uid": user_id,
            "record_date": record_date,
            "event_timestamp": event_timestamp,
            "event_type": event_info["name"],
            "type": event_info["type"],
            "code": event_code,
            "detail": detail,
            "related_event_id": "",
            "sort_order": 0,
            "create_time": generate_iso_date(),
            "update_time": generate_iso_date(),
        }
        if _nightmare_pair_key and event_code in {"nightmare", "heart_rate_increase"}:
            event["_nightmare_pair_key"] = _nightmare_pair_key
        events.append(event)
        abnormal_stage_counts[abnormal_stage] = (
            abnormal_stage_counts.get(abnormal_stage, 0) + 1
        )
        if event_code == "nightmare":
            _try_append_heart_rate_linked_before_nightmare(event)
        if event_code == "sleeping":
            sleeping_primary_emitted = True
        return True

    def _try_append_heart_rate_linked_before_nightmare(nightmare_event):
        """噩梦主事件落库后，按概率在噩床前 1.25～5 分钟（可扩至约 12 分钟）内的 REM 段插入心率上升（不单独生成心率）。"""
        if not isinstance(nightmare_event, dict) or nightmare_event.get("code") != "nightmare":
            return
        if nightmare_event.get("event_type") == "AI主动干预":
            return
        if _abnormal_code_count("heart_rate_increase") >= 1:
            return
        if _abnormal_primary_count() >= max_abnormal_primary:
            return
        if random.random() >= float(os.getenv("SLEEP_EVENTS_NIGHTMARE_LINKED_HR_PROB", "0.40")):
            return
        ndt = session_anchor_event_local_dt(nightmare_event, sleep_time, window_end)
        if ndt == datetime.min:
            return
        gmin = float(os.getenv("SLEEP_EVENTS_NIGHTMARE_HR_MIN_GAP_MIN", "1.25"))
        gmax = float(os.getenv("SLEEP_EVENTS_NIGHTMARE_HR_MAX_GAP_MIN", "5.0"))
        slices = _rem_slices_before_nightmare_dt(
            ndt, sleep_time, window_end, idf_data, gmin, gmax
        )
        if not slices:
            slices = _rem_slices_before_nightmare_dt(
                ndt, sleep_time, window_end, idf_data, gmin, min(12.0, gmax + 7.0)
            )
        if not slices:
            return
        hr_dt = clamp_dt_to_sleep_window(
            _pick_random_dt_in_stage_windows(slices, ndt),
            sleep_time,
            window_end,
        )
        if hr_dt >= ndt:
            return
        if _sleep_stage_at_event_anchor(hr_dt, idf_data, sleep_time, window_end) != "rem":
            return
        pair_k = nightmare_event.get("_nightmare_pair_key")
        append_abnormal_event_pair(
            "heart_rate_increase",
            hr_dt,
            _nightmare_pair_key=pair_k,
            _from_nightmare_link=True,
        )

    aux = _sleep_events_aux_rows_by_date(user_id)
    vitals_rows = aux["vitals"].get(record_date, [])
    env_rows = aux["environment"].get(record_date, [])

    sleep_difficulty_bedtime_ok = bedtime_supports_sleep_difficulty(
        sleep_time, sleep_time_cfg, personality_type
    )

    vitals_abnormal_used = 0
    for row in vitals_rows:
        local_dt = collected_at_to_local_naive_dt(row.get("collected_at"))
        if local_dt is None or not (sleep_time <= local_dt <= window_end):
            continue
        hr = (row.get("metrics") or {}).get("heart_rate", 0)
        if _nightmare_vitals_hr_min is not None:
            if hr <= _nightmare_vitals_hr_min:
                continue
        elif hr < 92:
            continue
        if vitals_abnormal_used >= SLEEP_EVENTS_MAX_VITALS_ABNORMAL:
            break
        # 体征偏高时尝试插入噩梦应激（时刻由 01:00～04:00 分期策略主导）
        if append_abnormal_event_pair("nightmare", None):
            vitals_abnormal_used += 1

    sleeping_from_env_done = False
    env_high_noise_times_for_snore = []
    env_noise_abnormal_used = 0
    for row in env_rows:
        local_dt = collected_at_to_local_naive_dt(row.get("collected_at"))
        if local_dt is None or not (sleep_time <= local_dt <= window_end):
            continue
        noise = int(row.get("noise") or 0)
        temp = int(row.get("temperature") or 0)
        if noise >= 56:
            if len(env_high_noise_times_for_snore) < SLEEP_EVENTS_MAX_SNORE_NOISE_ANCHORS:
                env_high_noise_times_for_snore.append(local_dt)
        if noise >= 56 and env_noise_abnormal_used < SLEEP_EVENTS_MAX_ENV_NOISE_ABNORMAL:
            pool = (
                list(_CONTINUOUS_NOISE_SLEEP_EVENT_CODES)
                if noise >= 72
                else list(_DISPOSABLE_NOISE_SLEEP_EVENT_CODES)
            )
            if pool:
                code = _pick_env_noise_sleep_event_code(
                    local_dt, pool, sleep_time, window_end
                )
                if code:
                    append_abnormal_event_pair(code, local_dt)
                    env_noise_abnormal_used += 1
        # 入睡困难：需同时满足「环境压力」与「入睡时刻相对典型窗口不利」
        env_sleep_stress = (temp >= _sleep_pressure_temp_c) or (
            noise >= _sleep_pressure_noise_min
        )
        if (
            env_sleep_stress
            and sleep_difficulty_bedtime_ok
        ):
            mins_onset = (local_dt - sleep_time).total_seconds() / 60.0
            if 0 <= mins_onset <= 50 and not sleeping_from_env_done:
                append_abnormal_event_pair("sleeping", local_dt)
                sleeping_from_env_done = True
    
    # 听觉类事件（打鼾/梦话/咳嗽）：条数与旧 generate_auditory 随机规则一致，不依赖睡眠报告
    env_high_noise_times_for_snore.sort()

    def _append_auditory_event(event_code, fixed_local_dt=None):
        if _normal_primary_count() >= max_normal_primary:
            return
        event_info = event_mapping.get(event_code, event_mapping["sleep_talking"])
        event_time = None
        if fixed_local_dt is not None:
            event_time = try_reserve_event_anchor(fixed_local_dt)
        if event_time is None:
            event_time = pick_event_time_by_stage(event_code)
        if event_time is None:
            return
        event_timestamp = format_sleep_event_local_timestamp(event_time)
        event = {
            "uid": user_id,
            "record_date": record_date,
            "event_timestamp": event_timestamp,
            "event_type": event_info["name"],
            "type": event_info["type"],
            "code": event_code,
            "detail": finalize_sleep_event_detail(event_code, event_info),
            "related_event_id": "",
            "sort_order": 0,
            "create_time": generate_iso_date(),
            "update_time": generate_iso_date(),
        }
        events.append(event)

    n_snore, include_sleep_talk, include_cough = plan_auditory_snore_talk_cough_counts(sleep_data)
    # 打鼾条数为 0 或 3～6：锚点不占用「每分期一条」的全局配额；重排时刻时允许与异常事件锚点重叠（见 assign_duration_and_retime_sleep_events）
    placed_snore_local_dts = []

    def _pick_snoring_anchor_dt(fixed_local_dt):
        """打鼾锚点不占 used_event_stage_counts；高噪时刻仅作 preferred_dt 投影。"""
        min_gap_sec = 8 * 60
        for _ in range(40):
            t = None
            if isinstance(idf_data, list) and idf_data:
                t = _weighted_snoring_anchor_dt(
                    idf_data, sleep_time, window_end, dims, profile, fixed_local_dt=fixed_local_dt
                )
                if t is not None:
                    t = _ensure_snoring_anchor_not_in_awake_stage(
                        t, idf_data, sleep_time, window_end, dims, profile
                    )
            if t is None:
                t = stage_aware_sleep_event_time(
                    "snoring",
                    sleep_time,
                    window_end,
                    dims,
                    profile,
                    idf_data=idf_data,
                    preferred_dt=fixed_local_dt,
                    latency_exclusive_min=_sleep_latency_exclusive_min,
                )
            t = clamp_dt_to_sleep_window(t, sleep_time, window_end)
            if not placed_snore_local_dts:
                return t
            if all(abs((t - u).total_seconds()) >= min_gap_sec for u in placed_snore_local_dts):
                return t
        return None

    def _append_snoring_event(fixed_local_dt=None):
        if _normal_primary_count() >= max_normal_primary:
            return False
        event_code = "snoring"
        event_info = event_mapping.get(event_code, event_mapping["sleep_talking"])
        event_time = _pick_snoring_anchor_dt(fixed_local_dt)
        if event_time is None:
            return False
        placed_snore_local_dts.append(event_time)
        event_timestamp = format_sleep_event_local_timestamp(event_time)
        event = {
            "uid": user_id,
            "record_date": record_date,
            "event_timestamp": event_timestamp,
            "event_type": event_info["name"],
            "type": event_info["type"],
            "code": event_code,
            "detail": finalize_sleep_event_detail(event_code, event_info),
            "related_event_id": "",
            "sort_order": 0,
            "create_time": generate_iso_date(),
            "update_time": generate_iso_date(),
        }
        events.append(event)
        return True

    for _ in range(n_snore):
        if _normal_primary_count() >= max_normal_primary:
            break
        fixed_snore_dt = (
            env_high_noise_times_for_snore.pop(0)
            if env_high_noise_times_for_snore
            else None
        )
        _append_snoring_event(fixed_local_dt=fixed_snore_dt)
    if n_snore >= 3:
        got_snore = sum(1 for e in events if e.get("code") == "snoring")
        if got_snore < 3:
            events = [e for e in events if e.get("code") != "snoring"]
        elif got_snore > 6:
            sn_evs = [e for e in events if e.get("code") == "snoring"]
            sn_evs.sort(key=lambda e: parse_sleep_event_timestamp_to_dt(e))
            drop = {id(x) for x in sn_evs[6:]}
            events = [e for e in events if id(e) not in drop]
    if include_sleep_talk:
        _append_auditory_event("sleep_talking")
    if include_cough:
        for _ in range(3):
            if _normal_primary_count() >= max_normal_primary:
                break
            _append_auditory_event("cough_clearing")

    abnormal_codes = [
        code
        for code, info in event_mapping.items()
        if info["type"] == "abnormal" and code != "heart_rate_increase"
    ]
    normal_codes = [
        code for code, info in event_mapping.items() if info["type"] == "normal"
    ]
    # 默认将异常事件占比轻度上调，保持“稍微多一点”而非极端偏多
    target_abnormal_ratio = max(
        0.0, min(0.6, float(os.getenv("SLEEP_EVENTS_TARGET_ABNORMAL_RATIO", "0.40")))
    )

    def _abnormal_ratio_info():
        prim = _primary_events()
        total = len(prim)
        if total <= 0:
            return 0.0, 0, 0
        abnormal_n = sum(1 for e in prim if e.get("type") == "abnormal")
        return abnormal_n / float(total), total, abnormal_n

    def _append_abnormal_for_ratio():
        if not abnormal_codes:
            return False
        if _abnormal_primary_count() >= max_abnormal_primary:
            return False
        for _ in range(max(3, len(abnormal_codes) * 2)):
            event_code = weighted_pick_sleep_event_code(abnormal_codes, dims, profile)
            if not event_code:
                event_code = random.choice(abnormal_codes)
            if append_abnormal_event_pair(event_code, None):
                return True
        return False

    # 异常事件：提高「至少一条异常主事件」概率，并额外抽样补异常（_append_abnormal_for_ratio）
    if not any(event.get("type") == "abnormal" for event in events):
        if abnormal_codes and random.random() < 0.78:
            event_code = weighted_pick_sleep_event_code(abnormal_codes, dims, profile)
            append_abnormal_event_pair(event_code, None)
    for _ in range(2):
        if _abnormal_primary_count() >= max_abnormal_primary:
            break
        if abnormal_codes and random.random() < 0.58:
            _append_abnormal_for_ratio()

    # 确保生成至少一个正常事件
    if not any(event.get("type") == "normal" for event in events):
        if normal_codes and _normal_primary_count() < max_normal_primary:
            event_code = weighted_pick_sleep_event_code(normal_codes, dims, profile)
            _append_auditory_event(event_code)
    
    # 打鼾 0 或 3～6 条、梦话 0-1 次、咳嗽 0 或 3 次，均不强制每天出现
    
    event_count = personality_extra_event_count(dims, profile)
    if random.random() < 0.35:
        event_count = min(event_count + 1, 8)
    for i in range(event_count):
        if _abnormal_primary_count() >= max_abnormal_primary and _normal_primary_count() >= max_normal_primary:
            break
        all_codes = [c for c in event_mapping.keys() if c != "heart_rate_increase"]
        if not all_codes:
            break
        cur_ratio, _, _ = _abnormal_ratio_info()
        pick_pool = all_codes
        if abnormal_codes and cur_ratio < target_abnormal_ratio:
            # 轻度偏向异常，提升出现率但仍保留明显随机性
            pick_pool = abnormal_codes if random.random() < 0.58 else all_codes
        event_code = weighted_pick_sleep_event_code(pick_pool, dims, profile)
        if not event_code:
            break
        if event_code == "sleeping" and sleeping_primary_emitted:
            continue
        if event_mapping.get(event_code, {}).get("type") == "abnormal":
            if _abnormal_primary_count() >= max_abnormal_primary:
                continue
            append_abnormal_event_pair(event_code, None)
            continue
        if _normal_primary_count() >= max_normal_primary:
            continue
        _append_auditory_event(event_code)
        if event_code == "sleeping":
            sleeping_primary_emitted = True

    # 心率上升仅随噩梦应激联动生成（见 append_abnormal_event_pair 内 _try_append_heart_rate_linked_before_nightmare）

    # 整晚打鼾：0 或 3～6 条（人格片段循环等不得留下 1～2 条；超过 6 条则按时间保留前 6 条）
    _sn_primary = [
        e for e in events if e.get("code") == "snoring" and e.get("event_type") != "AI主动干预"
    ]
    _sn_primary_n = len(_sn_primary)
    if _sn_primary_n in (1, 2):
        events = [e for e in events if e.get("code") != "snoring"]
    elif _sn_primary_n > 6:
        _sn_primary.sort(key=lambda e: parse_sleep_event_timestamp_to_dt(e))
        drop = {id(x) for x in _sn_primary[6:]}
        events = [e for e in events if id(e) not in drop]

    # 睡眠姿势切换：强制落在入睡～起床窗内（防 record_date+HH:MM 解析越界）
    for ev in events:
        enforce_posture_switch_in_sleep_window(ev, sleep_time, window_end, record_date)

    # 终态硬约束：每种异常主事件（按 code）每晚最多 2 条，噩梦应激与心率上升各最多 1 条；异常主事件总数不超过 max_abnormal_primary（AI 不计入）
    abnormal_primary = [
        e for e in events if e.get("type") == "abnormal" and e.get("event_type") != "AI主动干预"
    ]
    if abnormal_primary:
        keep_ids = set()
        keep_count_by_code = {}
        abnormal_primary_sorted = sorted(abnormal_primary, key=lambda e: parse_sleep_event_timestamp_to_dt(e))
        for e in abnormal_primary_sorted:
            c = e.get("code") or ""
            n = keep_count_by_code.get(c, 0)
            cap = 1 if c in ("heart_rate_increase", "nightmare") else max_abnormal_per_code
            if n < cap:
                keep_ids.add(id(e))
                keep_count_by_code[c] = n + 1
        events = [
            e
            for e in events
            if not (e.get("type") == "abnormal" and e.get("event_type") != "AI主动干预" and id(e) not in keep_ids)
        ]

    abnormal_primary_all = [
        e
        for e in events
        if e.get("type") == "abnormal" and e.get("event_type") != "AI主动干预"
    ]
    if len(abnormal_primary_all) > max_abnormal_primary:
        abnormal_primary_all.sort(key=lambda e: parse_sleep_event_timestamp_to_dt(e))
        drop_ab_extra = {id(x) for x in abnormal_primary_all[max_abnormal_primary:]}
        events = [
            e
            for e in events
            if not (
                e.get("type") == "abnormal"
                and e.get("event_type") != "AI主动干预"
                and id(e) in drop_ab_extra
            )
        ]

    normal_primary_all = [
        e
        for e in events
        if e.get("type") == "normal" and e.get("event_type") != "AI主动干预"
    ]
    if len(normal_primary_all) > max_normal_primary:
        normal_primary_all.sort(key=lambda e: parse_sleep_event_timestamp_to_dt(e))
        drop_nm_extra = {id(x) for x in normal_primary_all[max_normal_primary:]}
        events = [
            e
            for e in events
            if not (
                e.get("type") == "normal"
                and e.get("event_type") != "AI主动干预"
                and id(e) in drop_nm_extra
            )
        ]

    # 为每条事件补 duration_sec，并按规则重排时刻（不重叠、末尾不越界、首条边界）
    events = assign_duration_and_retime_sleep_events(
        events, sleep_time, window_end, retime=True
    )
    
    # 先确保每条事件都有 _id
    for event in events:
        if "_id" not in event or not event.get("_id"):
            event["_id"] = generate_object_id()

    # 硬约束：每一个异常主事件后面都紧跟一个 AI 干预事件（一对一）
    paired_events = []
    for event in events:
        if event.get("event_type") == "AI主动干预":
            continue
        paired_events.append(event)
        if event.get("type") == "abnormal":
            event_code = event.get("code")
            event_info = event_mapping.get(event_code, {})
            base_detail = event.get("detail") if isinstance(event.get("detail"), dict) else finalize_sleep_event_detail(event_code, event_info)
            base_local_dt = session_anchor_event_local_dt(event, sleep_time, window_end)
            if base_local_dt == datetime.min:
                base_local_dt = sleep_time
            if event_code == "nightmare":
                ai_event_time = clamp_dt_to_sleep_window(
                    base_local_dt + timedelta(minutes=nightmare_awake_offset_min),
                    sleep_time,
                    window_end,
                )
            else:
                ai_event_time = clamp_dt_to_sleep_window(
                    base_local_dt + timedelta(minutes=random.uniform(2.0, 3.0)),
                    sleep_time,
                    window_end,
                )
            ai_event_timestamp = format_sleep_event_local_timestamp(ai_event_time)
            ai_detail = build_ai_intervention_detail_for_abnormal(
                event_code, event_info, base_detail, use_doubao=intervention_doubao
            )
            ai_event = {
                "uid": user_id,
                "record_date": record_date,
                "event_timestamp": ai_event_timestamp,
                "event_type": "AI主动干预",
                "type": "intervention",
                "code": event_code,
                "detail": ai_detail,
                "related_event_id": event.get("_id") or "",
                "sort_order": 0,
                "create_time": generate_iso_date(),
                "update_time": generate_iso_date(),
                "duration_sec": _pick_sleep_event_duration_sec({"event_type": "AI主动干预"}),
                "_id": generate_object_id(),
            }
            paired_events.append(ai_event)
    events = paired_events
    # 插入 AI 事件后再次统一重排，确保全量事件时间不重叠且不越界
    events = assign_duration_and_retime_sleep_events(
        events, sleep_time, window_end, retime=True
    )
    # 心率上升仅允许锚在 REM：不再将「噩梦+心率」成对事件改写到清醒段（与分期策略一致）
    # 兜底硬约束：每个异常事件都必须有一条对应 AI 主动干预（含补齐的心率上升）
    existing_ai_related_ids = {
        str(e.get("related_event_id") or "")
        for e in events
        if e.get("event_type") == "AI主动干预"
    }
    missing_ai_added = False
    for event in events:
        if event.get("type") != "abnormal":
            continue
        abnormal_id = str(event.get("_id") or "")
        if not abnormal_id or abnormal_id in existing_ai_related_ids:
            continue
        event_code = event.get("code")
        event_info = event_mapping.get(event_code, {})
        base_detail = (
            event.get("detail")
            if isinstance(event.get("detail"), dict)
            else finalize_sleep_event_detail(event_code, event_info)
        )
        base_local_dt = session_anchor_event_local_dt(event, sleep_time, window_end)
        if base_local_dt == datetime.min:
            base_local_dt = sleep_time
        if event_code == "nightmare":
            ai_event_time = clamp_dt_to_sleep_window(
                base_local_dt + timedelta(minutes=nightmare_awake_offset_min),
                sleep_time,
                window_end,
            )
        else:
            ai_event_time = clamp_dt_to_sleep_window(
                base_local_dt + timedelta(minutes=random.uniform(2.0, 3.0)),
                sleep_time,
                window_end,
            )
        ai_detail = build_ai_intervention_detail_for_abnormal(
            event_code, event_info, base_detail, use_doubao=intervention_doubao
        )
        ai_event = {
            "uid": user_id,
            "record_date": record_date,
            "event_timestamp": format_sleep_event_local_timestamp(ai_event_time),
            "event_type": "AI主动干预",
            "type": "intervention",
            "code": event_code,
            "detail": ai_detail,
            "related_event_id": abnormal_id,
            "sort_order": 0,
            "create_time": generate_iso_date(),
            "update_time": generate_iso_date(),
            "duration_sec": _pick_sleep_event_duration_sec({"event_type": "AI主动干预"}),
            "_id": generate_object_id(),
        }
        events.append(ai_event)
        existing_ai_related_ids.add(abnormal_id)
        missing_ai_added = True
    if missing_ai_added:
        events = assign_duration_and_retime_sleep_events(
            events, sleep_time, window_end, retime=True
        )

    _repair_primary_snoring_out_of_awake_idf(
        events, idf_data, sleep_time, window_end, dims, profile
    )
    _repair_primary_heart_rate_out_of_rem_idf(
        events, idf_data, sleep_time, window_end
    )

    # AI主动干预事件：related_event_id 指向其对应异常主事件的 _id
    for i, event in enumerate(events):
        if event.get("event_type") == "AI主动干预":
            if not event.get("related_event_id"):
                event["related_event_id"] = events[i - 1]["_id"] if i > 0 else ""
        elif "related_event_id" not in event:
            event["related_event_id"] = ""

    # 更新sort_order
    for i, event in enumerate(events):
        event["sort_order"] = i
        event["language"] = "zh"
        event.pop("_nightmare_pair_key", None)
    
    return events

# 删除现有的睡眠事件数据文件
def delete_existing_sleep_events():
    """
    删除现有的睡眠事件数据文件
    """
    # 遍历output目录
    if os.path.exists('output'):
        for filename in os.listdir('output'):
            if filename.endswith('_sleep_events.json') or filename.endswith('_sleep_events_*.json'):
                filepath = os.path.join('output', filename)
                try:
                    os.remove(filepath)
                    print(f"已删除文件: {filepath}")
                except Exception as e:
                    print(f"删除文件 {filepath} 失败: {e}")

def _health_data_by_record_date(user_id, output_dir="output"):
    """若存在 {output_dir}/{uid}_health_data.json，返回 record_date -> 当日健康记录（含 raw_data）。"""
    fp = os.path.join(output_dir or "output", f"{user_id}_health_data.json")
    if not os.path.exists(fp):
        return {}
    try:
        with open(fp, "r", encoding="utf-8") as f:
            lst = json.load(f)
        return {
            item.get("record_date"): item
            for item in lst
            if item.get("record_date")
        }
    except Exception:
        return {}


# 为所有用户生成睡眠事件
def generate_events_for_all_users():
    """
    为配置文件中的每个用户在其指定的日期范围内生成睡眠事件
    """
    # 读取配置文件
    with open('config/config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    user_profiles = config.get('user_profiles', [])
    
    for user_profile in user_profiles:
        user_id = user_profile.get('user_id')
        personality_type = user_profile.get('personalInformation', {}).get('type', 'M-L-C')
        date_range = user_profile.get('date', {})
        start_date_str = date_range.get('start')
        end_date_str = date_range.get('end')
        
        if not user_id or not start_date_str or not end_date_str:
            print(f"用户 {user_id} 缺少必要信息，跳过")
            continue
        
        # 检查睡眠事件数据是否已生成
        output_file = f'output/{user_id}_sleep_events.json'
        if os.path.exists(output_file):
            print(f"用户 {user_id} 的睡眠事件数据已生成，跳过")
            continue
        
        # 解析日期范围
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
        
        # 收集该用户的所有睡眠事件
        all_events = []
        health_by_date = _health_data_by_record_date(user_id)
        
        # 生成日期范围内的每个日期的睡眠事件
        current_date = start_date
        while current_date <= end_date:
            record_date = current_date.strftime('%Y-%m-%d')
            
            real_day = health_by_date.get(record_date)
            if real_day and real_day.get("raw_data", {}).get("sleep_time") and real_day.get("raw_data", {}).get("wake_time"):
                sleep_data = real_day
            else:
                sleep_time_min = user_profile.get('sleepTime', {}).get('min', ['23:00'])[0]
                sleep_time_max = user_profile.get('sleepTime', {}).get('max', ['00:00'])[0]
                awake_time_min = user_profile.get('awakeTime', {}).get('min', ['07:00'])[0]
                awake_time_max = user_profile.get('awakeTime', {}).get('max', ['08:00'])[0]
                
                sleep_min = datetime.strptime(sleep_time_min, '%H:%M')
                sleep_max = datetime.strptime(sleep_time_max, '%H:%M')
                
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
                
                awake_min = datetime.strptime(awake_time_min, '%H:%M')
                awake_max = datetime.strptime(awake_time_max, '%H:%M')
                
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
                    wake_date = (current_date + timedelta(days=1)).strftime('%Y-%m-%d')
                else:
                    wake_date = record_date
                
                wake_dt_naive = datetime.strptime(
                    f"{wake_date} {awake_time_str}", "%Y-%m-%d %H:%M"
                )
                wake_up_dt = wake_dt_naive + timedelta(minutes=random.randint(12, 42))
                sleep_data = {
                    "raw_data": {
                        "sleep_time": f"{record_date}T{sleep_time_str}:00Z",
                        "wake_time": f"{wake_date}T{awake_time_str}:00Z",
                        "wake_up_time": wake_up_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "total_sleep_minutes": 480,
                    }
                }
            
            events = generate_sleep_events(
                sleep_data,
                user_id,
                record_date,
                None,
                personality_type,
            )
            
            # 添加到用户的事件列表
            all_events.extend(events)
            
            # 移动到下一天
            current_date += timedelta(days=1)
        
        # 按事件时间排序
        if all_events:

            def _sort_key_evt(ev):
                rd = ev.get("record_date")
                day = health_by_date.get(rd)
                st, we = (None, None)
                if day:
                    st, we = sleep_local_window_bounds_from_sleep_data(day)
                return (rd, session_anchor_event_local_dt(ev, st, we))

            all_events.sort(key=_sort_key_evt)
            
            # 按日期分组并更新sort_order，每个日期从0开始
            current_date = None
            order = 0
            for event in all_events:
                event_date = event['record_date']
                if event_date != current_date:
                    current_date = event_date
                    order = 0
                event['sort_order'] = order
                order += 1
            
            # 保存该用户的所有睡眠事件
            save_sleep_events_to_file(all_events, user_id)
            print(f"为用户 {user_id} 生成了 {len(all_events)} 个睡眠事件")
        else:
            # 即使没有事件，也创建一个空的睡眠事件文件
            save_sleep_events_to_file(all_events, user_id)
            print(f"为用户 {user_id} 未生成睡眠事件，但创建了空文件")


def _wrap_pipeline_step_health_skip_existing():
    """
    为 user_gen_pipeline 的 health 步骤增加「产物已存在则跳过」，
    与 HealthDataGenerator.generate_health_data 中对单文件的跳过逻辑一致。
    返回 (user_gen_pipeline 模块, 原始 health 步骤函数)，供调用方在 finally 中还原。
    """
    import user_gen_pipeline as ugp

    original = ugp.USER_DATA_PIPELINE_STEPS["health"]

    def _health_skip_if_json_exists(ctx):
        out = os.path.join(ctx.generator.output_dir, f"{ctx.user_id}_health_data.json")
        if os.path.isfile(out):
            print(f"用户 {ctx.user_id} 的健康数据已存在，跳过")
            return
        return original(ctx)

    ugp.USER_DATA_PIPELINE_STEPS["health"] = _health_skip_if_json_exists
    ugp.pipeline_step_health = _health_skip_if_json_exists
    return ugp, original


def _unwrap_pipeline_step_health(ugp, original):
    ugp.USER_DATA_PIPELINE_STEPS["health"] = original
    ugp.pipeline_step_health = original


def main(
    use_doubao=False,
    start_date_override=None,
    end_date_override=None,
    only_missing=False,
    pipeline_steps=None,
    only_user_ids=None,
):
    """主函数：通过 user_gen_pipeline 按阶段生成数据；pipeline_steps 为 None 表示全量。
    only_user_ids：仅处理这些 user_id（字符串列表），为 None 时处理配置中的全部用户。"""
    set_model_switch(use_doubao)
    # 以脚本方式运行时当前模块为 __main__，而 user_gen_pipeline._gh() 会 ``import generate_health_data``
    # 再得到一份独立模块对象；不同步则流水线内 USE_MODEL / sleepReportAI 等仍为默认 False。
    try:
        import generate_health_data as _loaded_gh
    except ImportError:
        _loaded_gh = None
    if _loaded_gh is not None and _loaded_gh is not sys.modules.get("__main__"):
        _loaded_gh.set_model_switch(use_doubao)
    print("开始生成健康数据...")
    print(f"模型调用开关: {'开启' if USE_MODEL else '关闭'}")
    
    # 创建健康数据生成器实例，设置generate_config=True以生成配置文件
    generator = HealthDataGenerator(generate_config=False)

    if only_user_ids:
        wanted = {str(x).strip() for x in only_user_ids if str(x).strip()}
        all_users = generator.users
        configured_ids = {str(u.get("user_id", "")).strip() for u in all_users}
        generator.users = [u for u in all_users if str(u.get("user_id", "")).strip() in wanted]
        missing = wanted - configured_ids
        if missing:
            print(f"[警告] 以下 user_id 在配置中不存在，已忽略：{sorted(missing)}")
        if not generator.users:
            print("没有可生成的用户（请核对 --user / --users 与配置文件中的 user_id）。")
            return
        print(f"仅生成 {len(generator.users)} 个指定用户（配置中共 {len(all_users)} 个用户）。")

    from user_gen_pipeline import (
        UserGenContext,
        run_user_data_pipeline,
        USER_DATA_PIPELINE_ORDER,
    )

    ugp_patched, ugp_orig_health = _wrap_pipeline_step_health_skip_existing()
    session_updates = {}
    try:

        def process_one_user(i, user):
            """
            单用户任务：生成 health/fitness/survey/quiz/schedule/ai/sleep_plan/environment/vitals/sleep_report/sleep_events 等。
            返回 (user_id, session_id) 供主线程统一写回 config。
            """
            user_id = user.get('user_id')
            print(f"\n=== 开始生成用户 {i+1}/{len(generator.users)} ({user_id}) 的数据 ===")

            # 获取用户配置的日期范围
            date_config = user.get('date', {})
            start_date_str = date_config.get('start', None)
            end_date_str = date_config.get('end', None)

            if not start_date_str or not end_date_str:
                print(f"用户 {user_id} 缺少日期配置，跳过")
                return user_id, ""

            # 解析日期范围，并支持命令行日期分片
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            if start_date_override:
                start_date = max(start_date, start_date_override)
            if end_date_override:
                end_date = min(end_date, end_date_override)
            if start_date > end_date:
                print(f"用户 {user_id} 在当前日期分片内无数据，跳过")
                return user_id, ""

            # 与分片后的 start/end 一致，供 environment 等按字符串区间的步骤使用
            start_date_str = start_date.strftime("%Y-%m-%d")
            end_date_str = end_date.strftime("%Y-%m-%d")

            user_preference = user.get('preference', None)
            user_profile = user.get('sleepProfile', '健康')
            session_id_local = ""

            # 仅补齐缺失文件模式：若主要产物都存在则跳过该用户
            if only_missing:
                required_outputs = [
                    f"{user_id}_health_data.json",
                    f"{user_id}_fitness_data.json",
                    f"{user_id}_schedule_data.json",
                    f"{user_id}_environment_data.json",
                    f"{user_id}_vitals_data.json",
                    f"{user_id}_sleep_report.json",
                    f"{user_id}_sleep_events.json",
                    # f"{user_id}_ai_analysis.json",  # 与下方暂停 ai_analysis 阶段一致，不参与 only-missing 判定
                ]
                all_exist = all(
                    os.path.exists(os.path.join(generator.output_dir, f)) for f in required_outputs
                )
                if all_exist:
                    print(f"用户 {user_id} 主要输出已存在（only-missing），跳过")
                    return user_id, ""

            ctx = UserGenContext(
                generator=generator,
                user=user,
                user_id=user_id,
                start_date=start_date,
                end_date=end_date,
                start_date_str=start_date_str,
                end_date_str=end_date_str,
                user_preference=user_preference,
                user_profile=user_profile,
                session_id_local=session_id_local,
                use_doubao=use_doubao,
            )
            # 暂停 ai_analysis 数据生成（不执行 pipeline 中的 ai_analysis 阶段）
            # run_user_data_pipeline(ctx, pipeline_steps)
            if pipeline_steps is None:
                _pipeline_steps = tuple(
                    s for s in USER_DATA_PIPELINE_ORDER if s != "ai_analysis"
                )
            else:
                _pipeline_steps = tuple(s for s in pipeline_steps if s != "ai_analysis")
            run_user_data_pipeline(ctx, _pipeline_steps)

            # 输出当前用户的数据
            print(f"\n=== 用户 {i+1}/{len(generator.users)} ({user_id}) 的数据生成完成 ====")
            print(f"\n用户 {user_id} 的数据已生成并输出：")

            # 列出生成的文件
            output_files = [
                f"{user_id}_health_data.json",
                f"{user_id}_fitness_data.json",
                f"{user_id}_schedule_data.json",
                f"{user_id}_environment_data.json",
                f"{user_id}_vitals_data.json",
                f"{user_id}_sleep_report.json",
                f"{user_id}_sleep_events.json",
            ]

            for file in output_files:
                file_path = os.path.join(generator.output_dir, file)
                if os.path.exists(file_path):
                    print(f"  - {file}")
                else:
                    print(f"  - {file} (未生成)")

            return user_id, session_id_local

        # 多线程并行生成每个用户的数据
        max_workers = int(os.getenv("GEN_WORKERS", "8"))
        max_workers = max(1, min(max_workers, 32))

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = []
            for i, user in enumerate(generator.users):
                futures.append(ex.submit(process_one_user, i, user))

            for fut in as_completed(futures):
                try:
                    uid, sid = fut.result()
                    if uid and sid:
                        session_updates[uid] = sid
                except Exception as e:
                    print(f"[错误] 并行生成用户数据任务失败: {e}")
    finally:
        _unwrap_pipeline_step_health(ugp_patched, ugp_orig_health)

    # 主线程统一写回 config/session_id
    if session_updates:
        try:
            with _CONFIG_WRITE_LOCK:
                with open(generator.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                for u in config.get('user_profiles', []):
                    uid = u.get('user_id')
                    if uid in session_updates:
                        u['session_id'] = session_updates[uid]
                with open(generator.config_file, 'w', encoding='utf-8') as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)
            print(f"\n已写回 {len(session_updates)} 个用户的 session_id 到配置文件。")
        except Exception as e:
            print(f"[警告] 写回 session_id 到配置文件失败: {e}")

    print("\n所有用户数据生成完成！")

if __name__ == "__main__":
    import argparse
    def _parse_bool_text(value):
        if isinstance(value, bool):
            return value
        txt = str(value).strip().lower()
        if txt in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if txt in {"0", "false", "f", "no", "n", "off"}:
            return False
        raise argparse.ArgumentTypeError("布尔参数仅支持 True/False（或 1/0）")

    parser = argparse.ArgumentParser(description='生成健康数据')
    parser.add_argument('--useModel', type=_parse_bool_text, default=None, help='是否调用模型，示例：--useModel True')
    parser.add_argument(
        '--doubao',
        action='store_true',
        help='兼容旧参数名，等价于 --useModel True（实际调用通义千问 DashScope）',
    )
    parser.add_argument('--workers', type=int, default=None, help='并行生成用户数据的线程数（默认8，也可用环境变量GEN_WORKERS）')
    parser.add_argument('--start', type=str, default=None, help='仅生成该日期及之后的数据，格式 YYYY-MM-DD')
    parser.add_argument('--end', type=str, default=None, help='仅生成该日期及之前的数据，格式 YYYY-MM-DD')
    parser.add_argument('--only-missing', action='store_true', help='仅补齐缺失产物，已存在的用户数据直接跳过')
    parser.add_argument(
        '--steps',
        type=str,
        default=None,
        help='逗号分隔，仅执行指定流水线阶段（见 user_gen_pipeline.USER_DATA_PIPELINE_ORDER），默认全量',
    )
    parser.add_argument(
        '--user',
        action='append',
        default=None,
        metavar='USER_ID',
        help='仅生成该 user_id；可重复多次指定多个用户',
    )
    parser.add_argument(
        '--users',
        type=str,
        default=None,
        help='逗号分隔的多个 user_id，与 --user 合并生效',
    )
    args = parser.parse_args()

    if args.workers is not None:
        os.environ["GEN_WORKERS"] = str(args.workers)

    start_dt = datetime.strptime(args.start, '%Y-%m-%d') if args.start else None
    end_dt = datetime.strptime(args.end, '%Y-%m-%d') if args.end else None

    from user_gen_pipeline import parse_steps_arg

    only_ids = []
    if args.user:
        only_ids.extend(args.user)
    if args.users:
        only_ids.extend(s.strip() for s in args.users.split(",") if s.strip())
    only_ids = list(dict.fromkeys(only_ids)) if only_ids else None
    use_model = args.useModel if args.useModel is not None else bool(args.doubao)

    # 单次主流程（避免重复全量生成）
    main(
        use_doubao=use_model,
        start_date_override=start_dt,
        end_date_override=end_dt,
        only_missing=args.only_missing,
        pipeline_steps=parse_steps_arg(args.steps),
        only_user_ids=only_ids,
    )