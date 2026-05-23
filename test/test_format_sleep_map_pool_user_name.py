"""format_sleep_map_pool_user_name 中文序号展示名。"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import format_sleep_map_pool_user_name  # noqa: E402


def test_pool_user_names():
    assert format_sleep_map_pool_user_name(1) == "用户一"
    assert format_sleep_map_pool_user_name(9) == "用户九"
    assert format_sleep_map_pool_user_name(10) == "用户十"
    assert format_sleep_map_pool_user_name(13) == "用户十三"
    assert format_sleep_map_pool_user_name(21) == "用户二十一"


if __name__ == "__main__":
    test_pool_user_names()
    print("ok")
