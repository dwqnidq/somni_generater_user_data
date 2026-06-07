"""date_range_helpers 单元测试。"""

import os
import sys
import unittest
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_GEN = os.path.join(ROOT, "scripts", "generate_data")
if _SCRIPTS_GEN not in sys.path:
    sys.path.insert(0, _SCRIPTS_GEN)

from date_range_helpers import apply_date_range_overrides  # noqa: E402


class TestDateRangeHelpers(unittest.TestCase):
    def test_warmup_extends_start_backward(self):
        self.assertEqual(
            apply_date_range_overrides(
                date(2026, 6, 1),
                date(2026, 6, 30),
                date(2026, 5, 18),
                None,
            ),
            (date(2026, 5, 18), date(2026, 6, 30)),
        )

    def test_cli_narrows_start_forward(self):
        self.assertEqual(
            apply_date_range_overrides(
                date(2026, 6, 1),
                date(2026, 6, 30),
                date(2026, 6, 10),
                None,
            ),
            (date(2026, 6, 10), date(2026, 6, 30)),
        )


if __name__ == "__main__":
    unittest.main()
