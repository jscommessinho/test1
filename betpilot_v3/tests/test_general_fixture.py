"""General PREMATCH fixture identity: cross-league, cross-country, fail-closed.

No network and no financial operation. Existing pinned source list is verified.
"""
import copy
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from betpilot_v3.general_fixture import (
    countries_equal, load_registry, fixture_from_api, reconcile, team, pinned
)
from betpilot_v3.pitchapi_preflight import PreflightBlocked

REPO = Path(__file__).resolve().parents[2]
REGISTRY = load_registry(REPO / "data/pitchapi/league_pins_77.json")
KICK = "2026-11-15T17:00:00+00:00"
NOW = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)


def source(key, country, league, league_id, home="Home City", away="Away City",
           season="2026/2027", api_country=None, api_name=None, api_league_id=999):
    obj = REGISTRY[(league_id, league, country)]
    match = {"event_id": "m_REAL_PROVIDER_PIN", "home": home, "away": away,
             "competition": league, "kickoff": KICK,
             "cutoff": "2026-11-15T15:00:00+00:00"}
    ids = {"league_id": league_id, "season": season}
    pitch = {"pitchapi_event_id": "m_REAL_PROVIDER_PIN",
             "pitchapi_league_id": league_id, "country": country}
    api_country = api_country or {"ITA":"Italy","ESP":"Spain","ENG":"England",
                                  "GER":"Germany","NOR":"Norway","NED":"Netherlands"}[country]
    api_name = api_name or league
    evidence = {"api_fixture_id": 1234, "api_league_id": api_league_id,
                "api_season": 2026, "api_country": api_country,
                "api_league_name": api_name, "home":home,"away":away,
                "kickoff":KICK}
    quote = {"match":{"event_id":"API-FOOTBALL-fixture-1234",
                      "home":home,"away":away,
                      "competition":f"{api_country} {api_name} 2026",
                      "kickoff":KICK,"cutoff":"2026-11-15T14:59:00+00:00"},
             "odds_snapshot":{"bookmaker":"Bet365","provenance":"VERIFIED_EXTERNAL_SNAPSHOT",
               "timestamp":"2026-11-15T14:58:00+00:00",
               "snapshot_id":"APIFOOTBALL-1234-20261115T145800Z",
               "external_capture_reference":"synthetic test authentication claim"},
             "odds":{"q1":2.0,"qx":3.2,"q2":3.6,"qo":1.9,"qu":1.9,"qgg":1.9,"qng":1.9}}
    return match, ids, pitch, quote, evidence


class FakeAPI:
    def __init__(self, rows):
        self.rows = rows
        self.used = 0
    def get(self, path, **kwargs):
        self.used += 1
        return {"response":self.rows,"paging":{"total":1}}


class GeneralFixtureTests(unittest.TestCase):
    def test_original_77_registry_not_a_spanish_hardcode(self):
        self.assertEqual(len(REGISTRY),77)
        keys={v["registry_key"] for v in REGISTRY.values()}
        self.assertIn("esp-laliga", keys)
        self.assertIn("ita-seriea", keys)
        self.assertIn("eng-premier", keys)
        self.assertIn("ger-bundesliga", keys)
        self.assertIn("ned-eredivisie", keys)

    def test_reconcile_multiple_competitions_from_real_provider_pins(self):
        for key, api_country, api_name, api_id in [
            ("esp-laliga","Spain","La Liga",140),
            ("ita-seriea","Italy","Serie A",135),
            ("eng-premier","England","Premier League",39),
            ("ger-bundesliga","Germany","Bundesliga",78),
            ("ned-eredivisie","Netherlands","Eredivisie",88),
        ]:
            pin = next((k,v) for k,v in REGISTRY.items() if v["registry_key"]==key)
            lid, league, country = pin[0]
            args=source(key,country,league,lid,
                        api_country=api_country,api_name=api_name,
                        api_league_id=api_id)
            aligned, audit = reconcile(*args, REGISTRY)
            self.assertEqual(aligned["match"]["event_id"],"m_REAL_PROVIDER_PIN")
            self.assertEqual(audit["registry_key"],key)
            self.assertEqual(audit["api_league_id"],api_id)
            self.assertFalse(audit["direct_bet365_capture_independently_verified"])

    def test_same_competition_name_different_country_never_collides(self):
        items=[(k,v) for k,v in REGISTRY.items() if v["league_name"]=="Bundesliga"]
        self.assertGreaterEqual(len(items),2)
        self.assertNotEqual(items[0][0][0],items[1][0][0])
        self.assertNotEqual(items[0][0][2],items[1][0][2])
        self.assertFalse(countries_equal("AUT","Germany"))
        self.assertTrue(countries_equal("GER","Germany"))

    def test_team_name_translate_only_explicitly(self):
        self.assertEqual(team("Siviglia"),team("Sevilla"))
        self.assertNotEqual(team("Manchester City"),team("Manchester United"))

    def test_cross_provider_conflicts_fail_closed(self):
        key="ita-seriea"
        pin = next((k,v) for k,v in REGISTRY.items() if v["registry_key"]==key)
        base=source(key,pin[0][2],pin[0][1],pin[0][0])
        for index, field, value in [
            (4,"api_country","Germany"),
            (4,"api_season",2025),
            (4,"home","Different FC"),
            (4,"kickoff","2026-11-15T18:00:00Z"),
            (4,"api_fixture_id",99),
        ]:
            docs=copy.deepcopy(base)
            docs[index][field]=value
            with self.assertRaises(PreflightBlocked):
                reconcile(*docs,REGISTRY)

    def test_fixture_lookup_unique_and_no_unrelated_league(self):
        fixture={
          "fixture":{"id":87342,"date":KICK,"status":{"short":"NS"}},
          "league":{"id":135,"name":"Serie A","country":"Italy","season":2026},
          "teams":{"home":{"id":1,"name":"Inter"},"away":{"id":2,"name":"Juventus"}}
        }
        client=FakeAPI([fixture])
        resolved,raw=fixture_from_api(client,"FULL ANALYSIS: Inter - Juventus",
                                       "2026-11-15",now=NOW)
        self.assertEqual(resolved["id"],87342)
        self.assertEqual(client.used,1)
        with self.assertRaises(PreflightBlocked):
            fixture_from_api(FakeAPI([fixture]),"FULL ANALYSIS: Inter - Juventus",
                             "2026-11-15", league_id=140, now=NOW)
        with self.assertRaises(PreflightBlocked):
            fixture_from_api(FakeAPI([fixture,fixture]),"FULL ANALYSIS: Inter - Juventus",
                             "2026-11-15", now=NOW)


if __name__ == "__main__":
    unittest.main()
