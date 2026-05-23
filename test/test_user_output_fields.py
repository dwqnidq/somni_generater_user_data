"""用户 output 翻译字段白名单：仅提取指定中文字段。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.translate.user_output_fields import (  # noqa: E402
    FIELD_HANDLERS,
    collect_translatable_strings,
    infer_basename_from_stem,
)
from scripts.translate._common import collect_chinese_strings, has_chinese  # noqa: E402

UID = "69aea6f3af5e6cbf0802796a"
OUTPUT = ROOT / "output"


def _load(basename: str) -> list[dict]:
    path = OUTPUT / f"{UID}_{basename}.json"
    if not path.is_file():
        pytest.skip(f"missing fixture {path.name}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [x for x in raw if isinstance(x, dict)]


@pytest.mark.parametrize("basename", list(FIELD_HANDLERS.keys()))
def test_whitelist_is_subset_of_all_chinese(basename: str) -> None:
    docs = _load(basename)
    whitelisted = collect_translatable_strings(docs, basename)
    all_zh: list[str] = []
    collect_chinese_strings(docs, all_zh)
    whitelisted_set = set(whitelisted)
    extra = [s for s in set(all_zh) if s not in whitelisted_set]
    assert not extra, f"{basename} 存在未纳入白名单的中文字段样例: {extra[:3]}"


def test_infer_basename() -> None:
    assert (
        infer_basename_from_stem(
            "69aea6f3af5e6cbf0802796a_ai_analysis_14d", tuple(FIELD_HANDLERS)
        )
        == "ai_analysis_14d"
    )
    assert (
        infer_basename_from_stem(
            "69aea6f3af5e6cbf0802796a_ai_analysis_14d_en", tuple(FIELD_HANDLERS)
        )
        == "ai_analysis_14d"
    )


def test_sleep_events_only_event_fields() -> None:
    docs = _load("sleep_events")
    strings = collect_translatable_strings(docs, "sleep_events")
    assert strings
    assert all(has_chinese(s) for s in strings)
    # uid / code / type 等不应出现
    assert not any(s.startswith("69aea") for s in strings)
