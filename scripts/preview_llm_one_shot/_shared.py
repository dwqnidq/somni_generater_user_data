"""预览脚本共用：工程根目录、加载 health 行、启用大模型（通义千问 / DashScope）开关。"""

from __future__ import annotations

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
GEN_DIR = os.path.join(PROJECT_ROOT, "scripts", "generate_data")


def bootstrap():
    for p in (PROJECT_ROOT, GEN_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.chdir(PROJECT_ROOT)


def apply_translate_qwen_env_defaults() -> None:
    """将 TRANSLATE_* 透传为 QWEN_*，供预览脚本统一使用。"""
    translate_base_url = os.getenv("TRANSLATE_BASE_URL", "").strip()
    translate_model_name = os.getenv("TRANSLATE_MODEL_NAME", "").strip()
    translate_enable_thinking = os.getenv("TRANSLATE_ENABLE_THINKING", "").strip()

    if translate_base_url and not os.getenv("QWEN_BASE_URL"):
        os.environ["QWEN_BASE_URL"] = translate_base_url
    if translate_model_name and not os.getenv("QWEN_MODEL_NAME"):
        os.environ["QWEN_MODEL_NAME"] = translate_model_name
    if translate_enable_thinking and not os.getenv("QWEN_ENABLE_THINKING"):
        os.environ["QWEN_ENABLE_THINKING"] = translate_enable_thinking


def base_arg_parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--user-id", required=True, help="与 output 下文件名前缀一致")
    p.add_argument(
        "--record-date",
        default="",
        help="YYYY-MM-DD；省略则取该用户 health 中第一条",
    )
    p.add_argument(
        "--output-dir",
        default="output",
        help="相对工程根目录，默认 output",
    )
    p.add_argument(
        "--out",
        default="",
        help="写入的 JSON 路径（相对当前工作目录）；省略则写入 ./preview_llm_output/<默认名>",
    )
    return p


def default_out_path(name: str) -> str:
    """默认写入当前工作目录下的 preview_llm_output/。"""
    sub = os.path.join(os.getcwd(), "preview_llm_output")
    os.makedirs(sub, exist_ok=True)
    return os.path.join(sub, f"{name}.json")


def load_health_row(user_id: str, record_date: str, output_dir: str) -> tuple[dict, str]:
    path = os.path.join(PROJECT_ROOT, output_dir, f"{user_id}_health_data.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path} 无有效记录")
    if record_date:
        for r in rows:
            if isinstance(r, dict) and str(r.get("record_date")) == record_date:
                return r, record_date
        raise ValueError(f"未找到 record_date={record_date} 的 health 行")
    r0 = next(x for x in rows if isinstance(x, dict) and x.get("record_date"))
    rd = str(r0.get("record_date"))
    return r0, rd


def load_config_profile(user_id: str) -> dict:
    cfg_path = os.path.join(PROJECT_ROOT, "config", "config.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    for u in config.get("user_profiles") or []:
        if u.get("user_id") == user_id:
            return u
    return {}


def write_result(out_path: str, data: object) -> str:
    """out_path 为绝对路径，或与当前工作目录相对的相对路径。"""
    path = os.path.abspath(out_path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path
