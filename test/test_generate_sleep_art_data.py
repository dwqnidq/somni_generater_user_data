"""generate_sleep_art_data：planet / metrics 由 raw 确定性推导。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts", "generate_data")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import generate_sleep_art_data as g  # noqa: E402


class TestSleepArtDerivations(unittest.TestCase):
    def test_planet_size_hours(self):
        self.assertEqual(g.planet_size_from_raw({"total_sleep_minutes": 0}), 0.6)
        self.assertEqual(g.planet_size_from_raw({"total_sleep_minutes": 359}), 0.6)
        self.assertEqual(g.planet_size_from_raw({"total_sleep_minutes": 360}), 0.8)
        self.assertEqual(g.planet_size_from_raw({"total_sleep_minutes": 417}), 0.8)
        self.assertEqual(g.planet_size_from_raw({"total_sleep_minutes": 420}), 1.0)
        self.assertEqual(g.planet_size_from_raw({"total_sleep_minutes": 479}), 1.0)
        self.assertEqual(g.planet_size_from_raw({"total_sleep_minutes": 480}), 1.2)

    def test_efficiency_colors_and_grade(self):
        land, water, ring = g.planet_colors_from_efficiency(85)
        self.assertEqual(land, "#8ec5f5")
        self.assertEqual(water, "#284e71")
        self.assertEqual(ring, "#a5c8e9")
        self.assertEqual(g.efficiency_grade_label(85), "Fast")
        self.assertEqual(g.efficiency_grade_label(84), "Optimal")
        self.assertEqual(g.efficiency_grade_label(75), "Optimal")
        self.assertEqual(g.efficiency_grade_label(74), "Normal")
        self.assertEqual(g.efficiency_grade_label(65), "Normal")
        self.assertEqual(g.efficiency_grade_label(64), "Slow")

    def test_ring_radius_deep(self):
        self.assertEqual(g.ring_radius_from_deep_ratio(11), 1.4)
        self.assertEqual(g.ring_radius_from_deep_ratio(12), 1.6)
        self.assertEqual(g.ring_radius_from_deep_ratio(17), 1.6)
        self.assertEqual(g.ring_radius_from_deep_ratio(18), 1.8)
        self.assertEqual(g.ring_radius_from_deep_ratio(20), 1.8)
        self.assertEqual(g.ring_radius_from_deep_ratio(21), 2.0)

    def test_stability_noise_and_grades(self):
        self.assertEqual(g.planet_noise_density_from_stability(40), 0.85)
        self.assertEqual(g.planet_noise_density_from_stability(41), 0.65)
        self.assertEqual(g.planet_noise_density_from_stability(60), 0.65)
        self.assertEqual(g.planet_noise_density_from_stability(61), 0.45)
        self.assertEqual(g.planet_noise_density_from_stability(80), 0.45)
        self.assertEqual(g.planet_noise_density_from_stability(81), 0.25)
        self.assertEqual(g.stability_grade_label(40), "Poor")
        self.assertEqual(g.stability_grade_label(41), "Fair")
        self.assertEqual(g.stability_grade_label(80), "Good")
        self.assertEqual(g.stability_grade_label(81), "Great")

    def test_same_raw_same_outputs(self):
        raw = {
            "total_sleep_minutes": 430,
            "deep_sleep_ratio": 12,
            "sleep_efficiency": 85,
            "awake_ratio": 15,
            "turnover_count": 40,
            "apnea_count": 4,
            "sleep_latency": 33,
            "average_heartbeat": 66,
            "average_respiration": 14,
        }
        m1 = g.build_metrics(raw)
        m2 = g.build_metrics(raw)
        self.assertEqual(m1, m2)
        s = m1["stability"]["score"]
        p1 = g.build_planet(raw, s)
        p2 = g.build_planet(raw, s)
        self.assertEqual(p1, p2)

    def test_build_planet_sample(self):
        raw = {
            "total_sleep_minutes": 430,
            "deep_sleep_ratio": 12,
            "sleep_efficiency": 85,
            "awake_ratio": 15,
            "turnover_count": 40,
            "apnea_count": 4,
            "sleep_latency": 33,
            "average_heartbeat": 66,
            "average_respiration": 14,
        }
        stab = g.stability_score_from_raw(raw)
        planet = g.build_planet(raw, stab)
        self.assertEqual(planet["planet_size"], 1.0)
        self.assertEqual(planet["land_color"], "#8ec5f5")
        self.assertEqual(planet["ring_radius"], 1.6)
        self.assertEqual(
            planet["planet_noise_density"],
            g.planet_noise_density_from_stability(stab),
        )


if __name__ == "__main__":
    unittest.main()
