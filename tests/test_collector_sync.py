"""No-network test: first historical sync and a later new match addition."""
import json
import tempfile
import unittest
from pathlib import Path
from collector.sync import sync, MATCH_DATASETS


def envelope(data):
    return json.dumps({"data":data},separators=(",",":")).encode()


class FakePitchAPI:
    def __init__(self):
        self.matches = ["m_FIRST"]
        self.calls = 0

    def get(self, endpoint, params=None):
        self.calls += 1
        if endpoint == "/v1/leagues":
            data = {"leagues":[
                {"name":"Premier League","country_code":"ENG",
                 "id":"l_VALID","seasons":["2021/2022","2026/2027"]},
                {"name":"Serie A","country_code":"ITA",
                 "id":"l_ITALY","seasons":["2026/2027"]},
                {"name":"La Liga","country_code":"ESP",
                 "id":"l_SPAIN","seasons":["2026/2027"]},
                {"name":"Bundesliga","country_code":"GER",
                 "id":"l_GERMAN","seasons":["2026/2027"]},
                {"name":"Ligue 1","country_code":"FRA",
                 "id":"l_FRANCE","seasons":["2026/2027"]},
            ]}
        elif endpoint.startswith("/v1/leagues/"):
            data = {
                "league":{"id":"l_VALID","name":"Premier League"},
                "matches":[
                    {"id":m,"status":"finished","time_utc":"2021-01-02T10:00:00Z",
                     "home_team":{"id":"t_ONE","name":"One"},
                     "away_team":{"id":"t_TWO","name":"Two"},
                     "score_home":2,"score_away":1}
                    for m in (self.matches if params.get("season")=="2021/2022" else [])
                ]
            }
        else:
            mid=endpoint.split("/")[3]
            kind=endpoint.split("/")[-1] if endpoint.count("/")>3 else "match_summary"
            if kind == "match_summary":
                data={"id":mid,"home_team":{"id":"t_ONE","name":"One"},
                      "away_team":{"id":"t_TWO","name":"Two"},
                      "status":"finished","score_home":2,"score_away":1}
            elif kind == "stats":
                data={"match_id":mid,"home_team":{"id":"t_ONE"},
                      "away_team":{"id":"t_TWO"},"periods":[{
                          "period":"All","groups":[{"group_name":"xG","items":[{
                              "key":"expected_goals","home":"2.0",
                              "away":"1.0","format_type":"double"
                          }]}]}]}
            elif kind == "events":
                data={"match_id":mid,"events":[{"event_type":"Goal","minute":15}]}
            elif kind == "lineups":
                data={"match_id":mid,"home":{"confirmed":True,"formation":"4-4-2"},
                      "away":{"confirmed":True,"formation":"4-3-3"}}
            elif kind == "momentum":
                data={"match_id":mid,"points":[{"minute":30,"value":3}]}
            elif kind == "advanced":
                data={"match_id":mid,"teams":[
                    {"team":{"id":"t_ONE"},"defending":{"ppda":8.5}}
                ]}
            else:
                raise AssertionError(kind)
        raw=envelope(data)
        return raw, json.loads(raw)


class CollectorTests(unittest.TestCase):
    def test_backfill_then_new_match_only(self):
        with tempfile.TemporaryDirectory() as root:
            warehouse=Path(root)
            api=FakePitchAPI()
            result=sync(api,warehouse,refresh_days=0)
            self.assertEqual(result["seasons"]["eng-premier/2021/2022"]["finished_matches"],1)
            first_requests=api.calls
            api.calls=0
            sync(api,warehouse,refresh_days=0)
            self.assertLess(api.calls, first_requests)
            api.matches.append("m_SECOND")
            api.calls=0
            result=sync(api,warehouse,refresh_days=0)
            self.assertEqual(result["seasons"]["eng-premier/2021/2022"]["finished_matches"],2)
            self.assertEqual(result["seasons"]["eng-premier/2021/2022"]["accepted_endpoint_responses"],2*len(MATCH_DATASETS))
            self.assertTrue((warehouse/"data"/"eng-premier"/"2021-2022.sqlite").is_file())
            self.assertTrue((warehouse/"reports"/"pitchapi-sync.json").is_file())

if __name__ == "__main__":
    unittest.main()
