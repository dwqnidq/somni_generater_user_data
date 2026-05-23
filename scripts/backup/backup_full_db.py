"""全库备份脚本 - 使用 mongodump CLI 备份整个 MongoDB 数据库。

用法:
    python backup_full_db.py                    # 备份全库到桌面
    python backup_full_db.py --out /path/to/dir # 指定输出目录
    python backup_full_db.py --gzip             # 压缩备份

也可在其他脚本中导入:
    from backup_full_db import backup_full_database
    result = backup_full_database()
"""

import subprocess
import os
import sys
import shutil
from datetime import datetime
from dotenv import load_dotenv

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

MONGO_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017/Fullive")
DESKTOP_PATH = os.path.join(os.path.expanduser("~"), "Desktop")


def backup_full_database(out_dir: str = None, gzip: bool = False) -> bool:
    """备份整个 MongoDB 数据库。

    Args:
        out_dir: 输出目录，默认为桌面
        gzip: 是否压缩备份

    Returns:
        True 表示成功，False 表示失败
    """
    if not shutil.which("mongodump"):
        print("错误: 未找到 mongodump 命令，请先安装 MongoDB Database Tools")
        print("安装方式: brew install mongodb-database-tools")
        return False

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = out_dir or os.path.join(DESKTOP_PATH, f"mongodump_full_{timestamp}")

    cmd = ["mongodump", "--uri", MONGO_URI, "--out", backup_dir]
    if gzip:
        cmd.append("--gzip")

    print(f"开始全库备份...")
    print(f"URI: {MONGO_URI.split('@')[-1] if '@' in MONGO_URI else MONGO_URI}")
    print(f"输出目录: {backup_dir}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print(result.stdout)
        print(f"全库备份完成! 文件保存在: {backup_dir}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"备份失败: {e.stderr}")
        return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="使用 mongodump 备份整个 MongoDB 数据库")
    parser.add_argument("--out", type=str, default=None, help="输出目录（默认桌面）")
    parser.add_argument("--gzip", action="store_true", help="压缩备份文件")
    args = parser.parse_args()

    success = backup_full_database(out_dir=args.out, gzip=args.gzip)
    sys.exit(0 if success else 1)
