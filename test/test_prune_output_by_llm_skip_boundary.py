"""prune_output_by_llm_skip_boundary 单元测试。"""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_GEN = os.path.join(ROOT, "scripts", "generate_data")
if _SCRIPTS_GEN not in sys.path:
    sys.path.insert(0, _SCRIPTS_GEN)

from prune_output_by_llm_skip_boundary import (  # noqa: E402
    compute_keep_from_date,
    keep_from_qweather_monthly_first_date,
    prune_uid_output_files,
)


class TestPruneOutputByLlmSkipBoundary(unittest.TestCase):
    def test_compute_keep_from_min_skip_plus_one(self):
        self.assertEqual(
            compute_keep_from_date(["2026-04-30", "2026-04-25", None], "2026-04-18"),
            "2026-04-26",
        )
        self.assertEqual(
            compute_keep_from_date([None, None, None], "2026-04-18"),
            "2026-04-18",
        )
        self.assertIsNone(compute_keep_from_date([None, None, None], None))

    def test_prune_removes_rows_before_keep_from(self):
        with tempfile.TemporaryDirectory() as tmp:
            uid = "testuid"
            llm_path = os.path.join(tmp, f"{uid}_ai_analysis_14d.json")
            health_path = os.path.join(tmp, f"{uid}_health_data.json")
            llm_rows = [
                {"record_date": "2026-04-18"},
                {"record_date": "2026-04-30"},
                {"record_date": "2026-05-01"},
            ]
            health_rows = [{"record_date": "2026-04-18"}, {"record_date": "2026-05-01"}]
            with open(llm_path, "w", encoding="utf-8") as f:
                json.dump(llm_rows, f)
            with open(health_path, "w", encoding="utf-8") as f:
                json.dump(health_rows, f)
            stats = prune_uid_output_files(uid, tmp, "2026-05-01")
            self.assertEqual(stats["total_removed"], 2)
            with open(llm_path, encoding="utf-8") as f:
                kept = json.load(f)
            self.assertEqual([r["record_date"] for r in kept], ["2026-05-01"])
            with open(health_path, encoding="utf-8") as f:
                health_kept = json.load(f)
            self.assertEqual(len(health_kept), 2, "基础 health 数据不应被 LLM 裁剪删除")

    def test_keep_from_qweather_monthly_first_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "qweather_monthly_data.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    [{"date": "2026-05-20", "tempMax": "25"}, {"date": "2026-05-21"}],
                    f,
                )
            self.assertEqual(keep_from_qweather_monthly_first_date(tmp), "2026-05-20")

    def test_prune_does_not_touch_health_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            uid = "testuid"
            path = os.path.join(tmp, f"{uid}_health_data.json")
            rows = [{"record_date": "2026-05-07"}, {"record_date": "2026-05-20"}]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rows, f)
            stats = prune_uid_output_files(uid, tmp, "2026-05-22")
            self.assertEqual(stats["total_removed"], 0)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), rows)


if __name__ == "__main__":
    unittest.main()
