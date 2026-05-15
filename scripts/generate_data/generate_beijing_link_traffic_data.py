"""
为每个用户生成一条“北京路段 Link 实时状态”数据。

输出文件：
  output/{uid}_traffic_link_realtime.json

数据结构（最小核心字段）：
{
  "linkId": "L_10086",
  "speed": 42.5,
  "status": 2,
  "timestamp": 1715241600,
  "geometry": {
    "type": "LineString",
    "coordinates": [[116.397, 39.908], [116.405, 39.915]]
  }
}
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "health_data_personas_config.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")

# 北京市内可复用路段（示例坐标）
BEIJING_LINKS = [
    ("L_BJ_0001", [[116.397, 39.908], [116.405, 39.915]]),  # 东城区
    ("L_BJ_0002", [[116.320, 39.983], [116.332, 39.989]]),  # 中关村附近
    ("L_BJ_0003", [[116.468, 39.914], [116.482, 39.920]]),  # 国贸附近
    ("L_BJ_0004", [[116.520, 39.901], [116.539, 39.905]]),  # 通州方向
    ("L_BJ_0005", [[116.360, 39.940], [116.373, 39.948]]),  # 北二环附近
]


def _status_to_speed(status: int) -> float:
    """按拥堵等级生成速度（km/h）。"""
    if status == 1:  # 畅通
        return round(random.uniform(45.0, 72.0), 1)
    if status == 2:  # 缓行
        return round(random.uniform(25.0, 45.0), 1)
    if status == 3:  # 拥堵
        return round(random.uniform(8.0, 25.0), 1)
    # 严重拥堵
    return round(random.uniform(0.0, 8.0), 1)


def _build_link_record(uid: str, ts: int) -> dict:
    """为指定用户构造一条 Link 实时路况记录。"""
    seed = int(uid[-6:], 16) ^ ts
    rng = random.Random(seed)

    link_id, coordinates = rng.choice(BEIJING_LINKS)
    # 状态概率：不含畅通；缓行较少，拥堵与严重拥堵为主（整体偏拥挤）
    status = rng.choices([2, 3, 4], weights=[2, 5, 5], k=1)[0]

    # 使用全局 random 统一浮点生成方式，再用 seed 还原可复现性
    random.seed(seed + 11)
    speed = _status_to_speed(status)

    return {
        "linkId": link_id,
        "speed": speed,
        "status": status,
        "timestamp": ts,
        "geometry": {
            "type": "LineString",
            "coordinates": coordinates,
        },
    }


def generate_traffic_link_realtime_for_personas(
    personas: list[dict],
    output_dir: str,
    *,
    ts: int | None = None,
    overwrite: bool = False,
) -> int:
    """为 personas 各写一条 Link 实时路况；返回本次使用的 Unix 时间戳。"""
    if ts is None:
        ts = int(time.time())
    os.makedirs(output_dir, exist_ok=True)

    for persona in personas:
        uid = persona.get("user_id")
        if not uid:
            continue
        out_path = os.path.join(output_dir, f"{uid}_traffic_link_realtime.json")
        if (not overwrite) and os.path.exists(out_path):
            print(f"[{uid}] 文件已存在，跳过（使用 --overwrite 覆盖）")
            continue

        record = _build_link_record(str(uid), ts)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        print(f"[{uid}] 已生成 → {out_path}")

    print(f"\n完成时间：{datetime.fromtimestamp(ts).isoformat()}（timestamp={ts}）")
    return ts


def main() -> None:
    parser = argparse.ArgumentParser(description="生成北京 Link 实时路况 JSON")
    parser.add_argument("--user-id", default=None, help="仅生成指定用户")
    parser.add_argument("--timestamp", type=int, default=None, help="指定 Unix 时间戳")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已存在文件",
    )
    args = parser.parse_args()

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    personas = config.get("personas", [])

    if args.user_id:
        personas = [p for p in personas if p.get("user_id") == args.user_id]
        if not personas:
            raise SystemExit(f"未找到 user_id={args.user_id}")

    generate_traffic_link_realtime_for_personas(
        personas,
        OUTPUT_DIR,
        ts=args.timestamp,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
