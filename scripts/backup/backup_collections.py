"""指定集合备份脚本 - 使用 mongodump CLI 备份一个或多个 MongoDB 集合。

用法:
    python backup_collections.py --collections somni_events somni_reports
    python backup_collections.py --collections somni_events --gzip
    python backup_collections.py --collections somni_events somni_reports somni_records --out /path/to/dir

也可在其他脚本中导入:
    from backup_collections import backup_collections
    result = backup_collections(["somni_events", "somni_reports"])
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

ALL_COLLECTIONS = [
    "somni_reports",
    "somni_physiological_data",
    "somni_events",
    "somni_environment_data",
    "somni_records",
    "somni_schedules",
    "somni_ai_insights",
    "somni_dream_universe_assets",
    "somni_sleep_analysis",
    "somni_fusion",
    "somni_sleep_district",
]


def backup_collections(
    collections: list[str], out_dir: str = None, gzip: bool = False
) -> bool:
    """备份指定的一个或多个 MongoDB 集合。

    Args:
        collections: 要备份的集合名称列表
        out_dir: 输出目录，默认为桌面
        gzip: 是否压缩备份

    Returns:
        True 表示全部成功，False 表示有失败
    """
    if not shutil.which("mongodump"):
        print("错误: 未找到 mongodump 命令，请先安装 MongoDB Database Tools")
        print("安装方式: brew install mongodb-database-tools")
        return False

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = out_dir or os.path.join(DESKTOP_PATH, f"mongodump_collections_{timestamp}")

    print(f"开始备份 {len(collections)} 个集合...")
    print(f"URI: {MONGO_URI.split('@')[-1] if '@' in MONGO_URI else MONGO_URI}")
    print(f"集合: {', '.join(collections)}")
    print(f"输出目录: {base_dir}")
    print()

    all_success = True
    for coll in collections:
        coll_out = os.path.join(base_dir, coll)
        cmd = ["mongodump", "--uri", MONGO_URI, "--collection", coll, "--out", coll_out]
        if gzip:
            cmd.append("--gzip")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            print(f"  [OK] {coll}")
            if result.stdout.strip():
                print(f"       {result.stdout.strip()}")
        except subprocess.CalledProcessError as e:
            print(f"  [FAIL] {coll}: {e.stderr.strip()}")
            all_success = False

    print()
    if all_success:
        print(f"全部备份完成! 文件保存在: {base_dir}")
    else:
        print(f"部分集合备份失败，请检查上方日志。文件保存在: {base_dir}")
    return all_success


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="使用 mongodump 备份指定的 MongoDB 集合")
    parser.add_argument(
        "--collections",
        nargs="+",
        required=True,
        choices=ALL_COLLECTIONS,
        help="要备份的集合名称（可指定多个）",
    )
    parser.add_argument("--out", type=str, default=None, help="输出目录（默认桌面）")
    parser.add_argument("--gzip", action="store_true", help="压缩备份文件")
    args = parser.parse_args()

    success = backup_collections(
        collections=args.collections, out_dir=args.out, gzip=args.gzip
    )
    sys.exit(0 if success else 1)
