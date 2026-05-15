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
            path = os.path.join(tmp, f"{uid}_health_data.json")
            rows = [
                {"record_date": "2026-04-18"},
                {"record_date": "2026-04-30"},
                {"record_date": "2026-05-01"},
            ]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rows, f)
            stats = prune_uid_output_files(uid, tmp, "2026-05-01")
            self.assertEqual(stats["total_removed"], 2)
            with open(path, encoding="utf-8") as f:
                kept = json.load(f)
            self.assertEqual([r["record_date"] for r in kept], ["2026-05-01"])


if __name__ == "__main__":
    unittest.main()
