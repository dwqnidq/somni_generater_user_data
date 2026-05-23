"""LLM 批量生成断点续跑：按日期合并已有 output、增量落盘、配额耗尽可恢复。"""

from __future__ import annotations

import json
import os
import time
from typing import Callable, List, Optional, Set

from utils import atomic_write_json

from generate_ai.llm_client import LlmQuotaExhausted  # noqa: F401 — re-export for callers


def load_rows_by_date(
    path: str,
    *,
    date_key: str = "record_date",
) -> dict[str, dict]:
    """读取 JSON 数组输出，按 ``date_key`` 建索引（同日期保留最后一条）。"""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, list):
        return {}
    out: dict[str, dict] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        d = str(row.get(date_key) or "").strip()
        if d:
            out[d] = row
    return out


def row_has_llm_payload(row: dict, *, date_key: str = "record_date") -> bool:
    """判断一行是否已有可写回的 LLM 内容（非 uid / 日期键的空记录）。"""
    if not str(row.get(date_key) or "").strip():
        return False
    skip = frozenset({"uid", date_key})
    for key, val in row.items():
        if key in skip:
            continue
        if val is None or val == "":
            continue
        if isinstance(val, (list, dict)) and len(val) == 0:
            continue
        return True
    return False


def merge_rows_sorted(rows_by_date: dict[str, dict], *, date_key: str = "record_date") -> List[dict]:
    return [rows_by_date[d] for d in sorted(rows_by_date.keys())]


def bootstrap_resume(
    output_path: str,
    resume: bool,
    *,
    date_key: str = "record_date",
    is_complete: Optional[Callable[[dict], bool]] = None,
) -> tuple[List[dict], Set[str], dict[str, dict]]:
    """加载已有文件；返回 (结果列表, 已完成日期集合, 按日索引)。"""
    complete = is_complete or (lambda r: row_has_llm_payload(r, date_key=date_key))
    by_date = load_rows_by_date(output_path, date_key=date_key) if resume else {}
    done: Set[str] = {d for d, r in by_date.items() if complete(r)}
    return merge_rows_sorted(by_date, date_key=date_key), done, by_date


def checkpoint_save(output_path: str, results: List[dict]) -> None:
    if not output_path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    atomic_write_json(output_path, results, ensure_ascii=False, indent=2)


def upsert_row(
    by_date: dict[str, dict],
    row: dict,
    *,
    date_key: str = "record_date",
) -> List[dict]:
    d = str(row.get(date_key) or "").strip()
    if d:
        by_date[d] = row
    return merge_rows_sorted(by_date, date_key=date_key)


def run_llm_date_batch(
    *,
    uid: str,
    output_path: str,
    resume: bool,
    dates: List[str],
    process_date: Callable[[str], Optional[dict]],
    date_key: str = "record_date",
    retry_delay: float = 0.5,
    is_complete: Optional[Callable[[dict], bool]] = None,
    on_soft_fail: Optional[Callable[[str], None]] = None,
) -> List[dict]:
    """按日期批量调用 LLM；支持续跑与每成功一日增量写盘。

    ``process_date`` 返回 ``None`` 表示软失败（跳过该日，不中断流水线）。
    抛出 ``LlmQuotaExhausted`` 前会先保存当前进度。
    """
    results, done, by_date = bootstrap_resume(
        output_path, resume, date_key=date_key, is_complete=is_complete
    )
    if resume and done:
        print(f"  [续跑] 已从 {os.path.basename(output_path)} 加载 {len(done)} 个已完成日期")

    n = len(dates)
    for i, d in enumerate(dates):
        if d in done:
            print(f"  [{i + 1}/{n}] uid={uid} date={d} … 续跑跳过（已有记录）")
            continue
        print(f"  [{i + 1}/{n}] uid={uid} date={d} …", end=" ", flush=True)
        try:
            row = process_date(d)
        except LlmQuotaExhausted:
            checkpoint_save(output_path, results)
            print("配额/限流耗尽，已保存进度")
            raise
        if row is None:
            print("失败（已跳过）")
            if on_soft_fail:
                on_soft_fail(d)
        else:
            print("完成")
            results = upsert_row(by_date, row, date_key=date_key)
            done.add(d)
            checkpoint_save(output_path, results)
        if i < n - 1 and retry_delay > 0:
            time.sleep(retry_delay)
    return results
