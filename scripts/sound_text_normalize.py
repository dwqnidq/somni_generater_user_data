"""声音文案归一化：Hz 与数字紧贴、db 小写、db 音量数字左侧与 db 右侧按需补空格。"""

from __future__ import annotations

import re

# 音量 token：纯数字或 数字→数字，后接 db（大小写混写）
_DB_TOKEN = re.compile(r"(\d+(?:→\d+)?)db", re.IGNORECASE)
# 数字与 Hz 之间可有空白，统一为 432Hz
_HZ_TOKEN = re.compile(r"(\d+)\s+[Hh][Zz]\b")


def normalize_sound_text(s: str) -> str:
    if not s:
        return s
    t = s
    t = _HZ_TOKEN.sub(r"\1Hz", t)
    # 统一为 ...db（小写）
    # 不写末尾 \\b：避免「22dB固」等中文紧贴时匹配失败
    t = re.sub(r"(\d+(?:→\d+)?)\s*[dD][bB]", r"\1db", t)

    out: list[str] = []
    last = 0
    for m in _DB_TOKEN.finditer(t):
        out.append(t[last : m.start()])
        if m.start() > 0 and t[m.start() - 1] not in " \t\n":
            out.append(" ")
        out.append(m.group(0))
        last = m.end()
    out.append(t[last:])
    t = "".join(out)

    # db 后若紧跟非空白，补一个空格（已有则不重复）
    t = re.sub(r"db(?=\S)", "db ", t)
    t = re.sub(r" {2,}", " ", t)
    return t.strip()


def normalize_sound_fields_in_json(obj: object) -> None:
    """原地修改：凡 type==sound 的 dict，处理 name、description 字符串。"""
    if isinstance(obj, dict):
        if obj.get("type") == "sound":
            for k in ("name", "description"):
                v = obj.get(k)
                if isinstance(v, str):
                    obj[k] = normalize_sound_text(v)
        for v in obj.values():
            normalize_sound_fields_in_json(v)
    elif isinstance(obj, list):
        for x in obj:
            normalize_sound_fields_in_json(x)
