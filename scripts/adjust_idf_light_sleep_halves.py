"""
将 output 目录下 *health_data.json 中每条记录的 idf_data 调整为：
前半夜（入睡时间轴的前半段）浅睡总分钟数 < 后半夜浅睡总分钟数。

前/后半夜以 idf_data 整条时间轴 [首段 start, 末段 end) 的中点划分（单调分钟轴，支持跨午夜）。
调整方式：在分钟级时间轴上，优先「前半夜 light → deep」与「后半夜 deep/rem → light」成对交换；
不足时再单边把后半夜 deep/rem 改为 light，或把前半夜 light 改为 deep。不修改 awake 段。
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "output"


def _hhmm_to_minute(hhmm: Any) -> Optional[int]:
    if not hhmm or not isinstance(hhmm, str):
        return None
    parts = hhmm.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        h = int(parts[0])
        m = int(parts[1])
        return h * 60 + m
    except (TypeError, ValueError):
        return None


def _minute_to_hhmm(total_min: int) -> str:
    mm = int(total_min) % 1440
    return f"{mm // 60:02d}:{mm % 60:02d}"


def idf_to_monotonic_ranges(idf_data: List[Dict[str, Any]]) -> List[Tuple[str, int, int]]:
    ranges: List[Tuple[str, int, int]] = []
    prev_end: Optional[int] = None
    for seg in idf_data or []:
        if not isinstance(seg, dict):
            continue
        stg = str(seg.get("stage") or "light")
        s_raw = _hhmm_to_minute(seg.get("start"))
        e_raw = _hhmm_to_minute(seg.get("end"))
        if s_raw is None or e_raw is None:
            continue
        s = s_raw
        if prev_end is not None:
            while s < prev_end:
                s += 1440
        e = e_raw
        while e <= s:
            e += 1440
        ranges.append((stg, s, e))
        prev_end = e
    return ranges


def ranges_to_timeline(ranges: List[Tuple[str, int, int]]) -> Tuple[int, List[str]]:
    if not ranges:
        return 0, []
    first_min = ranges[0][1]
    last_min = ranges[-1][2]
    total = max(1, last_min - first_min)
    base = ranges[0][0] if ranges[0][0] else "light"
    timeline = [base] * total
    for stg, s, e in ranges:
        a = max(0, s - first_min)
        b = min(total, e - first_min)
        if a < b:
            timeline[a:b] = [stg] * (b - a)
    return first_min, timeline


def timeline_to_idf(first_min: int, timeline: List[str]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    i = 0
    n = len(timeline)
    while i < n:
        stg = timeline[i]
        j = i + 1
        while j < n and timeline[j] == stg:
            j += 1
        out.append(
            {
                "stage": stg,
                "start": _minute_to_hhmm(first_min + i),
                "end": _minute_to_hhmm(first_min + j),
            }
        )
        i = j
    return out


def count_light_halves(
    timeline: List[str], split_idx: int
) -> Tuple[int, int]:
    """split_idx：属于后半夜的第一个下标（与 [0,split) 前半夜、[split,len) 后半夜）。"""
    first = sum(1 for k in range(0, min(split_idx, len(timeline))) if timeline[k] == "light")
    second = sum(1 for k in range(split_idx, len(timeline)) if timeline[k] == "light")
    return first, second


def adjust_timeline_light_halves(timeline: List[str], split_idx: int) -> Tuple[List[str], int]:
    """
    返回 (新 timeline, 本记录调整步数)。
    保证在可能情况下 light_first < light_second；若无法满足则尽量改善。
    """
    tl = list(timeline)
    steps = 0

    def light_first() -> int:
        return sum(1 for k in range(0, min(split_idx, len(tl))) if tl[k] == "light")

    def light_second() -> int:
        return sum(1 for k in range(split_idx, len(tl)) if tl[k] == "light")

    max_iter = len(tl) * 4 + 64
    it = 0
    while light_first() >= light_second() and it < max_iter:
        it += 1
        i_light = next((k for k in range(0, min(split_idx, len(tl))) if tl[k] == "light"), None)
        j_deep = next(
            (k for k in range(split_idx, len(tl)) if tl[k] in ("deep", "rem")),
            None,
        )
        if i_light is not None and j_deep is not None:
            tl[i_light] = "deep"
            tl[j_deep] = "light"
            steps += 1
            continue
        j_deep = next(
            (k for k in range(split_idx, len(tl)) if tl[k] in ("deep", "rem")),
            None,
        )
        if j_deep is not None:
            tl[j_deep] = "light"
            steps += 1
            continue
        i_light = next((k for k in range(0, min(split_idx, len(tl))) if tl[k] == "light"), None)
        if i_light is not None:
            tl[i_light] = "deep"
            steps += 1
            continue
        break
    return tl, steps


def compute_adjusted_idf(row: Dict[str, Any]) -> Tuple[str, Optional[List[Dict[str, str]]]]:
    """
    返回 (原因标签, 新 idf_data 或 None)。
    None 表示无需替换（已满足条件或无法解析）。
    """
    idf = row.get("idf_data")
    if not isinstance(idf, list) or not idf:
        return "skip_no_idf", None
    ranges = idf_to_monotonic_ranges(idf)
    if not ranges:
        return "skip_bad_ranges", None
    first_min = ranges[0][1]
    last_min = ranges[-1][2]
    split_abs = (first_min + last_min) // 2
    _, timeline = ranges_to_timeline(ranges)
    if not timeline:
        return "skip_empty_timeline", None
    split_idx = max(0, min(len(timeline), split_abs - first_min))
    lf0, ls0 = count_light_halves(timeline, split_idx)
    if lf0 < ls0:
        return "ok_already", None
    new_tl, steps = adjust_timeline_light_halves(timeline, split_idx)
    lf1, ls1 = count_light_halves(new_tl, split_idx)
    new_idf = timeline_to_idf(first_min, new_tl)
    if lf1 < ls1:
        return f"fixed_steps={steps}", new_idf
    return f"partial_steps={steps}_lf={lf1}_ls={ls1}", new_idf


def iter_health_data_files(directory: Path) -> List[Path]:
    return sorted(directory.glob("*health_data.json"))


def main() -> None:
    ap = argparse.ArgumentParser(description="调整 health_data JSON 的 idf_data：前半夜浅睡分钟 < 后半夜。")
    ap.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"扫描目录（默认 {DEFAULT_OUTPUT_DIR}）",
    )
    ap.add_argument(
        "--file",
        type=Path,
        default=None,
        help="只处理单个 JSON 文件（若指定则忽略 --dir 扫描）",
    )
    ap.add_argument("--dry-run", action="store_true", help="只统计将要修改的条数，不写回文件")
    args = ap.parse_args()

    files: List[Path]
    if args.file:
        files = [args.file.resolve()]
    else:
        files = iter_health_data_files(args.dir.resolve())

    if not files:
        print("未找到 *health_data.json 文件。")
        return

    for fp in files:
        if not fp.is_file():
            print(f"跳过（非文件）: {fp}")
            continue
        text = fp.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, list):
            print(f"跳过（根非数组）: {fp}")
            continue
        stats: Dict[str, int] = {}
        for row in data:
            if not isinstance(row, dict):
                continue
            reason, new_idf = compute_adjusted_idf(row)
            stats[reason] = stats.get(reason, 0) + 1
            if new_idf is not None and not args.dry_run:
                row["idf_data"] = new_idf
        print(f"{fp.name}: 记录数={len(data)} 分布={stats}")
        if args.dry_run:
            # 校验：成功 fixed 的记录应满足 前半夜浅睡 < 后半夜浅睡（partial 极端情况可能仍不成立）
            for row in data:
                if not isinstance(row, dict):
                    continue
                r2, idf2 = compute_adjusted_idf(copy.deepcopy(row))
                if idf2 is None or not r2.startswith("fixed_steps="):
                    continue
                ranges = idf_to_monotonic_ranges(idf2)
                first_min = ranges[0][1]
                split_abs = (ranges[0][1] + ranges[-1][2]) // 2
                _, tl = ranges_to_timeline(ranges)
                si = max(0, min(len(tl), split_abs - first_min))
                a, b = count_light_halves(tl, si)
                assert a < b, (fp.name, row.get("record_date"), a, b, r2)
            print("  [dry-run] 校验：凡标记 fixed_steps 的记录均满足 前浅睡 < 后浅睡")
            continue
        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  已写回: {fp}")


if __name__ == "__main__":
    main()
