"""预览脚本共用：工程根目录、加载 health 行、启用大模型（豆包 / DashScope）开关。"""

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



def force_doubao_env() -> None:
    """将 .env 中的豆包配置（BASE_URL / DOUBAO_API_KEY / MODEL_NAME）强制映射到
    QWEN_BASE_URL / DASHSCOPE_API_KEY / QWEN_MODEL_NAME，供各预览脚本统一调用豆包模型。"""
    base_url = (os.getenv("BASE_URL") or "").strip()
    api_key = (os.getenv("DOUBAO_API_KEY") or "").strip()
    model_name = (os.getenv("MODEL_NAME") or "").strip()
    missing = [k for k, v in [("BASE_URL", base_url), ("DOUBAO_API_KEY", api_key), ("MODEL_NAME", model_name)] if not v]
    if missing:
        raise SystemExit("豆包配置缺失，请在 .env 中配置: " + ", ".join(missing))
    os.environ["QWEN_BASE_URL"] = base_url
    os.environ["QWEN_MODEL_NAME"] = model_name
    os.environ["DASHSCOPE_API_KEY"] = api_key


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
