"""Offline test for the existing PitchAPI 26-league raw team-stats archive join."""
import json
import gzip
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from betpilot_v3.gts3_original_adapter import (
    collect_stats, collect_original_team_stats_archive
)
from betpilot_v3.pitchapi_preflight import PreflightBlocked

TARGET = {
    "pitchapi_event_id": "m_NEXT",
    "pitchapi_league_id": "l_0ErfuF",
    "competition": "LaLiga", "country": "ESP", "season": "2026/2027",
    "home": {"id": "t_GETAFE"}, "away": {"id": "t_SEVILLA"},
    "kickoff": "2026-11-02T12:00:00Z"
}
CUT = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)


def make_archive(path, *, wrong_league=False, too_late=False, bad_raw=False):
    records = {}
    for i in range(10):
        mid = "m_HIST_" + str(i)
        day = f"2026-10-{i+1:02d}"
        records[mid] = {
            "fixture": {
                "match_id": mid, "date": day, "season": "2026/2027",
                "home": {"id": TARGET["home"]["id"]},
                "away": {"id": TARGET["away"]["id"]},
                "home_goals": 1, "away_goals": 1,
            },
            "team_xg": {"home": 1.2, "away": 1.1},
            "statistics": (
                {"match_id": mid, "raw_team_stats": True}
                if not bad_raw else {"wrong": "synthetic invalid"}
            ),
            "retrieved_at_utc": (
                "2026-11-02T14:00:00Z" if too_late
                else "2026-10-31T08:00:00Z"
            )
        }
    obj = {
        "schema": "BETPILOT_PITCHAPI_TEAM_STATS_v1",
        "league": {
            "id": ("l_OTHER" if wrong_league else "l_0ErfuF"),
            "name": "LaLiga", "country": "ESP"
        },
        "as_of": "2026-10-31", "records": records, "rejected": {}
    }
    Path(path).write_text(json.dumps(obj), "utf8")


def validate_original(raw, mid, ht, at):
    assert raw["match_id"] == mid
    assert raw["raw_team_stats"] is True
    assert ht == TARGET["home"]["id"] and at == TARGET["away"]["id"]
    return {"expected_goals": 1.2}, {"expected_goals": 1.1}, 0


class ExistingRawSourceTests(unittest.TestCase):
    def test_pinned_original_raw_source_supplies_gts_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "teamstats.json"
            make_archive(archive)
            rows, audit = collect_stats(
                td, TARGET, CUT, stats_validator=validate_original,
                team_stats_archive=archive)
            self.assertEqual(len(rows["records"]), 10)
            self.assertEqual(audit["coverage"]["home_total"], 10)
            self.assertEqual(audit["coverage"]["away_at_away"], 10)
            self.assertEqual(audit["original_team_stats_archive"]["included"], 10)
            self.assertIn("source_sha256", audit["original_team_stats_archive"])

    def test_new_current_season_raw_stats_extend_historical_gts(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            archive=root/"teamstats.json"
            make_archive(archive)
            day="2026-10-25"
            fresh_id="m_NEW_FROM_DAILY"
            raw_stats={"match_id":fresh_id,"raw_team_stats":True}
            fixture={"id":fresh_id,"date":day,
                     "time_utc":day+"T12:00:00Z", "status":"finished",
                     "league_id":TARGET["pitchapi_league_id"],
                     "home_team":{"id":TARGET["home"]["id"]},
                     "away_team":{"id":TARGET["away"]["id"]},
                     "score_home":1,"score_away":1}
            item={"fixture":fixture,"stats_raw":raw_stats,
                  "status":"PASS_INTERNAL",
                  "retrieved_at_utc":"2026-10-26T08:00:00Z"}
            directory=root/"daily/2026/10"
            directory.mkdir(parents=True)
            with gzip.open(directory/(day+".json.gz"),"wt",encoding="utf8") as stream:
                json.dump({"schema":"BETPILOT_PITCHAPI_WORLD_DAILY_v1",
                           "date_utc":day,"matches":{fresh_id:item}},stream)
            rows,qa=collect_stats(root,TARGET,CUT,
                                  stats_validator=validate_original,
                                  team_stats_archive=archive)
            self.assertEqual(len(rows["records"]),11)
            self.assertEqual(rows["records"][fresh_id]["season"],"2026/2027")
            self.assertEqual(qa["current_season_rows_anchored_to_original"],1)

    def test_bad_league_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "teamstats.json"
            make_archive(archive, wrong_league=True)
            with self.assertRaises(PreflightBlocked) as exc:
                collect_original_team_stats_archive(
                    archive, TARGET, CUT, validate_original)
            self.assertEqual(exc.exception.code,
                             "GTS3_TEAM_STATS_SOURCE_INVALID")

    def test_stats_captured_after_cutoff_not_used(self):
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "teamstats.json"
            make_archive(archive, too_late=True)
            with self.assertRaises(PreflightBlocked) as exc:
                collect_stats(td, TARGET, CUT, stats_validator=validate_original,
                              team_stats_archive=archive)
            self.assertEqual(exc.exception.code, "GTS3_SOURCE_INSUFFICIENT")

    def test_invalid_raw_stats_not_replaced_with_shot_xg(self):
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "teamstats.json"
            make_archive(archive, bad_raw=True)
            with self.assertRaises(PreflightBlocked) as exc:
                collect_stats(td, TARGET, CUT, stats_validator=validate_original,
                              team_stats_archive=archive)
            self.assertEqual(exc.exception.code, "GTS3_SOURCE_INSUFFICIENT")


if __name__ == "__main__":
    unittest.main()
