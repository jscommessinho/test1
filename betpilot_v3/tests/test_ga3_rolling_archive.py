"""Regression: GA3 must combine prior-season archive with mature current-season
daily PitchAPI records. No live API, no model mutation, no fake fixture.
"""
import gzip
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from betpilot.pitchapi_history_archive import sha256, load_archive
from betpilot_v3.ga3_rolling_archive import merged_original_and_daily
from betpilot_v3.pitchapi_preflight import PreflightBlocked

LEAGUE = {"id":"l_0ALvwF", "name":"Serie A", "country":"ITA"}
TARGET = {
    "pitchapi_event_id":"m_FUT001", "pitchapi_league_id":LEAGUE["id"],
    "competition":LEAGUE["name"], "country":LEAGUE["country"],
    "season":"2026/2027", "kickoff":"2026-11-05T20:00:00Z"
}
CUTOFF = datetime(2026,11,4,12,tzinfo=timezone.utc)
H={"id":"t_HOME01", "name":"Inter"}
A={"id":"t_AWAY01", "name":"Juventus"}


def historical(path):
    def mk(mid,day,season):
        v={"match_id":mid,"league_id":LEAGUE["id"],"season":season,
           "date":day,"home":{"id":H["id"],"name":"Inter","goals":2,"xg":1.2,"shots":12},
           "away":{"id":A["id"],"name":"Juventus","goals":1,"xg":0.8,"shots":8},
           "qa_internal":"PASS","qa_external":"OFFICIAL_REFERENCE_NOT_VERIFIED",
           "source":"PitchAPI"}
        v["source_digest"]=sha256(v)
        v["retrieved_at_utc"]=day+"T22:00:00Z"
        return v
    obj={"schema":"BETPILOT-PITCHAPI-HISTORY-ARCHIVE-RESEARCH-v1",
         "provider":"PitchAPI","league":LEAGUE,"as_of_utc_date":"2026-10-31",
         "records":{"m_OLD001":mk("m_OLD001","2025-09-20","2025/2026"),
                    "m_CUR001":mk("m_CUR001","2026-10-20","2026/2027")},
         "warnings":{},"rejected":{}}
    path.write_text(json.dumps(obj)+"\n")
    return obj


def daily(root,*,capture="2026-10-30T08:00:00Z", status="PASS_INTERNAL",
          event="2026-10-29", season=None, include_bad=False):
    f={"id":"m_NEW001","date":event,"time_utc":event+"T15:00:00Z",
       "status":"finished","league_id":LEAGUE["id"],
       "home_team":H,"away_team":A,"score_home":1,"score_away":2}
    if season is not None:f["season"]=season
    obj={"schema":"BETPILOT_PITCHAPI_WORLD_DAILY_v1","date_utc":event,
         "matches":{"m_NEW001":{
             "fixture":f, "retrieved_at_utc":capture,
             "status":status,
             "home_shots":{"shots":8,"xg":float("nan") if include_bad else 0.7},
             "away_shots":{"shots":11,"xg":1.6},
             "internal_quality":{"internal_absolute_deltas":{"home":0.02,"away":0.04}}
         }}}
    path=root/"daily"/event[:4]/event[5:7]/(event+".json.gz")
    path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,"wt",encoding="utf8") as f:json.dump(obj,f)
    return path


class RollingSourceTests(unittest.TestCase):
    def run_roll(self,td,**kwargs):
        root=Path(td)
        source=root/"old.json"
        obj=historical(source)
        daily(root,**kwargs)
        target=root/"rolled.json"
        stats=merged_original_and_daily(source,root,TARGET,CUTOFF,target)
        return obj,target,stats

    def test_historical_plus_new_current_season_at_cutoff(self):
        with tempfile.TemporaryDirectory() as td:
            old,p,stats=self.run_roll(td)
            merged=load_archive(p,LEAGUE,"2026-11-04")
            self.assertEqual(len(merged["records"]),3)
            self.assertEqual(stats["added_daily_records"],1)
            self.assertEqual(merged["records"]["m_NEW001"]["season"],"2026/2027")
            self.assertEqual(merged["records"]["m_NEW001"]["season_provenance"],
                             "CURRENT_SEASON_ANCHORED_TO_PRIOR_PINNED_HISTORY")
            self.assertEqual(json.loads((Path(td)/"old.json").read_text()),old)
            self.assertIn("m_OLD001",merged["records"])

    def test_no_after_cutoff_capture(self):
        with tempfile.TemporaryDirectory() as td:
            _,p,stats=self.run_roll(td,capture="2026-11-06T09:00:00Z")
            self.assertEqual(stats["added_daily_records"],0)
            self.assertEqual(len(json.loads(p.read_text())["records"]),2)

    def test_no_quality_bypass(self):
        with tempfile.TemporaryDirectory() as td:
            _,p,stats=self.run_roll(td,status="INCOMPLETE_SOURCE")
            self.assertEqual(stats["added_daily_records"],0)
            self.assertEqual(len(json.loads(p.read_text())["records"]),2)

    def test_explicit_contradictory_season_refused(self):
        with tempfile.TemporaryDirectory() as td:
            _,p,stats=self.run_roll(td,season="2027/2028")
            self.assertEqual(stats["added_daily_records"],0)
            self.assertIn("GA3_DAILY_SEASON_UNKNOWN",stats["daily_exclusions"])

    def test_do_not_use_day_not_mature_for_ga3(self):
        with tempfile.TemporaryDirectory() as td:
            _,p,stats=self.run_roll(td,event="2026-11-03",capture="2026-11-03T20:00:00Z")
            self.assertEqual(stats["added_daily_records"],0)
            self.assertIn("GA3_DAILY_NOT_MATURE",stats["daily_exclusions"])

    def test_bad_xg_rejected_not_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            _,p,stats=self.run_roll(td,include_bad=True)
            self.assertEqual(stats["added_daily_records"],0)
            self.assertIn("GA3_DAILY_METRICS_INVALID",stats["daily_exclusions"])


if __name__ == "__main__":
    unittest.main()
