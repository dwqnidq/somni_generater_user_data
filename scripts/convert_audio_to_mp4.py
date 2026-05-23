#!/usr/bin/env python3
"""将 audio 目录下所有非 .mp3 媒体文件转为 .mp3（依赖系统已安装 ffmpeg）。

超过 15 分钟的音/视频仅保留前 15 分钟。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIO_DIR = ROOT / "audio"
MAX_DURATION_SEC = 15 * 60


def _find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _output_path(source: Path) -> Path:
    return source.with_suffix(".mp3")


def _iter_sources(audio_dir: Path, recursive: bool) -> list[Path]:
    if not audio_dir.is_dir():
        raise FileNotFoundError(f"目录不存在: {audio_dir}")
    pattern = "**/*" if recursive else "*"
    files: list[Path] = []
    for path in sorted(audio_dir.glob(pattern)):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.suffix.lower() == ".mp3":
            continue
        files.append(path)
    return files


def _convert_one(ffmpeg: str, source: Path, dest: Path, overwrite: bool) -> None:
    if dest.exists() and not overwrite:
        raise FileExistsError(f"目标已存在（使用 --overwrite 覆盖）: {dest}")

    dest.parent.mkdir(parents=True, exist_ok=True)

    encode_cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y" if overwrite else "-n",
        "-i",
        str(source),
        "-t",
        str(MAX_DURATION_SEC),
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        str(dest),
    ]
    result = subprocess.run(encode_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(err or f"ffmpeg 退出码 {result.returncode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="将 audio 目录中的非 mp3 文件转为 mp3；超过 15 分钟仅保留前 15 分钟（需要 ffmpeg）。"
    )
    parser.add_argument(
        "--audio-dir",
        type=Path,
        default=DEFAULT_AUDIO_DIR,
        help=f"源目录（默认: {DEFAULT_AUDIO_DIR}）",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="递归处理子目录",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅列出待转换文件，不执行",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已存在的 .mp3",
    )
    parser.add_argument(
        "--remove-source",
        action="store_true",
        help="转换成功后删除源文件",
    )
    args = parser.parse_args(argv)

    audio_dir = args.audio_dir.resolve()
    sources = _iter_sources(audio_dir, args.recursive)
    if not sources:
        print(f"未发现需转换的文件: {audio_dir}")
        return 0

    print(f"待转换 {len(sources)} 个文件（目录: {audio_dir}）")
    for src in sources:
        dest = _output_path(src)
        print(f"  {src.name} -> {dest.name}")

    if args.dry_run:
        return 0

    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        print("错误: 未找到 ffmpeg，请先安装并加入 PATH。", file=sys.stderr)
        print("  macOS: brew install ffmpeg", file=sys.stderr)
        return 1

    ok, failed = 0, 0
    for src in sources:
        dest = _output_path(src)
        try:
            _convert_one(ffmpeg, src, dest, args.overwrite)
            print(f"OK  {src.name} -> {dest.name}")
            if args.remove_source:
                src.unlink()
                print(f"    已删除源文件: {src.name}")
            ok += 1
        except Exception as exc:
            print(f"FAIL {src.name}: {exc}", file=sys.stderr)
            failed += 1

    print(f"完成: 成功 {ok}, 失败 {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
