"""No-network regressions for real provider crosswalk and fail-closed orchestration.

These are integration-contract mocks, NOT a claim of real API execution.
"""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from betpilot_v3.real_prematch_bridge import (
    align_authenticated_quote, digest, execute, write
)
from betpilot_v3.pitchapi_preflight import PreflightBlocked

KICK = "2026-11-02T12:00:00+00:00"
CUT = "2026-11-02T10:02:00+00:00"
QCAP = "2026-11-02T10:00:00+00:00"


def sample():
    match = {"event_id": "m_PITCH1", "home": "Getafe", "away": "Sevilla",
             "competition": "LaLiga", "kickoff": KICK, "cutoff": CUT}
    identity = {"league_id": "l_0ErfuF", "season": "2026/2027",
                "home_team_id": "t_GETAFE", "away_team_id": "t_SEVILLA"}
    target = {"pitchapi_event_id": "m_PITCH1",
              "pitchapi_league_id": "l_0ErfuF", "country": "ESP"}
    quote = {
        "match": {
            "event_id": "API-FOOTBALL-fixture-123456",
            "home": "Getafe", "away": "Sevilla",
            "competition": "Spain La Liga 2026",
            "kickoff": KICK, "cutoff": QCAP
        },
        "odds_snapshot": {
            "bookmaker": "Bet365", "timestamp": "2026-11-02T09:59:00+00:00",
            "provenance": "VERIFIED_EXTERNAL_SNAPSHOT",
            "snapshot_id": "APIFOOTBALL-123456-20261102T095900Z",
            "external_capture_reference": "authenticated API fixture and odds hashes"
        },
        "odds": {
            "q1": 2.4, "qx": 3.1, "q2": 3.2,
            "qo": 2.0, "qu": 1.8, "qgg": 1.9, "qng": 1.9
        }
    }
    return match, identity, target, quote


class QuoteCrosswalkTests(unittest.TestCase):
    def align(self, match=None, identity=None, target=None, quote=None, **kwargs):
        default = sample()
        return align_authenticated_quote(
            match or default[0], identity or default[1],
            target or default[2], quote or default[3],
            api_league_id=kwargs.get("api_league_id", 140),
            api_season=kwargs.get("api_season", 2026))

    def test_independent_event_ids_preserved_in_audit(self):
        match, ids, target, quote = sample()
        original = copy.deepcopy(quote)
        remapped, audit = self.align(match, ids, target, quote)
        self.assertEqual(quote, original)
        self.assertEqual(remapped["match"], match)
        self.assertEqual(audit["independent_fixture_id_crosswalk"]
                         ["api_football_event_id"], "API-FOOTBALL-fixture-123456")
        self.assertFalse(audit["independent_fixture_id_crosswalk"]
                         ["bookmaker_direct_capture_verified"])
        self.assertEqual(remapped["odds_snapshot"], quote["odds_snapshot"])

    def test_exact_laliga_frozen_rc15_alias(self):
        from betpilot.bookmaker_verdict_c import canonical_competition
        self.assertEqual(canonical_competition("LaLiga"), "Spain La Liga")
        self.assertEqual(canonical_competition("LaLiga 2026"), "Spain La Liga")
        self.assertEqual(canonical_competition("Brazil Serie A 2026"),
                         "Brazil Serie A 2026")

    def test_cross_provider_league_and_season_conflicts(self):
        for kwargs in ({"api_league_id": 135}, {"api_season": 2025}):
            with self.assertRaises(PreflightBlocked):
                self.align(**kwargs)

    def test_wrong_venue_kickoff_or_team_blocks(self):
        for field, value in (("home", "Madrid"), ("away", "Valencia"),
                             ("kickoff", "2026-11-02T14:00:00+00:00")):
            _, _, _, q = sample()
            q["match"][field] = value
            with self.assertRaises(PreflightBlocked):
                self.align(quote=q)

    def test_quote_capture_after_pitchapi_cutoff_blocks(self):
        _, _, _, q = sample()
        q["match"]["cutoff"] = "2026-11-02T10:03:00+00:00"
        with self.assertRaises(PreflightBlocked) as e:
            self.align(quote=q)
        self.assertEqual(e.exception.code, "BOOKMAKER_CUTOFF_CONFLICT")

    def test_missing_odds_or_unverified_provenance_blocks(self):
        for mutate in (
            lambda q: q["odds"].pop("qng"),
            lambda q: q["odds_snapshot"].update(provenance="SYNTHETIC")
        ):
            _, _, _, q = sample()
            mutate(q)
            with self.assertRaises(PreflightBlocked):
                self.align(quote=q)

    def test_wrong_source_event_id_rejected(self):
        match, ids, target, quote = sample()
        target["pitchapi_event_id"] = "m_OTHER"
        with self.assertRaises(PreflightBlocked):
            self.align(match, ids, target, quote)


class OfflineSingleCommandTests(unittest.TestCase):
    def args(self, root):
        p = Path(root)
        (p/"world").mkdir()
        (p/"historical").mkdir()
        return SimpleNamespace(
            request="FULL ANALYSIS: Getafe - Sevilla",
            day_utc="2026-11-02", country_code="ESP", competition="LaLiga",
            api_league_id=140, api_season=2026,
            warehouse_root=p/"world", history_root=p/"historical",
            output_dir=p/"out")

    @staticmethod
    def fake_quote(client, **kwargs):
        directory = Path(kwargs["output_dir"])
        directory.mkdir(parents=True)
        write(directory/"RC15_EVENT_INPUT.json", sample()[3])
        return {"requests_this_run": 2}

    @staticmethod
    def fake_phase1(args):
        native = Path(args.output_dir)/"native"/"fixture"
        native.mkdir(parents=True)
        match, identity, _, _ = sample()
        five = {
            "match.json": match,
            "pitchapi_identity.json": identity,
            "ga3_native_output.json": {"fake":"source"},
            "gts3_native_output.json": {"fake":"source"},
            "GTS3_FROZEN_MODEL_PARAMS.json": {"fake":"source"},
        }
        hashes = {}
        for name, obj in five.items():
            write(native/name, obj)
            hashes[name] = digest(native/name)
        write(native/"PITCHAPI_NATIVE_MANIFEST.json",
              {"five_file_sha256": hashes})
        return {"status": "PITCHAPI_NATIVE_FIVE_EXPORTS_VERIFIED",
                "native_bundle": str(native)}

    def test_mock_chain_produces_rebound_six_bundle_and_original_quote(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {
                "PITCHAPI_API_KEY":"TEST_ONLY", "APISPORTS_KEY":"TEST_ONLY"}):
            a = self.args(td)
            seen = []
            def inspect_full_run(input_):
                folder = Path(input_.sources_root)/"fixture"
                self.assertEqual(json.loads((folder/"match.json").read_text())
                                 ["event_id"], "m_PITCH1")
                self.assertEqual(json.loads((folder/"RC15_EVENT_INPUT.json").read_text())
                                 ["match"]["event_id"], "m_PITCH1")
                seen.append(True)
                return {"status":"RESEARCH_REPORT_AVAILABLE",
                        "decision":"NO_BET_INSUFFICIENT_MARKET_EVIDENCE"}
            result = execute(
                a, odds_run=self.fake_quote,
                client_factory=lambda *args, **kwargs: object(),
                native_phase1=self.fake_phase1,
                full_analysis=inspect_full_run)
            self.assertEqual(result["status"], "PREMATCH_RESEARCH_REPORT_AVAILABLE",
                             result)
            self.assertFalse(result["operational_eligible"])
            self.assertTrue(seen)
            original = json.loads((a.output_dir/"authenticated_quote"/
                                  "RC15_EVENT_INPUT.json").read_text())
            self.assertEqual(original["match"]["event_id"],
                             "API-FOOTBALL-fixture-123456")
            self.assertTrue((a.output_dir/"CROSS_PROVIDER_IDENTITY_AUDIT.json").exists())

    def test_missing_credentials_fails_before_network(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {
                "PITCHAPI_API_KEY":"", "APISPORTS_KEY":""}):
            a=self.args(td)
            result = execute(a, odds_run=lambda *args,**kwargs:
                             self.fail("No quote queries permitted"))
            self.assertEqual(result["status"], "NO_BET_PREMATCH_CHAIN_BLOCKED")
            self.assertEqual(result["reason_code"], "APISPORTS_KEY_UNAVAILABLE")

    def test_real_phase1_rejection_prevents_fake_full_report(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {
                "PITCHAPI_API_KEY":"TEST_ONLY", "APISPORTS_KEY":"TEST_ONLY"}):
            a=self.args(td)
            result=execute(a,odds_run=self.fake_quote,
                client_factory=lambda *args, **kwargs: object(),
                native_phase1=lambda a: {
                    "status":"NO_BET_NATIVE_INPUTS_BLOCKED",
                    "reason_code":"GTS3_SOURCE_INSUFFICIENT",
                    "reason":"No complete historical All stats"},
                full_analysis=lambda a:self.fail("Do not run BA3"))
            self.assertEqual(result["reason_code"], "GTS3_SOURCE_INSUFFICIENT")
            self.assertFalse((a.output_dir/"six_verified_inputs").exists())


if __name__ == "__main__":
    unittest.main()
