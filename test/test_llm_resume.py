"""LLM 断点续跑与配额检测."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN_AI = os.path.join(ROOT, "scripts", "generate_data", "generate_ai")
for p in (ROOT, os.path.join(ROOT, "scripts", "generate_data"), GEN_AI):
    if p not in sys.path:
        sys.path.insert(0, p)

from generate_ai.llm_client import is_quota_or_rate_limit  # noqa: E402
from generate_ai.llm_resume import (  # noqa: E402
    bootstrap_resume,
    row_has_llm_payload,
    run_llm_date_batch,
    upsert_row,
)


class TestQuotaDetection(unittest.TestCase):
    def test_http_429(self):
        self.assertTrue(is_quota_or_rate_limit(429, ""))

    def test_message_hint(self):
        self.assertTrue(is_quota_or_rate_limit(200, "You exceeded your current quota"))


class TestLlmResume(unittest.TestCase):
    def test_row_has_payload(self):
        self.assertTrue(
            row_has_llm_payload({"uid": "u", "record_date": "2026-05-01", "notice": {"title": "a"}})
        )
        self.assertFalse(row_has_llm_payload({"uid": "u", "record_date": "2026-05-01"}))

    def test_run_llm_date_batch_resume_skips_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "u_sleep_quality.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    [
                        {
                            "uid": "u",
                            "record_date": "2026-05-01",
                            "quality_analysis_module": [{"target": "t", "description": "d"}],
                        }
                    ],
                    f,
                )
            calls: list[str] = []

            def process(d: str):
                calls.append(d)
                return {"uid": "u", "record_date": d, "quality_analysis_module": []}

            out = run_llm_date_batch(
                uid="u",
                output_path=path,
                resume=True,
                dates=["2026-05-01", "2026-05-02"],
                process_date=process,
            )
            self.assertEqual(calls, ["2026-05-02"])
            self.assertEqual(len(out), 2)

    def test_upsert_row(self):
        by = {}
        upsert_row(by, {"record_date": "2026-05-02", "x": 1})
        upsert_row(by, {"record_date": "2026-05-01", "x": 2})
        merged = bootstrap_resume("", False)[0]
        del merged
        from generate_ai.llm_resume import merge_rows_sorted

        self.assertEqual(merge_rows_sorted(by)[0]["record_date"], "2026-05-01")


if __name__ == "__main__":
    unittest.main()
