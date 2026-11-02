"""Integration tests: unknown/future leagues use the unchanged original GA3 core."""
from __future__ import annotations
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from integration.ga3_fast import pin_original_engine, run


def league_table(connection, entries):
    connection.execute(
        "CREATE TABLE leagues (slug TEXT PRIMARY KEY, name TEXT, pitchapi_league_id TEXT)")
    connection.executemany("INSERT INTO leagues VALUES (?,?,?)", entries)


def make_warehouse(root: Path):
    data = root / "data"
    data.mkdir()
    with sqlite3.connect(data / "catalogue.sqlite") as db:
        league_table(db, [
            ("new-league", "Future League Added Later", "l_NEW"),
            ("not-downloaded", "Still Awaiting Historical Download", "l_WAIT"),
        ])
    directory = data / "new-league"
    directory.mkdir()
    with sqlite3.connect(directory / "2025-2026.sqlite") as db:
        league_table(db, [
            ("new-league", "Future League Added Later", "l_NEW")])
        db.executescript("""
        CREATE TABLE seasons (league_slug TEXT, season_label TEXT);
        CREATE TABLE teams (pitchapi_team_id TEXT PRIMARY KEY, team_name TEXT);
        CREATE TABLE fixtures (
          pitchapi_fixture_id TEXT, league_slug TEXT, season_label TEXT,
          kickoff_utc TEXT, home_team_id TEXT, away_team_id TEXT,
          fixture_status TEXT, home_goals INTEGER, away_goals INTEGER,
          raw_json TEXT);
        """)
        db.execute("INSERT INTO seasons VALUES ('new-league','2025/2026')")
        db.executemany("INSERT INTO teams VALUES (?,?)", [
            ("t_home", "Home Town"), ("t_away", "Away Town"),
            ("t_other", "Other United"),
        ])
        teams=("t_home", "t_away", "t_other")
        for i in range(75):
            home,away=teams[i%3],teams[(i+1)%3]
            hg,ag=(i%4), ((i+2)%3)
            moment=datetime(2025,8,1,tzinfo=timezone.utc)+timedelta(days=i*5)
            provider_record={
                "id":f"m_{i}","home_team":{"id":home},
                "away_team":{"id":away},"score_home":hg,"score_away":ag}
            db.execute("""INSERT INTO fixtures
                (pitchapi_fixture_id,league_slug,season_label,kickoff_utc,
                 home_team_id,away_team_id,fixture_status,home_goals,away_goals,raw_json)
                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (f"m_{i}","new-league","2025/2026",moment.isoformat(),
                  home,away,"finished",hg,ag,json.dumps(provider_record)))


class AnyLeagueTests(unittest.TestCase):
    def test_authentic_core_integrity(self):
        self.assertGreater(len(pin_original_engine()), 3000)

    def test_future_competition_detected_and_predicted(self):
        try:
            import scipy
        except ImportError:
            self.skipTest("Full mathematical end-to-end requires SciPy")
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            make_warehouse(root)
            listing=run(warehouse=root,list_leagues=True)
            self.assertEqual(len(listing["competitions"]),2)
            self.assertEqual(sum(x["has_history"] for x in listing["competitions"]),1)
            with self.assertRaisesRegex(ValueError, "WAITING_FOR_DATABASE"):
                run(warehouse=root,league="Still Awaiting Historical Download",list_teams=True)
            now=datetime.now(timezone.utc)
            future=datetime(2028,2,11,tzinfo=timezone.utc)
            result=run(
                warehouse=root,league="Future League Added Later",
                home="Home Town",away="Away Town",kickoff=future,
                as_of=now)
            self.assertEqual(result["status"],"OK")
            self.assertEqual(result["competition_slug"],"new-league")
            self.assertEqual(result["training_matches"],75)
            self.assertTrue(result["goals_only"])
            self.assertFalse(result["uses_xg"])
            self.assertEqual(result["model_config"]["xg_blend"],0)
            self.assertEqual(result["model_config"]["min_train"],1)
            probs=result["markets"]
            self.assertAlmostEqual(
                probs["home_win"]+probs["draw"]+probs["away_win"],1.0,places=8)

    def test_quick_command_without_kickoff(self):
        """A matchup can be analyzed from saved goals only, with no match-time search."""
        try:
            import scipy
        except ImportError:
            self.skipTest("SciPy required")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_warehouse(root)
            result = run(warehouse=root, league="new-league",
                         home="Home Town", away="Away Town")
            self.assertEqual(result["analysis_type"], "CURRENT_DATA_SNAPSHOT")
            self.assertIsNone(result["kickoff_utc"])
            self.assertFalse(result["fixture_status_verified"])
            self.assertEqual(result["training_matches"], 75)
            self.assertIn("total", result["timing_ms"])
            # Real pinned GA3 engine -> additive joint-derived markets.
            additional = result["additional_markets"]
            self.assertEqual(additional["source"], "original_ga3_joint_score_grid")
            self.assertEqual(len(additional["probability_bounds"]), 52)
            self.assertIn(additional["status"], ("OK", "PARTIAL_GRID"))
            self.assertFalse(additional["ga3_lambda_formula_changed"])
            self.assertFalse(additional["bookmaker_odds_generated"])
            self.assertLess(result["timing_ms"]["total"], 10000.0)

    def test_no_future_match_without_asof(self):
        try:
            import scipy
        except ImportError:
            self.skipTest("Original GA3 engine requires SciPy; tested in full integration job")
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            make_warehouse(root)
            with self.assertRaisesRegex(ValueError,"Kickoff has passed"):
                run(warehouse=root,league="new-league",
                    home="Home Town",away="Away Town",
                    kickoff=datetime(2020,1,1,tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
