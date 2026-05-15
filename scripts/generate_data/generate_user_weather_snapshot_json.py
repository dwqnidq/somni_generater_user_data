"""为每个用户生成一个天气结构 JSON 文件。

字段结构对齐 output/123.json：
  uv_index
  humidity
  pressure
  precipitation_intensity
  temperature
  temperature_max
  temperature_min

默认输出到 output/{user_id}_weather.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
from typing import Any


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "health_data_personas_config.json")
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")


def _build_record(uid: str) -> dict[str, float]:
    """按 uid 生成可复现的单条天气结构数据。"""
    rng = random.Random(uid)

    temperature_min = round(rng.uniform(18.0, 28.0), 2)
    temperature_max = round(max(temperature_min + 1.0, rng.uniform(26.0, 36.0)), 2)
    temperature = round(rng.uniform(temperature_min, temperature_max), 2)

    return {
        "uv_index": round(rng.uniform(0.0, 11.0), 2),
        "humidity": round(rng.uniform(25.0, 90.0), 2),
        "pressure": round(rng.uniform(995.0, 1025.0), 2),
        "precipitation_intensity": round(rng.uniform(0.0, 5.0), 2),
        "temperature": temperature,
        "temperature_max": temperature_max,
        "temperature_min": temperature_min,
    }


def _load_personas(config_path: str) -> list[dict[str, Any]]:
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    personas = data.get("personas")
    if not isinstance(personas, list):
        raise ValueError("配置文件缺少 personas 数组")
    return personas


def generate_weather_snapshots_for_personas(
    personas: list[dict[str, Any]],
    output_dir: str,
    *,
    overwrite: bool,
) -> None:
    os.makedirs(output_dir, exist_ok=True)
    generated = 0
    skipped = 0
    for persona in personas:
        uid = persona.get("user_id")
        if not uid:
            continue

        out_path = os.path.join(output_dir, f"{uid}_weather.json")
        if not overwrite and os.path.exists(out_path):
            skipped += 1
            print(f"[跳过] {uid} 已存在：{out_path}")
            continue

        record = _build_record(uid)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=4)
            f.write("\n")
        generated += 1
        print(f"[生成] {uid} -> {out_path}")

    print(f"\n完成：生成 {generated} 个文件，跳过 {skipped} 个文件。")


def generate_files(config_path: str, output_dir: str, overwrite: bool) -> None:
    personas = _load_personas(config_path)
    generate_weather_snapshots_for_personas(personas, output_dir, overwrite=overwrite)


def main() -> None:
    parser = argparse.ArgumentParser(description="为每个用户生成天气结构 JSON 文件")
    parser.add_argument("--config", default=CONFIG_PATH, help="人格配置文件路径")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在文件")
    args = parser.parse_args()

    generate_files(
        config_path=args.config,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
