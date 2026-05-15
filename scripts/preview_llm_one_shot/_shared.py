"""预览脚本共用：工程根目录、加载 health 行、启用大模型（豆包 / DashScope）开关。"""

from __future__ import annotations

import argparse
import json
import os
import sys

# 本目录下预览脚本统一使用的大模型采样参数（与批量生成 pipeline 区分）
PREVIEW_LLM_TEMPERATURE = 0.7
PREVIEW_LLM_TOP_P = 0.5

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
GEN_DIR = os.path.join(PROJECT_ROOT, "scripts", "generate_data")
GEN_AI_DIR = os.path.join(GEN_DIR, "generate_ai")


def bootstrap():
    for p in (PROJECT_ROOT, GEN_DIR, GEN_AI_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.chdir(PROJECT_ROOT)



def force_doubao_env() -> None:
    """加载 .env 并固定 ``LLM_VENDOR=doubao``（火山方舟 + MODEL_NAME）。"""
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    except ImportError:
        pass
    from generate_ai.runtime import apply_doubao_env

    apply_doubao_env(strict=True)


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


def load_sleep_report_main_title(user_id: str, record_date: str, output_dir: str) -> str:
    """从 ``{PROJECT_ROOT}/{output_dir}/{user_id}_sleep_report.json`` 读取指定日的 ``main.title``。"""
    path = os.path.join(PROJECT_ROOT, output_dir, f"{user_id}_sleep_report.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"未找到睡眠报告文件: {path}（主摘要标签须从该文件对应日期的 main.title 读取）"
        )
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path} 应为 JSON 数组")
    for item in data:
        if not isinstance(item, dict):
            continue
        if str(item.get("record_date") or "").strip() != record_date:
            continue
        main = item.get("main")
        if isinstance(main, dict):
            t = str(main.get("title") or "").strip()
            if t:
                return t
        raise ValueError(
            f"{path} 中 record_date={record_date} 的条目缺少有效的 main.title"
        )
    raise ValueError(f"{path} 中未找到 record_date={record_date} 的睡眠报告条目")


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
