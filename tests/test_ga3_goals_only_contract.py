"""Regression guard: GA3 adapter must never consume xG or enable xG blending.

Uses Python stdlib only: original proprietary/standalone GA3 package is not
required by the database CI runner. Functional integration is separately tested
against the supplied original GA3 RC2 archive.
"""
import ast
import pathlib
import unittest

BRIDGE = pathlib.Path(__file__).resolve().parents[1] / "integration" / "ga3_sqlite_adapter.py"


class GoalsOnlyContractTests(unittest.TestCase):
    def setUp(self):
        self.source = BRIDGE.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)

    def test_no_individual_or_team_xg_database_queries(self):
        self.assertNotIn("match_team_stats", self.source)
        self.assertNotIn("expected_goals", self.source)
        self.assertNotIn("team_xg(", self.source)
        self.assertNotIn("match_shots", self.source)
        self.assertNotIn("match_player_stats", self.source)

    def test_no_experimental_xg_option(self):
        self.assertNotIn("--allow-experimental-pitchapi-xg", self.source)
        self.assertNotIn("allow_experimental_pitchapi_xg", self.source)

    def test_nonzero_xg_blend_explicitly_rejected(self):
        self.assertGreaterEqual(self.source.count("if config.xg_blend != 0"), 1)
        self.assertGreaterEqual(self.source.count("if cfg.xg_blend != 0"), 1)
        self.assertIn("xG blend is forbidden", self.source)

    def test_no_historical_team_match_minimum_in_adapter(self):
        self.assertNotIn("min_team_matches", self.source)
        self.assertNotIn("Minimum 25", self.source)
        self.assertNotIn("Need 25 historical matches", self.source)

    def test_native_match_objects_include_only_goal_fields(self):
        funcs = [f for f in ast.walk(self.tree) if isinstance(f, ast.FunctionDef)
                 and f.name == "load_from_warehouse"]
        self.assertEqual(len(funcs), 1)
        match_calls = [n for n in ast.walk(funcs[0]) if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Name) and n.func.id == "Match"]
        self.assertEqual(len(match_calls), 1)
        self.assertEqual(len(match_calls[0].args), 8)
        self.assertFalse(match_calls[0].keywords)


if __name__ == "__main__":
    unittest.main()
