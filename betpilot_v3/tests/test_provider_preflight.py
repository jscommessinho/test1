"""Network-free test of credential preflight: external calls always mocked."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from betpilot_v3.provider_preflight import check
from betpilot_v3.command import execute


class ProviderPreflightTests(unittest.TestCase):
    def test_no_secrets_are_echoed_and_missing_key_blocks(self):
        with patch.dict(os.environ, {}, clear=True):
            r = check()
        self.assertEqual(r["status"], "AUTHENTICATION_ERROR")
        self.assertFalse(r["real_match_analysis_completed"])

    def test_both_approved_providers_verified_with_bounded_requests(self):
        marker1 = "sensitive_pitchapi_example_key"
        marker2 = "sensitive_api_sports_example_key"
        observed = []
        class P:
            def __init__(self, key, *, budget, pause):
                self.assert_key = key
                observed.append(("pitch", key, budget, pause))
            def get(self, path):
                observed.append(("endpoint",path))
                return {"leagues": []}
        class S:
            def __init__(self, key, *, reserve, max_calls):
                observed.append(("sport", key, reserve, max_calls))
            def preflight(self):
                return {"quota_remaining_at_start": 50}
        with patch.dict(os.environ, {"PITCHAPI_API_KEY": marker1,
                                     "APISPORTS_KEY": marker2}):
            r = check(pitchapi_factory=P, apisports_factory=S)
        self.assertEqual(r["status"], "PROVIDER_CONNECTIVITY_VERIFIED")
        self.assertEqual(observed[0], ("pitch", marker1, 1, 0))
        self.assertEqual(observed[1], ("endpoint", "/v1/leagues"))
        self.assertEqual(observed[2], ("sport", marker2, 20, 1))
        self.assertNotIn(marker1,json.dumps(r))
        self.assertNotIn(marker2,json.dumps(r))

    def test_provider_errors_not_echoed(self):
        marker1 = "sensitive_pitchapi_example_key"
        marker2 = "sensitive_api_sports_example_key"
        class Broken:
            def __init__(self, *_args, **_kwargs): pass
            def get(self, path): raise ValueError("leak " + marker1)
            def preflight(self): raise ValueError("leak " + marker2)
        with patch.dict(os.environ, {"PITCHAPI_API_KEY": marker1,
                                     "APISPORTS_KEY": marker2}):
            r = check(pitchapi_factory=Broken, apisports_factory=Broken)
        self.assertEqual(r["status"], "PROVIDER_PREFLIGHT_BLOCKED")
        self.assertNotIn(marker1,json.dumps(r))
        self.assertNotIn(marker2,json.dumps(r))

    def test_one_command_redacts_provider_keys_even_if_runner_leaks(self):
        key1="sensitive_pitchapi_example_key"
        key2="sensitive_api_sports_example_key"
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {
                "PITCHAPI_API_KEY": key1, "APISPORTS_KEY": key2}):
            root = Path(td)
            (root/"warehouse").mkdir(); (root/"history").mkdir()
            def leaking_runner(args):
                return {"status":"NO_BET_PREMATCH_CHAIN_BLOCKED", "reason_code":"TEST_BLOCK",
                        "reason": key1 + " " + key2}
            r = execute("BETPILOT: Deportivo La Coruña - Osasuna",
                        output_dir=root/"output", warehouse_root=root/"warehouse",
                        history_root=root/"history", live_runner=leaking_runner)
            output = (root/"output"/"BETPILOT_STATUS.json").read_text("utf8")
            markdown = (root/"output"/"BETPILOT_REPORT.md").read_text("utf8")
            self.assertNotIn(key1,output+markdown+json.dumps(r))
            self.assertNotIn(key2,output+markdown+json.dumps(r))
            self.assertIn("[REDACTED_PROVIDER_SECRET]",output)

if __name__ == "__main__":
    unittest.main()
