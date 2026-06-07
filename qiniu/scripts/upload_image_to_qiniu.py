"""文件作用：用于 upload image to qiniu 相关的数据处理或流程支持。"""

import os
import json
import time
import random
import string
from dotenv import load_dotenv
import qiniu

load_dotenv()

QINIU_ACCESS_KEY = os.getenv('QINIU_ACCESS_KEY')
QINIU_SECRET_KEY = os.getenv('QINIU_SECRET_KEY')
QINIU_BUCKET = os.getenv('QINIU_BUCKET')
QINIU_DOMAIN = os.getenv('QINIU_DOMAIN')
IMAGE_URL = os.getenv('IMAGE_URL')

IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.svg']

def generate_random_string(length=11):
    letters_and_digits = string.ascii_letters + string.digits
    return ''.join(random.choice(letters_and_digits) for _ in range(length))

def upload_image(local_file, key):
    """上传单张图片到七牛云，返回访问URL"""
    try:
        q = qiniu.Auth(QINIU_ACCESS_KEY, QINIU_SECRET_KEY)
        token = q.upload_token(QINIU_BUCKET, key)
        ret, info = qiniu.put_file(token, key, local_file)
        if ret is not None:
            url = f"{QINIU_DOMAIN}/{key}"
            print(f"上传成功: {url}")
            return url
        else:
            print(f"上传失败: {info}")
            return None
    except Exception as e:
        print(f"上传出错: {e}")
        return None

def get_image_files(folder):
    """递归获取文件夹中所有图片文件"""
    images = []
    for root, _, files in os.walk(folder):
        for file in files:
            if os.path.splitext(file)[1].lower() in IMAGE_EXTENSIONS:
                images.append(os.path.join(root, file))
    return images

def main():
    if not all([QINIU_ACCESS_KEY, QINIU_SECRET_KEY, QINIU_BUCKET, QINIU_DOMAIN]):
        print("请在 .env 文件中配置七牛云相关信息")
        return

    if not IMAGE_URL:
        print("请在 .env 文件中配置 IMAGE_URL 字段")
        return

    if not os.path.isdir(IMAGE_URL):
        print(f"IMAGE_URL 目录不存在: {IMAGE_URL}")
        return

    files = get_image_files(IMAGE_URL)
    if not files:
        print(f"目录中没有图片文件: {IMAGE_URL}")
        return

    print(f"找到 {len(files)} 张图片，开始上传...")

    results = []
    for file in files:
        ext = os.path.splitext(file)[1].lower()
        key = f"somni/icon/{int(time.time())}_{generate_random_string()}{ext}"
        print(f"正在上传: {file}")
        url = upload_image(file, key)
        if url:
            results.append({"file": os.path.basename(file), "url": url})

    # 输出到 json 文件
    output_file = os.path.join(os.path.dirname(__file__), 'uploaded_images.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n完成，成功上传 {len(results)}/{len(files)} 张图片")
    print(f"结果已保存到: {output_file}")

if __name__ == "__main__":
    main()
