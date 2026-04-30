"""文件作用：用于 download qiniu files 相关的数据处理或流程支持。"""

import os
import json
import requests
from urllib.parse import urlparse

# json文件路径
JSON_FILE = os.path.join(os.path.dirname(__file__), 'downloadAudioUrl.json')

# 下载目录
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), 'downloads')


def download_file(url, save_dir):
    """下载单个文件到指定目录"""
    try:
        filename = os.path.basename(urlparse(url).path)
        if not filename:
            filename = url.split('/')[-1]

        save_path = os.path.join(save_dir, filename)

        # 如果文件已存在则跳过
        if os.path.exists(save_path):
            print(f"已存在，跳过: {filename}")
            return True

        print(f"正在下载: {filename}")
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()

        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        size_kb = os.path.getsize(save_path) / 1024
        print(f"下载完成: {filename} ({size_kb:.1f} KB)")
        return True

    except Exception as e:
        print(f"下载失败 {url}: {e}")
        return False


def main():
    # 读取url列表
    with open(JSON_FILE, 'r', encoding='utf-8') as f:
        urls = json.load(f)

    if not urls:
        print("downloadAudioUrl.json 中没有地址")
        return

    # 创建下载目录
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    print(f"共 {len(urls)} 个文件，保存到: {DOWNLOAD_DIR}\n")

    success, failed = 0, 0
    for url in urls:
        if download_file(url, DOWNLOAD_DIR):
            success += 1
        else:
            failed += 1

    print(f"\n完成: 成功 {success} 个，失败 {failed} 个")


if __name__ == "__main__":
    main()
