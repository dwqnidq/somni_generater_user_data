"""文件作用：用于 trim audio 相关的数据处理或流程支持。"""

import os
import sys
from pydub import AudioSegment


def trim_audio(input_file: str, start_sec: float, end_sec: float, output_file: str = None) -> str:
    """
    截取音频文件的指定时间段。

    :param input_file: 输入音频文件路径
    :param start_sec: 截取起始时间（秒）
    :param end_sec: 截取结束时间（秒）
    :param output_file: 输出文件路径（可选，默认在原文件名后加 _trimmed）
    :return: 输出文件路径
    """
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"文件不存在: {input_file}")

    if start_sec < 0:
        raise ValueError("起始时间不能为负数")
    if end_sec <= start_sec:
        raise ValueError("结束时间必须大于起始时间")

    audio = AudioSegment.from_file(input_file)
    duration_sec = len(audio) / 1000.0

    if start_sec >= duration_sec:
        raise ValueError(f"起始时间 {start_sec}s 超过音频总时长 {duration_sec:.1f}s")
    if end_sec > duration_sec:
        print(f"警告: 结束时间 {end_sec}s 超过音频总时长 {duration_sec:.1f}s，将截取到末尾")
        end_sec = duration_sec

    trimmed = audio[int(start_sec * 1000):int(end_sec * 1000)]

    if output_file is None:
        base, ext = os.path.splitext(input_file)
        output_file = f"{base}_trimmed{ext}"
        # 避免覆盖已有文件，自动加序号
        counter = 1
        while os.path.exists(output_file):
            output_file = f"{base}_trimmed_{counter}{ext}"
            counter += 1

    fmt = os.path.splitext(output_file)[1].lstrip('.') or 'wav'
    trimmed.export(output_file, format=fmt)

    print(f"截取完成: {start_sec}s ~ {end_sec}s -> {output_file} ({len(trimmed) / 1000:.1f}s)")
    return output_file


def main():
    if len(sys.argv) < 4:
        print("用法: python trim_audio.py <音频文件> <起始秒> <结束秒> [输出文件]")
        print("示例: python trim_audio.py audio.wav 10 30")
        print("      python trim_audio.py audio.wav 10 30 output.wav")
        sys.exit(1)

    input_file = sys.argv[1]
    start_sec = float(sys.argv[2])
    end_sec = float(sys.argv[3])
    output_file = sys.argv[4] if len(sys.argv) >= 5 else None

    trim_audio(input_file, start_sec, end_sec, output_file)


if __name__ == "__main__":
    main()
