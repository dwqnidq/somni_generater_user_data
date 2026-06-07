import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN_DATA = os.path.join(ROOT, "scripts", "generate_data")
GEN_AI = os.path.join(GEN_DATA, "generate_ai")
for path in (ROOT, GEN_DATA, GEN_AI):
    if path not in sys.path:
        sys.path.insert(0, path)

from trend_14d_prompt_helpers import (  # noqa: E402
    build_trend_14d_payload,
    build_trend_14d_user_prompt,
    extract_emotion_score_from_schedule_insight,
    schedule_insight_uses_today_health,
)


class Trend14dScheduleInsightValidationTests(unittest.TestCase):
    def test_payload_includes_today_health_and_weather(self):
        payload = build_trend_14d_payload(
            "2026-05-21",
            "2026-05-08",
            [],
            [],
            today_health={"steps": 11305, "emotion_score": 78},
            today_weather={"humidity": "74"},
        )
        self.assertEqual(payload["today_health"]["emotion_score"], 78)
        self.assertEqual(payload["today_health"]["steps"], 11305)
        self.assertEqual(payload["today_weather"]["humidity"], "74")

    def test_extract_emotion_score(self):
        text = "密集工作将情绪压力指数推至87的高位，全天步数8376步。"
        self.assertEqual(extract_emotion_score_from_schedule_insight(text), 87)

    def test_schedule_insight_validation_passes(self):
        health = {"steps": 11305, "emotion_score": 78}
        text = "今日密集安排将情绪压力指数推至78的高位，全天步数11305步，叠加偏高湿度。"
        self.assertTrue(schedule_insight_uses_today_health(text, health))

    def test_schedule_insight_validation_fails_on_wrong_score(self):
        health = {"steps": 11305, "emotion_score": 78}
        text = "情绪压力指数达到87的高位，全天步数11305步。"
        self.assertFalse(schedule_insight_uses_today_health(text, health))

    def test_strict_prompt_reminds_exact_values(self):
        payload = build_trend_14d_payload(
            "2026-05-21",
            "2026-05-08",
            [],
            [],
            today_health={"steps": 11305, "emotion_score": 78},
        )
        prompt = build_trend_14d_user_prompt(payload, strict_health=True)
        self.assertIn("emotion_score=78", prompt)
        self.assertIn("steps=11305", prompt)


if __name__ == "__main__":
    unittest.main()
