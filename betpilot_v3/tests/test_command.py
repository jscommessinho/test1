"""Offline verification of the BETPILOT command; no provider calls are made."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from betpilot_v3.command import execute, _fixture_day_from_index

REQUEST = "BETPILOT: Deportivo La Coruña - Osasuna"
FIXTURE = "2026-11-05T19:00:00+00:00"
NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)


class OneCommandTests(unittest.TestCase):
    def test_absent_approved_credentials_is_explicit_error_not_fiction(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {}, clear=True):
            result = execute(REQUEST, output_dir=Path(td)/"out", now=NOW)
            self.assertEqual(result["status"], "AUTHENTICATION_ERROR")
            self.assertFalse(result["five_engines_verified"])
            self.assertFalse(result["uses_web_search"])
            self.assertTrue((Path(td)/"out/BETPILOT_REPORT.md").exists())

    def test_local_original_source_bundle_handoff(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            matchdir=root/"bundles"/"one"
            matchdir.mkdir(parents=True)
            match={"event_id":"m_123","home":"Deportivo La Coruña",
                   "away":"Osasuna","competition":"LaLiga2",
                   "kickoff":FIXTURE,"cutoff":"2026-11-02T10:00:00+00:00"}
            for name in ("match.json","pitchapi_identity.json","ga3_native_output.json",
                         "gts3_native_output.json","GTS3_FROZEN_MODEL_PARAMS.json","RC15_EVENT_INPUT.json"):
                (matchdir/name).write_text(json.dumps(match if name=="match.json" else {"test":True}))
            def fake_run(args):
                self.assertEqual(args.request, REQUEST)
                return {"status":"RESEARCH_REPORT_AVAILABLE","decision":"NO_BET_INSUFFICIENT_MARKET_EVIDENCE"}
            result=execute(REQUEST, output_dir=root/"out",sources_root=root/"bundles",
                           native_runner=fake_run, now=NOW)
            self.assertEqual(result["status"],"RESEARCH_REPORT_AVAILABLE")
            self.assertEqual(result["mode"],"VERIFIED_NATIVE_BUNDLE")
            self.assertEqual(result["decision"],"NO_BET_INSUFFICIENT_MARKET_EVIDENCE")

    def test_authenticated_index_does_not_guess_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"idx.json"
            p.write_text(json.dumps({"provider":"PitchAPI","fixtures":[
                {"home":"Deportivo La Coruña","away":"Osasuna",
                 "kickoff":FIXTURE,"status":"scheduled"}]}))
            d=_fixture_day_from_index(p,"Deportivo La Coruña","Osasuna",NOW)
            self.assertEqual(d,"2026-11-05")
            p.write_text(json.dumps({"provider":"Other","fixtures":[]}))
            with self.assertRaises(ValueError):
                _fixture_day_from_index(p,"Deportivo La Coruña","Osasuna",NOW)

    def test_authenticated_runner_never_silent_success(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ,{
                "APISPORTS_KEY":"MOCK_NOT_REAL", "PITCHAPI_API_KEY":"MOCK_NOT_REAL"}):
            root=Path(td)
            (root/"warehouse").mkdir();(root/"history").mkdir()
            seen=[]
            def fake_run(args):
                seen.append(args)
                return {"status":"NO_BET_PREMATCH_CHAIN_BLOCKED",
                        "reason_code":"FIXTURE_NOT_FOUND","reason":"No event in original archive"}
            result=execute(REQUEST, output_dir=root/"out",
                           warehouse_root=root/"warehouse",history_root=root/"history",
                           fixture_index=None,live_runner=fake_run,now=NOW)
            self.assertEqual(len(seen),1)
            self.assertIsNone(seen[0].day_utc)
            self.assertEqual(result["date_selection"],"authenticated_api_sports_autodiscovery")
            self.assertEqual(result["status"],"NO_BET_DATA_OR_INTEGRATION_BLOCKED")
            self.assertFalse(result["five_engines_verified"])


if __name__ == "__main__":
    unittest.main()
