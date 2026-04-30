
"""
为8种睡眠人格 × 4个第四维编码(U/S/W/M) 生成32条人格配置文档
输出到 output/all_personality_profiles.json
"""
# 文件作用：用于 generate personality profiles 相关的数据处理或流程支持。

import json
import os
import sys
import random
from datetime import datetime
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

load_dotenv()

MONGO_URI = os.getenv('MONGODB_URI', 'mongodb://Fullive:LZTSwLbBxtYBZ64h@192.144.200.251:27017/Fullive')
DB_NAME = 'Fullive'
OUTPUT_FILE = 'output/all_personality_profiles.json'

# 第四维编码含义
FOURTH_DIM_LABELS = {
    'U': 'Unwind',   # 放松解压型
    'S': 'Sensory',  # 感官沉浸型
    'W': 'Wake',     # 自然唤醒型
    'M': 'Melody',   # 旋律型
}

# 每种第四维对应的 category_code 偏好（用于选音频）
FOURTH_DIM_CATEGORIES = {
    'U': [12, 14, 19],       # 白噪音、冥想、治愈音乐
    'S': [5, 7, 8, 16, 17],  # 环境氛围音
    'W': [20, 21, 26, 6],    # 自然音、鸟鸣、海浪
    'M': [18, 23, 25, 19],   # 轻音乐、爵士、古风
}

# 唤醒阶段固定用自然音
WAKE_CATEGORIES = [20, 21, 6, 26]

# 8种人格基础信息
PERSONALITY_BASE = {
    'M-H-R': {
        'mhr_name': '完美主义百灵鸟',
        'identity_name': '完美主义者',
        'status_analysis': '作息规律但入睡困难，高敏感+高活跃导致睡前思绪纷飞。',
        'title': '你的人格是完美主义百灵鸟',
        'description': '作息规律，对睡眠环境要求苛刻，睡前思绪活跃，需要完整的放松仪式才能入睡。',
        'comment_template': '节律管理型睡眠人格，自律度高但心理压力容易影响睡眠稳定性。建议建立固定的睡前放松仪式，减少睡前思维活跃度。',
        'indicator_descriptions': {
            'circadian': '作息规律，生物钟稳定。',
            'sensitivity': '对光线、声音、温度敏感，环境要求高。',
            'brain_state': '睡前思绪活跃，容易过度思考。',
            'atmosphere': '需要完整的睡前仪式才能入睡。',
        },
        'chronotype': 'M', 'sensitivity': 'H', 'brain': 'R',
        'light_style': 'warm_dim',
    },
    'M-H-C': {
        'mhr_name': '敏感的晨间鹿',
        'identity_name': '敏感守护者',
        'status_analysis': '早睡型，大脑容易安静，但感官高度敏感，夜间易被惊醒。',
        'title': '你的人格是敏感的晨间鹿',
        'description': '早睡型，入睡相对顺利，但对环境极度敏感，轻微扰动即可惊醒，睡眠依赖极致安静的环境。',
        'comment_template': '高敏感警觉型睡眠人格，睡眠质量高度依赖环境稳定性。建议打造极致安静的睡眠空间，减少夜间环境干扰。',
        'indicator_descriptions': {
            'circadian': '早睡早起，作息偏早。',
            'sensitivity': '感官极度敏感，对微小变化有强烈反应。',
            'brain_state': '大脑容易安静，思绪平稳。',
            'atmosphere': '需要极致安静、安全的环境才能放松。',
        },
        'chronotype': 'M', 'sensitivity': 'H', 'brain': 'C',
        'light_style': 'warm_soft',
    },
    'M-L-R': {
        'mhr_name': '效率至上考拉',
        'identity_name': '效率达人',
        'status_analysis': '作息规律，环境适应力强，入睡慢但睡得好。',
        'title': '你的人格是效率至上考拉',
        'description': '白天精力充沛，大脑到睡前仍高速运转，入睡需要一定时间，但一旦入睡质量极高。',
        'comment_template': '入睡慢但睡眠质量优秀的人格类型。建议睡前给大脑一段"降速"时间，通过轻度放松活动帮助思维切换到休息模式。',
        'indicator_descriptions': {
            'circadian': '作息规律，生物钟与自然节律高度匹配。',
            'sensitivity': '环境适应力强，不受外界干扰。',
            'brain_state': '睡前大脑仍活跃，需要时间降速。',
            'atmosphere': '简单环境即可，无需复杂助眠手段。',
        },
        'chronotype': 'M', 'sensitivity': 'L', 'brain': 'R',
        'light_style': 'warm_bright',
    },
    'M-L-C': {
        'mhr_name': '阳光漫步者',
        'identity_name': '享乐家',
        'status_analysis': '睡眠质量最优，倒头就睡，一觉到天亮。',
        'title': '你的人格是阳光漫步者',
        'description': '睡眠赢家，入睡极快，深睡比例高，几乎不受外界干扰，只需简单仪式即可自然入睡。',
        'comment_template': '睡眠节律稳定，入睡条件简单，是典型的"睡眠赢家"。只需轻微的仪式感即可顺利进入睡眠状态，整体睡眠适应力良好。',
        'indicator_descriptions': {
            'circadian': '作息稳定，睡意自然到来。',
            'sensitivity': '环境影响小，易入睡。',
            'brain_state': '放松迅速，思绪安静。',
            'atmosphere': '简单仪式即可入睡。',
        },
        'chronotype': 'M', 'sensitivity': 'L', 'brain': 'C',
        'light_style': 'warm_bright',
    },
    'E-H-R': {
        'mhr_name': '深夜灵感守望者',
        'identity_name': '创造者',
        'status_analysis': '夜间大脑极度活跃且感官高度敏感，入睡极其困难，睡眠质量最差。',
        'title': '你的人格是深夜灵感守望者',
        'description': '越夜越精神，思绪翻涌，对环境每个细节都有感知，入睡困难，深睡严重不足。',
        'comment_template': '高敏感夜间活跃型人格，长期处于睡眠负债状态。建议建立严格的睡前断网仪式，通过感官降噪帮助大脑从创作模式切换到休息模式。',
        'indicator_descriptions': {
            'circadian': '作息偏晚，越夜越精神。',
            'sensitivity': '感官高度敏感，对环境变化极为敏锐。',
            'brain_state': '夜间大脑持续高速运转，灵感活跃。',
            'atmosphere': '需要严格的感官隔离才能逐渐放松。',
        },
        'chronotype': 'E', 'sensitivity': 'H', 'brain': 'R',
        'light_style': 'cool_dim',
    },
    'E-H-C': {
        'mhr_name': '深海独奏家',
        'identity_name': '内省者',
        'status_analysis': '作息偏晚，大脑虽能安静，但感官极度敏感，对睡眠环境要求极高。',
        'title': '你的人格是深海独奏家',
        'description': '性格内向，习惯独处，对环境极为挑剔，需要完全黑暗、安静、温度适宜才能入睡。',
        'comment_template': '高敏感内省型睡眠人格，睡眠质量高度依赖环境的完美程度。建议打造专属的感官隔离睡眠空间，减少一切可能的环境干扰。',
        'indicator_descriptions': {
            'circadian': '作息偏晚，夜晚是独处的黄金时间。',
            'sensitivity': '感官极度敏感，对光、声、温度极为挑剔。',
            'brain_state': '大脑容易安静，但情绪容易陷入反复思考。',
            'atmosphere': '需要完全黑暗、安静、温度适宜的完美环境。',
        },
        'chronotype': 'E', 'sensitivity': 'H', 'brain': 'C',
        'light_style': 'cool_dim',
    },
    'E-L-R': {
        'mhr_name': '创意夜猫子',
        'identity_name': '自由探索者',
        'status_analysis': '典型夜猫子，越夜越精神，但对环境不挑剔，入睡后睡眠质量尚可。',
        'title': '你的人格是创意夜猫子',
        'description': '天生适应夜晚节奏，大脑在夜间持续活跃，入睡需要一定时间，但低敏感让其不受环境干扰。',
        'comment_template': '夜间活跃低敏感型人格，睡眠整体稳定。顺应自身生物节律，通过简单的睡前放松帮助大脑从活跃状态平稳过渡到睡眠。',
        'indicator_descriptions': {
            'circadian': '作息偏晚，夜晚是最活跃的时间段。',
            'sensitivity': '环境适应力强，不挑剔睡眠条件。',
            'brain_state': '夜间思维活跃，大脑需要时间降速。',
            'atmosphere': '随性自然，无需复杂助眠手段。',
        },
        'chronotype': 'E', 'sensitivity': 'L', 'brain': 'R',
        'light_style': 'cool_bright',
    },
    'E-L-C': {
        'mhr_name': '月光冲浪者',
        'identity_name': '享乐家',
        'status_analysis': '熬夜纯属自愿，睡得晚但质量极高。',
        'title': '你的人格是月光冲浪者',
        'description': '享受深夜，必须在完美的暗环境中才能入睡。因为睡得晚，早晨被强行唤醒时会极度痛苦。',
        'comment_template': '夜间效率型睡眠人格，主动选择晚睡但睡眠质量极高。只需简单的入睡仪式即可快速进入深度睡眠，整体睡眠适应力强。',
        'indicator_descriptions': {
            'circadian': '作息偏晚，深夜精力最集中。',
            'sensitivity': '环境影响小，适应力强。',
            'brain_state': '决定睡觉后大脑迅速切换到休息模式。',
            'atmosphere': '简单暗环境即可快速入睡。',
        },
        'chronotype': 'E', 'sensitivity': 'L', 'brain': 'C',
        'light_style': 'cool_bright',
    },
}


# 人格编码到第四维的映射（每种人格对应哪个第四维）
PERSONALITY_FOURTH_DIM = {
    'M-H-R': 'U',  # 完美主义百灵鸟 → Unwind
    'M-H-C': 'M',  # 敏感的晨间鹿 → Melody
    'M-L-R': 'S',  # 效率至上考拉 → Sensory
    'M-L-C': 'M',  # 阳光漫步者 → Melody
    'E-H-R': 'U',  # 深夜灵感守望者 → Unwind
    'E-H-C': 'S',  # 深海独奏家 → Sensory
    'E-L-R': 'W',  # 创意夜猫子 → Wake
    'E-L-C': 'W',  # 月光冲浪者 → Wake
}

# 分享配置模板
SHARE_CONFIG_TEMPLATES = {
    'M-H-R': {
        'share_title_template': '我是「完美主义百灵鸟」，你是哪种睡眠人格？',
        'share_desc_template': '完成 Somni 睡眠测评，发现你的专属助眠方案',
        'share_image_url': '',
        'share_summary_template': '完美主义百灵鸟：节律管理型，自律但易受压力影响',
    },
    'M-H-C': {
        'share_title_template': '我是「敏感的晨间鹿」，你是哪种睡眠人格？',
        'share_desc_template': '完成 Somni 睡眠测评，发现你的专属助眠方案',
        'share_image_url': '',
        'share_summary_template': '敏感的晨间鹿：高敏感警觉型，睡眠依赖环境稳定',
    },
    'M-L-R': {
        'share_title_template': '我是「效率至上考拉」，你是哪种睡眠人格？',
        'share_desc_template': '完成 Somni 睡眠测评，发现你的专属助眠方案',
        'share_image_url': '',
        'share_summary_template': '效率至上考拉：入睡慢但睡得好，健康高效型',
    },
    'M-L-C': {
        'share_title_template': '我是「阳光漫步者」，你是哪种睡眠人格？',
        'share_desc_template': '完成 Somni 睡眠测评，发现你的专属助眠方案',
        'share_image_url': '',
        'share_summary_template': '阳光漫步者：享乐家型，天生好睡基因',
    },
    'E-H-R': {
        'share_title_template': '我是「深夜灵感守望者」，你是哪种睡眠人格？',
        'share_desc_template': '完成 Somni 睡眠测评，发现你的专属助眠方案',
        'share_image_url': '',
        'share_summary_template': '深夜灵感守望者：创造者型，灵感与睡眠的永恒博弈',
    },
    'E-H-C': {
        'share_title_template': '我是「深海独奏家」，你是哪种睡眠人格？',
        'share_desc_template': '完成 Somni 睡眠测评，发现你的专属助眠方案',
        'share_image_url': '',
        'share_summary_template': '深海独奏家：内省者型，独处与敏感的深夜守护者',
    },
    'E-L-R': {
        'share_title_template': '我是「创意夜猫子」，你是哪种睡眠人格？',
        'share_desc_template': '完成 Somni 睡眠测评，发现你的专属助眠方案',
        'share_image_url': '',
        'share_summary_template': '创意夜猫子：自由探索型，随性夜间活跃者',
    },
    'E-L-C': {
        'share_title_template': '我是「月光冲浪者」，你是哪种睡眠人格？',
        'share_desc_template': '完成 Somni 睡眠测评，发现你的专属助眠方案',
        'share_image_url': '',
        'share_summary_template': '月光冲浪者：享乐家型，天生好睡基因',
    },
}

def _classify_color(r, g, b):
    """根据 RGB 判断色调描述"""
    if r == 0 and g == 0 and b == 0:
        return '关闭'
    if r > 200 and g > 180 and b < 100:
        return '暖黄色'
    if r > 200 and g > 100 and b < 80:
        return '暖橙色'
    if r > 180 and g < 100 and b < 80:
        return '暖红色'
    if r < 100 and g < 120 and b > 150:
        return '冷蓝色'
    if r < 120 and g < 80 and b > 120:
        return '冷紫色'
    if r < 120 and g > 150 and b > 180:
        return '冷青色'
    if r > 180 and g > 200 and b > 150:
        return '暖白色'
    return '混合色'


def _light_description(mode, color_stops, lux):
    """根据灯光配置生成 description"""
    if mode == 'off':
        return '关灯，深度睡眠守护'
    tones = [_classify_color(c['r'], c['g'], c['b']) for c in color_stops]
    unique_tones = list(dict.fromkeys(tones))  # 去重保序
    tone_str = '、'.join(unique_tones)
    mode_str = '渐变' if mode == 'gradient' else '常亮'
    return f'{mode_str}{tone_str}，亮度 {lux}%'


def get_light_config(light_style, phase):
    """根据人格灯光风格和阶段生成灯光配置"""
    palettes = {
        'warm_dim': {
            'relax':      [{'r':255,'g':147,'b':41},  {'r':255,'g':100,'b':50},  {'r':220,'g':80,'b':30}],
            'fall_asleep':[{'r':180,'g':60,'b':20},   {'r':120,'g':40,'b':10}],
            'guard':      [{'r':0,  'g':0,  'b':0}],
            'wake':       [{'r':255,'g':200,'b':100},  {'r':255,'g':220,'b':150}],
        },
        'warm_soft': {
            'relax':      [{'r':255,'g':180,'b':120},  {'r':240,'g':160,'b':100},  {'r':220,'g':140,'b':80}],
            'fall_asleep':[{'r':160,'g':80,'b':40},    {'r':100,'g':50,'b':20}],
            'guard':      [{'r':0,  'g':0,  'b':0}],
            'wake':       [{'r':255,'g':210,'b':130},  {'r':255,'g':230,'b':170}],
        },
        'warm_bright': {
            'relax':      [{'r':255,'g':200,'b':80},   {'r':255,'g':170,'b':50},  {'r':240,'g':130,'b':30}],
            'fall_asleep':[{'r':200,'g':100,'b':30},   {'r':140,'g':60,'b':10}],
            'guard':      [{'r':0,  'g':0,  'b':0}],
            'wake':       [{'r':255,'g':230,'b':120},  {'r':255,'g':245,'b':180}],
        },
        'cool_dim': {
            'relax':      [{'r':60, 'g':80, 'b':180},  {'r':80, 'g':60, 'b':160},  {'r':100,'g':40,'b':140}],
            'fall_asleep':[{'r':30, 'g':30, 'b':100},  {'r':20, 'g':20, 'b':80}],
            'guard':      [{'r':0,  'g':0,  'b':0}],
            'wake':       [{'r':100,'g':180,'b':220},  {'r':150,'g':210,'b':240}],
        },
        'cool_bright': {
            'relax':      [{'r':50, 'g':150,'b':220},  {'r':80, 'g':100,'b':200},  {'r':120,'g':60,'b':180}],
            'fall_asleep':[{'r':30, 'g':60, 'b':150},  {'r':20, 'g':40, 'b':120}],
            'guard':      [{'r':0,  'g':0,  'b':0}],
            'wake':       [{'r':80, 'g':200,'b':230},  {'r':140,'g':220,'b':245}],
        },
    }

    palette = palettes.get(light_style, palettes['warm_bright'])
    colors = palette.get(phase, [{'r':0,'g':0,'b':0}])

    if phase == 'guard':
        return {
            'mode': 'off',
            'enabled': True,
            'color_stops': [dict(c, ww=0, cw=0, lux=0, enabled=True) for c in colors],
            'transition_durations': [],
            'description': '关灯，深度睡眠守护',
        }

    lux = 30 if 'dim' in light_style else 70
    color_stops = [dict(c, ww=0, cw=0, lux=lux, enabled=True) for c in colors]
    n = len(color_stops)
    durations = [20] * (n - 1) if n > 1 else []
    mode = 'gradient' if n > 1 else 'steady'

    return {
        'mode': mode,
        'enabled': True,
        'color_stops': color_stops,
        'transition_durations': durations,
        'description': _light_description(mode, colors, lux),
    }


def pick_audio(audio_pool, categories, used_ids, count=1):
    """从指定分类中随机选取音频，避免重复"""
    candidates = [a for a in audio_pool if int(a['category_code']) in categories and str(a['_id']) not in used_ids]
    if not candidates:
        candidates = [a for a in audio_pool if str(a['_id']) not in used_ids]
    if not candidates:
        candidates = audio_pool
    chosen = random.sample(candidates, min(count, len(candidates)))
    for a in chosen:
        used_ids.add(str(a['_id']))
    return chosen


def make_tracks(audio_list):
    """将音频文档转为 tracks 格式，并生成 sound description"""
    tracks = []
    for a in audio_list:
        tracks.append({
            'material_id': str(a['_id']),
            'name': a['name'],
            'type': '',
            'volume': random.choice([60, 65, 70]),
            'enabled': True,
        })
    # description 取第一条音频名称的前8字，加"氛围音效"
    if tracks:
        first_name = tracks[0]['name']
        short = first_name[:8] if len(first_name) > 8 else first_name
        desc = short if len(tracks) == 1 else f"{short}等{len(tracks)}种音效"
    else:
        desc = '助眠音效'
    return tracks, desc


def make_scent(phase):
    """根据阶段生成香氛配置"""
    scent_type = 'wake' if phase == 'wake' else 'relax'
    release = 1 if phase == 'fall_asleep' else (2 if phase == 'relax' else 5)
    interval = 5 if phase in ('relax', 'fall_asleep') else (10 if phase == 'wake' else 30)
    enabled = phase != 'guard'

    scent_names = {
        'relax': ['薰衣草香氛', '迷迭香香氛', '檀香香氛', '茉莉香氛'],
        'wake':  ['柠檬香氛', '佛手柑香氛', '薄荷香氛', '橙花香氛'],
    }
    desc = random.choice(scent_names.get(scent_type, ['助眠香氛'])) if enabled else '香氛关闭'

    return {
        'mode': 'continuous',
        'enabled': enabled,
        'slots': [{
            'name': '仓位1',
            'type': scent_type,
            'release': release,
            'interval': interval,
            'cycle_mode': 'continuous',
            'enabled': enabled,
        }],
        'description': desc,
    }


def build_scheme(name, phase, light_style, audio_pool, categories, used_ids):
    """构建单个 scheme"""
    audio_count = 2 if phase in ('relax', 'wake') else 1
    audios = pick_audio(audio_pool, categories, used_ids, count=audio_count)
    tracks, sound_desc = make_tracks(audios)
    light_cfg = get_light_config(light_style, phase)
    scent_cfg = make_scent(phase)
    return {
        'name': name,
        'light': light_cfg,
        'sound': {
            'mode': 'overlay',
            'enabled': phase != 'guard',
            'tracks': tracks,
            'description': sound_desc if phase != 'guard' else '声音关闭',
        },
        'scent': scent_cfg,
    }


PHASE_CONFIGS = [
    {'phase': 'relax',       'phase_name': '放松', 'duration_sec': 1800,  'transition_sec': 30},
    {'phase': 'fall_asleep', 'phase_name': '入睡', 'duration_sec': 2700,  'transition_sec': 35},
    {'phase': 'guard',       'phase_name': '守护', 'duration_sec': 21600, 'transition_sec': 10},
    {'phase': 'wake',        'phase_name': '唤醒', 'duration_sec': 1800,  'transition_sec': 0},
]

PHASE_DESCRIPTIONS = {
    'relax':       '舒缓灯光 + 放松音效 + 助眠香氛',
    'fall_asleep': '暗暖色灯光 + 助眠音效 + 舒缓香氛',
    'guard':       '关灯守护，深度睡眠阶段',
    'wake':        '自然光渐亮 + 自然唤醒音效 + 提神香氛',
}

def build_period(phase_cfg, light_style, fourth_dim, audio_pool, used_ids, scenes_map):
    """构建单个 period，scenes 来自 quiz_personalities 对应 phase 的 scenes 字段"""
    phase = phase_cfg['phase']
    # 唤醒阶段固定用自然音类别
    if phase == 'wake':
        categories = WAKE_CATEGORIES
    else:
        categories = FOURTH_DIM_CATEGORIES.get(fourth_dim, [12, 14])

    scheme_name = {
        'relax': '放松专属',
        'fall_asleep': '入睡专属',
        'guard': '默认方案',
        'wake': '唤醒方案',
    }.get(phase, '默认方案')

    scheme = build_scheme(scheme_name, phase, light_style, audio_pool, categories, used_ids)

    # 从 scenes_map 中取对应 phase 的 scenes
    # fall_asleep 和 guard 数据库无数据，复用 relax 的 scenes
    scenes = scenes_map.get(phase) or scenes_map.get('relax', [])

    return {
        'scenes': scenes,
        'schemes': [scheme],
        'phase': phase,
        'phase_name': phase_cfg['phase_name'],
        'description': PHASE_DESCRIPTIONS.get(phase, ''),
        'duration_sec': phase_cfg['duration_sec'],
        'transition_sec': phase_cfg['transition_sec'],
    }


def generate_profile(personality_code, fourth_dim, audio_pool, scenes_map):
    """生成单条人格配置文档"""
    base = PERSONALITY_BASE[personality_code]
    mhr_codes = personality_code.split('-') + [fourth_dim]
    light_style = base['light_style']

    used_ids = set()
    periods = [
        build_period(pc, light_style, fourth_dim, audio_pool, used_ids, scenes_map)
        for pc in PHASE_CONFIGS
    ]

    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')

    return {
        '_id': str(ObjectId()),
        'mhr_codes': mhr_codes,
        'mhr_name': base['mhr_name'],
        'identity_name': base['identity_name'],
        'status_analysis': base['status_analysis'],
        'title': base['title'],
        'description': base['description'],
        'comment_template': base['comment_template'],
        'character_3d_url': '',
        'population_ratio': 0,
        'indicator_descriptions': base['indicator_descriptions'],
        'periods': periods,
        'share_config': SHARE_CONFIG_TEMPLATES[personality_code],
        'language': 'zh',
        'sort_order': 0,
        'status': 1,
        'create_time': now,
        'update_time': now,
    }


def main():
    # 连接数据库
    print('连接数据库，获取音频素材和人格场景数据...')
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=8000)
    db = client[DB_NAME]

    audio_pool = list(db['audio_materials'].find(
        {'status': 1},
        {'_id': 1, 'name': 1, 'category_code': 1}
    ))
    print(f'获取到 {len(audio_pool)} 条音频素材')

    # 查询 quiz_personalities，只取 mhr_codes 长度为3的文档
    qp_docs = list(db['quiz_personalities'].find(
        {'$expr': {'$eq': [{'$size': '$mhr_codes'}, 3]}},
        {'mhr_codes': 1, 'periods': 1}
    ))
    client.close()
    print(f'获取到 {len(qp_docs)} 条三位编码人格数据')

    # 构建 { 'M-H-R': { 'relax': [...scenes], 'wake': [...scenes], ... } } 的映射
    qp_scenes = {}
    for doc in qp_docs:
        key = '-'.join(doc['mhr_codes'])
        phase_scenes = {}
        for period in doc.get('periods', []):
            phase_scenes[period['phase']] = period.get('scenes', [])
        qp_scenes[key] = phase_scenes

    all_profiles = []
    fourth_dims = ['U', 'S', 'W', 'M']

    for personality_code in PERSONALITY_BASE:
        scenes_map = qp_scenes.get(personality_code, {})
        if not scenes_map:
            print(f'  警告: 未找到 {personality_code} 的场景数据，scenes 将为空')
        for fourth_dim in fourth_dims:
            profile = generate_profile(personality_code, fourth_dim, audio_pool, scenes_map)
            all_profiles.append(profile)
            print(f'  生成 {personality_code}-{fourth_dim}  {PERSONALITY_BASE[personality_code]["mhr_name"]}')

    os.makedirs('output', exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_profiles, f, ensure_ascii=False, indent=2)

    print(f'\n完成，共生成 {len(all_profiles)} 条，已保存到 {OUTPUT_FILE}')


if __name__ == '__main__':
    main()
