"""multi_day_llm_helpers 小工具测试（stdlib unittest，无需 pytest）。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN_AI = os.path.join(ROOT, "scripts", "generate_data", "generate_ai")
if GEN_AI not in sys.path:
    sys.path.insert(0, GEN_AI)

from multi_day_llm_helpers import apply_max_records  # noqa: E402


class TestApplyMaxRecords(unittest.TestCase):
    def test_unlimited(self) -> None:
        xs = ["a", "b", "c"]
        self.assertEqual(apply_max_records(xs, None), xs)
        self.assertEqual(apply_max_records(xs, 0), xs)
        self.assertEqual(apply_max_records(xs, -1), xs)

    def test_cap(self) -> None:
        xs = ["2026-01-01", "2026-01-02", "2026-01-03"]
        self.assertEqual(apply_max_records(xs, 2), ["2026-01-01", "2026-01-02"])


if __name__ == "__main__":
    unittest.main()
