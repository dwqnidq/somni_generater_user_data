"""文件作用：用于 delete qiniu files 相关的数据处理或流程支持。"""

import os
import sys
import json
from dotenv import load_dotenv
import qiniu

# 加载环境变量
load_dotenv()

# 七牛云配置
QINIU_ACCESS_KEY = os.getenv('QINIU_ACCESS_KEY')
QINIU_SECRET_KEY = os.getenv('QINIU_SECRET_KEY')
QINIU_BUCKET = os.getenv('QINIU_BUCKET')
QINIU_DOMAIN = os.getenv('QINIU_DOMAIN')

def extract_key_from_url(url):
    """从七牛云URL中提取文件key"""
    if QINIU_DOMAIN in url:
        # 从URL中提取key
        key = url.replace(QINIU_DOMAIN + '/', '')
        # 处理URL编码的字符
        import urllib.parse
        return urllib.parse.unquote(key)
    else:
        print(f"URL不属于配置的七牛云域名: {url}")
        return None

def delete_single_file(key):
    """删除单个文件"""
    try:
        # 初始化七牛云客户端
        q = qiniu.Auth(QINIU_ACCESS_KEY, QINIU_SECRET_KEY)
        bucket_manager = qiniu.BucketManager(q)
        
        # 删除文件
        ret, info = bucket_manager.delete(QINIU_BUCKET, key)
        
        if ret is None:
            print(f"删除成功: {key}")
            return True
        else:
            print(f"删除失败: {info}")
            return False
    except Exception as e:
        print(f"删除过程出错: {e}")
        return False

def delete_files_from_urls(urls):
    """从URL列表中删除文件"""
    success_count = 0
    fail_count = 0
    successfully_deleted = []
    
    for url in urls:
        key = extract_key_from_url(url)
        if key:
            if delete_single_file(key):
                success_count += 1
                successfully_deleted.append(url)
            else:
                fail_count += 1
        else:
            fail_count += 1
    
    print(f"\n删除完成: 成功 {success_count} 个, 失败 {fail_count} 个")
    return successfully_deleted

def delete_files_from_file(file_path):
    """从文件中读取URL并删除"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip()]
        
        if urls:
            print(f"从文件中读取到 {len(urls)} 个URL")
            successfully_deleted = delete_files_from_urls(urls)
            
            # 更新audio.json文件
            if successfully_deleted:
                # 检查项目根目录和qiniu文件夹中的audio.json文件
                root_audio_json = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'audio.json')
                qiniu_audio_json = os.path.join(os.path.dirname(__file__), 'audio.json')
                
                # 优先使用项目根目录下的audio.json
                if os.path.exists(root_audio_json):
                    audio_json_path = root_audio_json
                elif os.path.exists(qiniu_audio_json):
                    audio_json_path = qiniu_audio_json
                else:
                    print("audio.json文件不存在，无法更新")
                    return
                
                try:
                    with open(audio_json_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    
                    # 过滤掉已删除的音频数据
                    audio_urls = data.get('audioUrl', [])
                    updated_audio_urls = []
                    for item in audio_urls:
                        if isinstance(item, dict):
                            if 'url' in item and item['url'] not in successfully_deleted:
                                updated_audio_urls.append(item)
                        elif isinstance(item, str):
                            if item not in successfully_deleted:
                                updated_audio_urls.append(item)
                    
                    # 清空或更新alreadyUploadedFileName数组
                    # 由于audioUrl和alreadyUploadedFileName是一对一关系，我们也清空这个数组
                    # 注意：这是基于它们是一一对应的假设
                    data['audioUrl'] = updated_audio_urls
                    data['alreadyUploadedFileName'] = []
                    
                    with open(audio_json_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    print("已更新audio.json文件，移除了已删除的音频数据")
                except Exception as e:
                    print(f"更新audio.json出错: {e}")
        else:
            print("文件中没有URL")
    except FileNotFoundError:
        print(f"文件不存在: {file_path}")
    except Exception as e:
        print(f"读取文件出错: {e}")

def main():
    """主函数"""
    if not all([QINIU_ACCESS_KEY, QINIU_SECRET_KEY, QINIU_BUCKET, QINIU_DOMAIN]):
        print("请在.env文件中配置七牛云相关信息")
        return
    
    # 命令行参数处理
    if len(sys.argv) < 2:
        print("使用方法:")
        print("  单个删除: python delete_qiniu_files.py <url>")
        print("  批量删除: python delete_qiniu_files.py --file <file_path>")
        print("  从audio.json删除: python delete_qiniu_files.py --audio-json")
        return
    
    if sys.argv[1] == "--file":
        if len(sys.argv) < 3:
            print("请提供文件路径")
            return
        file_path = sys.argv[2]
        delete_files_from_file(file_path)
    elif sys.argv[1] == "--audio-json":
        # 从audio.json文件中读取URL并删除
        # 检查项目根目录下的audio.json文件
        root_audio_json = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'audio.json')
        qiniu_audio_json = os.path.join(os.path.dirname(__file__), 'audio.json')
        
        # 优先使用项目根目录下的audio.json
        if os.path.exists(root_audio_json):
            audio_json_path = root_audio_json
        elif os.path.exists(qiniu_audio_json):
            audio_json_path = qiniu_audio_json
        else:
            print("audio.json文件不存在")
            return
        
        try:
            with open(audio_json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 处理audioUrl数组，可能是对象数组或字符串数组
            audio_urls = data.get('audioUrl', [])
            urls = []
            
            for item in audio_urls:
                if isinstance(item, dict):
                    # 如果是对象，提取url字段
                    if 'url' in item:
                        urls.append(item['url'])
                elif isinstance(item, str):
                    # 如果是字符串，直接使用
                    urls.append(item)
            
            if urls:
                print(f"从audio.json中读取到 {len(urls)} 个URL")
                # 执行删除并获取成功删除的URL
                successfully_deleted = delete_files_from_urls(urls)
                
                # 更新audio.json文件
                if successfully_deleted:
                    # 过滤掉已删除的音频数据
                    updated_audio_urls = []
                    for item in audio_urls:
                        if isinstance(item, dict):
                            if 'url' in item and item['url'] not in successfully_deleted:
                                updated_audio_urls.append(item)
                        elif isinstance(item, str):
                            if item not in successfully_deleted:
                                updated_audio_urls.append(item)
                    
                    # 清空或更新alreadyUploadedFileName数组
                    # 由于audioUrl和alreadyUploadedFileName是一对一关系，我们也清空这个数组
                    # 注意：这是基于它们是一一对应的假设
                    data['audioUrl'] = updated_audio_urls
                    data['alreadyUploadedFileName'] = []
                    
                    with open(audio_json_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    print("已更新audio.json文件，移除了已删除的音频数据")
                else:
                    print("没有成功删除的文件")
            else:
                print("audio.json中没有URL")
        except Exception as e:
            print(f"读取audio.json出错: {e}")
    else:
        # 单个URL删除
        url = sys.argv[1]
        successfully_deleted = delete_files_from_urls([url])
        
        # 更新audio.json文件
        if successfully_deleted:
            # 检查项目根目录和qiniu文件夹中的audio.json文件
            root_audio_json = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'audio.json')
            qiniu_audio_json = os.path.join(os.path.dirname(__file__), 'audio.json')
            
            # 优先使用项目根目录下的audio.json
            if os.path.exists(root_audio_json):
                audio_json_path = root_audio_json
            elif os.path.exists(qiniu_audio_json):
                audio_json_path = qiniu_audio_json
            else:
                print("audio.json文件不存在，无法更新")
                return
            
            try:
                with open(audio_json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 过滤掉已删除的音频数据
                audio_urls = data.get('audioUrl', [])
                updated_audio_urls = []
                for item in audio_urls:
                    if isinstance(item, dict):
                        if 'url' in item and item['url'] not in successfully_deleted:
                            updated_audio_urls.append(item)
                    elif isinstance(item, str):
                        if item not in successfully_deleted:
                            updated_audio_urls.append(item)
                
                # 清空或更新alreadyUploadedFileName数组
                # 由于audioUrl和alreadyUploadedFileName是一对一关系，我们也清空这个数组
                # 注意：这是基于它们是一一对应的假设
                data['audioUrl'] = updated_audio_urls
                data['alreadyUploadedFileName'] = []
                
                with open(audio_json_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print("已更新audio.json文件，移除了已删除的音频数据")
            except Exception as e:
                print(f"更新audio.json出错: {e}")

if __name__ == "__main__":
    main()
