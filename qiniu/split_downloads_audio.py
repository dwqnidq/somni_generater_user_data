"""
将 qiniu/downloads 下的音频按 2~3 秒切片。

特性：
- 默认只处理 WAV（无第三方依赖）
- 每个源文件的切片输出到单独文件夹：qiniu/chunks/<源文件名(不含后缀)>/
- 切片时以 max_sec 为步长切割；最后一段若小于 min_sec 且前面已有切片，则合并到上一段
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import wave
from dataclasses import dataclass
from typing import Iterable, List, Tuple


@dataclass(frozen=True)
class Segment:
    start_frame: int
    end_frame: int  # exclusive

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame


def iter_audio_files(input_dir: str) -> Iterable[str]:
    for name in os.listdir(input_dir):
        path = os.path.join(input_dir, name)
        if os.path.isfile(path):
            yield path


def build_segments(total_frames: int, framerate: int, min_sec: float, max_sec: float) -> List[Segment]:
    if total_frames <= 0:
        return []

    min_frames = max(1, int(round(min_sec * framerate)))
    max_frames = max(1, int(round(max_sec * framerate)))
    if max_frames < min_frames:
        raise ValueError(f"max_sec 必须 >= min_sec（当前 min_sec={min_sec}, max_sec={max_sec}）")

    segments: List[Segment] = []
    start = 0
    while start < total_frames:
        end = min(total_frames, start + max_frames)
        segments.append(Segment(start_frame=start, end_frame=end))
        start = end

    if len(segments) >= 2 and segments[-1].frames < min_frames:
        last = segments.pop()
        prev = segments[-1]
        segments[-1] = Segment(start_frame=prev.start_frame, end_frame=last.end_frame)

    return segments


def safe_makedirs(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def write_wav_chunk(
    src_path: str,
    out_path: str,
    params: wave._wave_params,
    start_frame: int,
    frame_count: int,
) -> None:
    with wave.open(src_path, "rb") as r:
        r.setpos(start_frame)
        frames = r.readframes(frame_count)

    safe_makedirs(os.path.dirname(out_path))
    with wave.open(out_path, "wb") as w:
        w.setparams(params)
        w.writeframes(frames)


def process_wav(src_path: str, output_root: str, min_sec: float, max_sec: float) -> Tuple[int, str]:
    base = os.path.splitext(os.path.basename(src_path))[0]
    out_dir = os.path.join(output_root, base)
    safe_makedirs(out_dir)

    with wave.open(src_path, "rb") as r:
        params = r.getparams()
        total_frames = r.getnframes()
        framerate = r.getframerate()

    segments = build_segments(total_frames, framerate, min_sec=min_sec, max_sec=max_sec)
    if not segments:
        return (0, out_dir)

    manifest = {
        "source": os.path.abspath(src_path),
        "framerate": framerate,
        "channels": params.nchannels,
        "sampwidth": params.sampwidth,
        "nframes": total_frames,
        "min_sec": min_sec,
        "max_sec": max_sec,
        "chunks": [],
    }

    for idx, seg in enumerate(segments):
        chunk_name = f"chunk_{idx:04d}.wav"
        out_path = os.path.join(out_dir, chunk_name)
        write_wav_chunk(
            src_path=src_path,
            out_path=out_path,
            params=params,
            start_frame=seg.start_frame,
            frame_count=seg.frames,
        )
        manifest["chunks"].append(
            {
                "file": chunk_name,
                "start_sec": seg.start_frame / framerate,
                "end_sec": seg.end_frame / framerate,
                "duration_sec": seg.frames / framerate,
            }
        )

    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return (len(segments), out_dir)


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="将 qiniu/downloads 下的 WAV 切成 2~3 秒小片段")
    parser.add_argument(
        "--input-dir",
        default=os.path.join(os.path.dirname(__file__), "downloads"),
        help="输入目录（默认：qiniu/downloads）",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(os.path.dirname(__file__), "chunks"),
        help="输出根目录（默认：qiniu/chunks）",
    )
    parser.add_argument("--min-sec", type=float, default=2.0, help="最小切片时长（秒）")
    parser.add_argument("--max-sec", type=float, default=3.0, help="最大切片时长（秒）")
    parser.add_argument("--recursive", action="store_true", help="递归扫描 input-dir（默认不递归）")
    args = parser.parse_args(argv)

    input_dir = os.path.abspath(args.input_dir)
    output_dir = os.path.abspath(args.output_dir)

    if not os.path.isdir(input_dir):
        print(f"输入目录不存在: {input_dir}", file=sys.stderr)
        return 2

    safe_makedirs(output_dir)

    def collect_files() -> Iterable[str]:
        if not args.recursive:
            yield from iter_audio_files(input_dir)
            return
        for root, _dirs, files in os.walk(input_dir):
            for name in files:
                path = os.path.join(root, name)
                if os.path.isfile(path):
                    yield path

    files = list(collect_files())
    if not files:
        print(f"目录下没有文件: {input_dir}")
        return 0

    ok_files = 0
    skipped = 0
    total_chunks = 0
    for path in files:
        ext = os.path.splitext(path)[1].lower()
        if ext != ".wav":
            skipped += 1
            continue
        try:
            n_chunks, out_dir = process_wav(path, output_root=output_dir, min_sec=args.min_sec, max_sec=args.max_sec)
            ok_files += 1
            total_chunks += n_chunks
            print(f"[OK] {os.path.basename(path)} -> {out_dir} ({n_chunks} chunks)")
        except wave.Error as e:
            print(f"[FAIL] {path}: WAV 解析失败: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[FAIL] {path}: {e}", file=sys.stderr)

    print(f"\n完成：处理 {ok_files} 个 WAV，生成 {total_chunks} 个切片；跳过非 WAV {skipped} 个文件。")
    print(f"输出目录：{output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

