"""文件作用：用于 upload audio to qiniu 相关的数据处理或流程支持。"""

import os
import json
import time
import random
import string
import sys
from dotenv import load_dotenv
import qiniu
from pydub import AudioSegment

# 加载环境变量
load_dotenv()

# 七牛云配置
QINIU_ACCESS_KEY = os.getenv('QINIU_ACCESS_KEY')
QINIU_SECRET_KEY = os.getenv('QINIU_SECRET_KEY')
QINIU_BUCKET = os.getenv('QINIU_BUCKET')
QINIU_ZONE = os.getenv('QINIU_ZONE')
QINIU_DOMAIN = os.getenv('QINIU_DOMAIN')
AUDIO_URL = os.getenv('AUDIO_URL')

# 音频文件扩展名
AUDIO_EXTENSIONS = ['.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a']

# 视频文件扩展名
VIDEO_EXTENSIONS = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.m4v', '.3gp']

def generate_random_string(length=11):
    """生成指定长度的随机字符串"""
    letters_and_digits = string.ascii_letters + string.digits
    return ''.join(random.choice(letters_and_digits) for _ in range(length))

def get_zone(zone_str):
    """根据字符串获取七牛云区域对象"""
    try:
        from qiniu import Zone
        zone_map = {
            'z0': Zone.z0,  # 华东
            'z1': Zone.z1,  # 华北
            'z2': Zone.z2,  # 华南
            'na0': Zone.na0,  # 北美
            'as0': Zone.as0  # 亚太
        }
        return zone_map.get(zone_str, Zone.z0)
    except ImportError:
        # 兼容旧版本SDK
        zone_map = {
            'z0': 'z0',  # 华东
            'z1': 'z1',  # 华北
            'z2': 'z2',  # 华南
            'na0': 'na0',  # 北美
            'as0': 'as0'  # 亚太
        }
        return zone_map.get(zone_str, 'z0')

def convert_to_wav(input_file):
    """将音频文件转换为wav格式"""
    try:
        # 加载音频文件
        audio = AudioSegment.from_file(input_file)
        
        # 生成临时wav文件路径
        temp_wav = os.path.splitext(input_file)[0] + "_temp.wav"
        
        # 导出为wav格式
        audio.export(temp_wav, format="wav")
        
        return temp_wav
    except Exception as e:
        print(f"转换失败: {e}")
        return None

def upload_file_to_qiniu(local_file, key):
    """上传文件到七牛云，视频文件先提取音频再上传，超过10分钟的音频只保留前10分钟"""
    ext = os.path.splitext(local_file)[1].lower()
    is_video = ext in VIDEO_EXTENSIONS

    if is_video:
        print(f"检测到视频文件，正在提取音频: {os.path.basename(local_file)}")
        wav_file = convert_video_to_audio(local_file)
        if not wav_file:
            return None
        temp_file = wav_file
    else:
        wav_file = convert_to_wav(local_file)
        if not wav_file:
            return None
        temp_file = wav_file

    # 超过10分钟则截取前10分钟
    MAX_DURATION_MS = 10 * 60 * 1000
    try:
        audio = AudioSegment.from_file(temp_file)
        if len(audio) > MAX_DURATION_MS:
            print(f"音频时长 {len(audio) / 1000:.1f}s 超过10分钟，截取前10分钟")
            audio = audio[:MAX_DURATION_MS]
            audio.export(temp_file, format="wav")
    except Exception as e:
        print(f"截取音频时出错: {e}")
        if os.path.exists(temp_file):
            os.remove(temp_file)
        return None

    # 确保key的扩展名是.wav
    key = os.path.splitext(key)[0] + ".wav"
    
    try:
        # 初始化七牛云客户端
        q = qiniu.Auth(QINIU_ACCESS_KEY, QINIU_SECRET_KEY)
        
        # 生成上传令牌
        token = q.upload_token(QINIU_BUCKET, key)
        
        # 上传文件
        ret, info = qiniu.put_file(token, key, temp_file)
        
        # 删除临时文件
        if os.path.exists(temp_file):
            os.remove(temp_file)
        
        if ret is not None:
            # 生成访问URL
            return f"{QINIU_DOMAIN}/{key}"
        else:
            print(f"上传失败: {info}")
            return None
    except Exception as e:
        print(f"上传过程出错: {e}")
        # 删除临时文件
        if os.path.exists(temp_file):
            os.remove(temp_file)
        return None

def convert_video_to_audio(video_file):
    """将视频文件提取音频并转为wav格式"""
    try:
        audio = AudioSegment.from_file(video_file)
        temp_wav = os.path.splitext(video_file)[0] + "_video_extracted.wav"
        audio.export(temp_wav, format="wav")
        print(f"视频转音频成功: {os.path.basename(video_file)} -> {os.path.basename(temp_wav)}")
        return temp_wav
    except Exception as e:
        print(f"视频转音频失败 {video_file}: {e}")
        return None

def get_audio_files(folder):
    """获取文件夹中的所有音频和视频文件"""
    media_files = []
    for root, dirs, files in os.walk(folder):
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in AUDIO_EXTENSIONS or ext in VIDEO_EXTENSIONS:
                media_files.append(os.path.join(root, file))
    return media_files

def get_audio_duration(audio_file):
    """获取音频文件时长（秒）"""
    try:
        audio = AudioSegment.from_file(audio_file)
        return int(audio.duration_seconds)
    except Exception as e:
        print(f"获取音频时长失败: {e}")
        return 0

def update_audio_json(audio_data, source_file_names, target_array):
    """更新audio.json文件"""
    # 检查项目根目录和qiniu文件夹中的audio.json文件
    root_audio_json = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'audio.json')
    qiniu_audio_json = os.path.join(os.path.dirname(__file__), 'audio.json')
    
    # 优先使用项目根目录下的audio.json
    if os.path.exists(root_audio_json):
        audio_json_path = root_audio_json
    else:
        audio_json_path = qiniu_audio_json
    
    try:
        with open(audio_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {"audioUrl": [], "sleepTalkingUrl": [], "coughUrl": [], "alreadyUploadedFileName": []}
    
    # 确保目标数组存在
    if target_array not in data:
        data[target_array] = []
    
    # 确保alreadyUploadedFileName字段存在
    if "alreadyUploadedFileName" not in data:
        data["alreadyUploadedFileName"] = []
    
    # 添加新的音频数据
    data[target_array].extend(audio_data)
    
    # 添加源文件名称
    for file_name in source_file_names:
        if file_name not in data["alreadyUploadedFileName"]:
            data["alreadyUploadedFileName"].append(file_name)
    
    # 写回文件
    with open(audio_json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def main():
    """主函数"""
    # 解析命令行参数
    if len(sys.argv) != 2:
        print("用法: python upload_audio_to_qiniu.py <target_array>")
        print("其中 target_array 可以是: audioUrl, sleepTalkingUrl, coughUrl")
        return
    
    target_array = sys.argv[1]
    # 验证目标数组名称
    valid_arrays = ['audioUrl', 'sleepTalkingUrl', 'coughUrl']
    if target_array not in valid_arrays:
        print(f"无效的目标数组名称: {target_array}")
        print(f"有效的数组名称: {', '.join(valid_arrays)}")
        return
    
    if not all([QINIU_ACCESS_KEY, QINIU_SECRET_KEY, QINIU_BUCKET, QINIU_DOMAIN, AUDIO_URL]):
        print("请在.env文件中配置七牛云相关信息和音频文件夹路径")
        return
    
    if not os.path.exists(AUDIO_URL):
        print(f"音频文件夹不存在: {AUDIO_URL}")
        return
    
    # 检查项目根目录和qiniu文件夹中的audio.json文件
    root_audio_json = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'audio.json')
    qiniu_audio_json = os.path.join(os.path.dirname(__file__), 'audio.json')
    
    # 优先使用项目根目录下的audio.json
    if os.path.exists(root_audio_json):
        audio_json_path = root_audio_json
    else:
        audio_json_path = qiniu_audio_json
    
    # 读取已上传的文件名
    uploaded_files = []
    try:
        with open(audio_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        uploaded_files = data.get('alreadyUploadedFileName', [])
    except FileNotFoundError:
        pass
    
    # 获取音频文件列表
    audio_files = get_audio_files(AUDIO_URL)
    if not audio_files:
        print(f"文件夹中没有音频或视频文件: {AUDIO_URL}")
        return
    
    # 过滤已上传的文件
    filtered_files = []
    for audio_file in audio_files:
        file_name = os.path.basename(audio_file)
        if file_name not in uploaded_files:
            filtered_files.append(audio_file)
        else:
            print(f"文件已上传，跳过: {file_name}")
    
    if not filtered_files:
        print("所有文件都已上传过，无需重复上传")
        return
    
    print(f"找到 {len(filtered_files)} 个未上传的音频/视频文件")
    
    # 上传文件并收集数据
    uploaded_data = []
    source_file_names = []
    
    for audio_file in filtered_files:
        # 生成新的文件名
        timestamp = int(time.time())
        random_str = generate_random_string()
        ext = os.path.splitext(audio_file)[1]
        new_filename = f"{timestamp}_{random_str}{ext}"
        
        # 上传文件
        print(f"正在上传: {audio_file}")
        audio_url = upload_file_to_qiniu(audio_file, new_filename)
        
        if audio_url:
            # 获取音频实际上传时长（最多10分钟）
            raw_duration = get_audio_duration(audio_file)
            duration = min(raw_duration, 600)
            
            # 构建音频数据对象
            audio_data = {
                "url": audio_url,
                "duration_sec": duration
            }
            
            uploaded_data.append(audio_data)
            source_file_names.append(os.path.basename(audio_file))
            print(f"上传成功: {audio_url}, 时长: {duration}秒")
        else:
            print(f"上传失败: {audio_file}")
    
    # 更新audio.json文件
    if uploaded_data:
        update_audio_json(uploaded_data, source_file_names, target_array)
        print(f"已更新audio.json文件，添加了 {len(uploaded_data)} 个音频数据到 {target_array} 数组")
    else:
        print("没有成功上传的音频文件")

if __name__ == "__main__":
    main()
