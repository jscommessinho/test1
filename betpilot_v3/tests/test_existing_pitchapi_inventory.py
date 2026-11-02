"""Non-mutating inventory of already-acquired PitchAPI daily archives.

GitHub Actions uses the normal checkout token, never provider API credentials.
Read-only git fetch; no new collection/schedule/workflow or archive rewrites.
"""
import collections
import gzip
import json
import subprocess
import unittest

class ExistingPitchApiInventory(unittest.TestCase):
    def test_daily_raw_stats_existing_data_inventory(self):
        p=subprocess.run(["git","fetch","--no-tags","origin",
                          "refs/heads/pitchapi-daily-data"],
                         capture_output=True,text=True,timeout=35)
        if p.returncode:
            print("PITCHAPI_DAILY_INVENTORY_BLOCKED private_data_branch_not_readable")
            self.skipTest("Data branch not readable from checkout")
        tree=subprocess.run(["git","ls-tree","-r","--full-tree","--name-only","FETCH_HEAD"],
                            capture_output=True,text=True,check=True)
        files=[f for f in tree.stdout.splitlines() if f.startswith("warehouse/daily/") and f.endswith(".json.gz")]
        per_comp=collections.defaultdict(lambda: collections.Counter())
        per_team=collections.defaultdict(lambda: collections.Counter())
        total=collections.Counter()
        captures=[]
        for path in files:
            blob=subprocess.run(["git","show","FETCH_HEAD:"+path],
                                capture_output=True,check=True,timeout=15).stdout
            doc=json.loads(gzip.decompress(blob))
            total["partitions"]+=1
            for mid,item in doc.get("matches",{}).items():
                total["matches"]+=1
                fixture=item.get("fixture",{})
                league=fixture.get("league_id") or (fixture.get("league") or {}).get("id") or "LEAGUE_MISSING"
                comp=per_comp[league];comp["all"]+=1
                comp[item.get("status","STATUS_MISSING")]+=1
                if not fixture.get("season"):comp["missing_season"]+=1
                if not fixture.get("time_utc"):comp["missing_kickoff_utc"]+=1
                if not fixture.get("home_team") or not fixture.get("away_team"):comp["missing_teams"]+=1
                raw=item.get("stats_raw")
                stamp=item.get("retrieved_at_utc")
                if stamp:
                    captures.append(stamp)
                if not isinstance(raw,dict):
                    comp["missing_raw_stats"]+=1;continue
                comp["has_raw_stats"]+=1
                ids=((raw.get("home_team") or {}).get("id"),(raw.get("away_team") or {}).get("id"))
                fids=((fixture.get("home_team") or {}).get("id"),(fixture.get("away_team") or {}).get("id"))
                if ids!=fids:comp["raw_team_id_mismatch"]+=1
                periods=[x for x in raw.get("periods",[]) if x.get("period")=="All"]
                if len(periods)!=1:
                    comp["missing_or_duplicated_All"]+=1;continue
                items=[r for group in periods[0].get("groups",[]) for r in group.get("items",[])]
                exg=[r for r in items if r.get("key")=="expected_goals"]
                shots=[r for r in items if r.get("key")=="total_shots"]
                if exg and shots:
                    comp["All_expected_goals_and_total_shots"]+=1
                    if item.get("status")=="PASS_INTERNAL" and ids==fids:
                        comp["gts3_raw_candidate_PASS"]+=1
                        for team in fids:
                            if team: per_team[league][team]+=1
                else:comp["All_missing_required"]+=1
        summary={"schema":"BETPILOT_PHASE1_DAILY_STORED_SNAPSHOT_AUDIT",
                 "daily_partitions":total["partitions"],"stored_match_records":total["matches"],
                 "first_capture":min(captures) if captures else None,
                 "last_capture":max(captures) if captures else None,
                 "league_observed":len(per_comp),
                 "per_league":{k:dict(v) for k,v in sorted(per_comp.items())},
                 "team_sample_coverage":{k:dict(v) for k,v in sorted(per_team.items())},
                 "historical_artifacts_scanned":False,
                 "point_in_time_publication_proven":False}
        print("PITCHAPI_DAILY_INVENTORY "+json.dumps(summary,ensure_ascii=False,sort_keys=True))
        if not files:self.skipTest("Private data branch contains no readable daily gzip partitions")
