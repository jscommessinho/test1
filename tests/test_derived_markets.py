"""Pure-math regression tests. No PitchAPI, API-Sports, network or calibration."""
import unittest

from integration.derived_markets import from_joint_score_grid


class DerivedMarketTests(unittest.TestCase):
    def setUp(self):
        # p(0-0)=.1, p(0-1)=.3, p(1-0)=.2, p(1-1)=.4
        self.result = from_joint_score_grid([[0.1, 0.3], [0.2, 0.4]])
        self.p = self.result["markets"]

    def test_double_chance_partition(self):
        self.assertEqual(self.result["status"], "OK")
        self.assertAlmostEqual(self.p["double_chance_1x"], 0.7)
        self.assertAlmostEqual(self.p["double_chance_x2"], 0.8)
        self.assertAlmostEqual(self.p["double_chance_12"], 0.5)

    def test_team_markets(self):
        self.assertAlmostEqual(self.p["home_scores"], 0.6)
        self.assertAlmostEqual(self.p["away_scores"], 0.7)
        self.assertAlmostEqual(self.p["home_multigol_1_2"], 0.6)
        self.assertAlmostEqual(self.p["away_multigol_1_3"], 0.7)
        self.assertAlmostEqual(self.p["home_over_1_5"], 0)
        self.assertAlmostEqual(self.p["away_over_2_5"], 0)

    def test_combinations_use_joint_events_not_products(self):
        joint = self.p["double_chance_1x_and_over_1_5"]
        self.assertAlmostEqual(joint, 0.4)
        # Multiplying marginals would incorrectly produce .7 * .4 = .28.
        self.assertNotAlmostEqual(joint, 0.7 * 0.4)
        self.assertAlmostEqual(
            self.p["double_chance_x2_and_multigol_1_2"], 0.7)
        self.assertAlmostEqual(
            self.p["double_chance_12_and_over_1_5"], 0.0)

    def test_all_required_combinations_are_available(self):
        self.assertEqual(len(self.p), 3 + 2 * (1 + 2 + 2) + 3 * (5 + 8))
        self.assertFalse(self.result["bookmaker_odds_generated"])
        self.assertFalse(self.result["ga3_lambda_formula_changed"])

    def test_truncated_joint_grid_has_bounds_not_false_exact_values(self):
        partial = from_joint_score_grid([[0.1, 0.1], [0.1, 0.1]])
        self.assertEqual(partial["status"], "PARTIAL_GRID")
        self.assertEqual(partial["markets"], {})
        self.assertAlmostEqual(partial["captured_mass"], 0.4)
        self.assertAlmostEqual(
            partial["probability_bounds"]["home_scores"]["lower"], 0.2)
        self.assertAlmostEqual(
            partial["probability_bounds"]["home_scores"]["upper"], 0.8)

    def test_invalid_probabilities_fail_closed(self):
        for grid in (
            [[-0.1, 1.1]], [[float("nan")]], [[0]], [[0.9, 0.9]],
            [[0.3, 0.2], [0.5]],
        ):
            with self.subTest(grid=grid):
                with self.assertRaises(ValueError):
                    from_joint_score_grid(grid)


if __name__ == "__main__":
    unittest.main()
