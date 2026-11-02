"""Offline safety regressions for single-request V3 orchestration."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from full_analysis import FullAnalysisBlocked, locate, parse_request, run, REQUIRED

def fixture(root, folder, event_id="p:123", cutoff="2026-11-01T09:00:00+00:00"):
    p = Path(root)/folder
    p.mkdir(parents=True)
    m = {"event_id":event_id, "home":"Inter", "away":"Juventus",
         "competition":"Serie A", "kickoff":"2026-11-02T19:45:00+00:00", "cutoff":cutoff}
    for name in REQUIRED:
        (p/name).write_text(json.dumps(m if name=="match.json" else {"test":True}),"utf8")
    return p

def args(root):
    return SimpleNamespace(request="FULL ANALYSIS: Inter - Juventus",
            sources_root=Path(root), repo_root=Path(root),
            output_dir=Path(root)/"output",competition=None,kickoff=None,event_id=None)

class SafeOrchestratorTests(unittest.TestCase):
    def test_command_parser(self):
        self.assertEqual(parse_request("FULL ANALYSIS: Inter - Juventus"), ("Inter","Juventus"))
        self.assertEqual(parse_request("full analysis: Inter – Juventus"), ("Inter","Juventus"))
        with self.assertRaises(FullAnalysisBlocked):parse_request("Inter vs Juventus")
        with self.assertRaises(FullAnalysisBlocked):parse_request("FULL ANALYSIS: Inter - Inter")
    def test_fixture_identity(self):
        with tempfile.TemporaryDirectory() as d:
            fixture(d,"one")
            self.assertEqual(locate("FULL ANALYSIS: inter - juventus",d)["match"]["event_id"],"p:123")
    def test_no_guess_ambiguous(self):
        with tempfile.TemporaryDirectory() as d:
            fixture(d,"one",event_id="1");fixture(d,"two",event_id="2")
            with self.assertRaises(FullAnalysisBlocked) as err:locate("FULL ANALYSIS: Inter - Juventus",d)
            self.assertEqual(err.exception.code,"AMBIGUOUS_MATCH")
            self.assertEqual(locate("FULL ANALYSIS: Inter - Juventus",d,event_id="2")["match"]["event_id"],"2")
    def test_missing_source(self):
        with tempfile.TemporaryDirectory() as d:
            p=fixture(d,"one");(p/"ga3_native_output.json").unlink()
            with self.assertRaises(FullAnalysisBlocked) as err:locate("FULL ANALYSIS: Inter - Juventus",d)
            self.assertEqual(err.exception.code,"MISSING_NATIVE_OUTPUT")
    def test_leakage(self):
        with tempfile.TemporaryDirectory() as d:
            fixture(d,"one",cutoff="2026-11-03T19:45:00+00:00")
            with self.assertRaises(FullAnalysisBlocked) as err:locate("FULL ANALYSIS: Inter - Juventus",d)
            self.assertEqual(err.exception.code,"INVALID_MATCH")
    def test_duplicate_json_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=fixture(d,"one");(p/"gts3_native_output.json").write_text('{"x":1,"x":2}')
            with self.assertRaises(FullAnalysisBlocked) as err:locate("FULL ANALYSIS: Inter - Juventus",d)
            self.assertEqual(err.exception.code,"DUPLICATE_JSON_KEY")
    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=fixture(d,"one");(p/"RC15_EVENT_INPUT.json").unlink()
            (p/"RC15_EVENT_INPUT.json").symlink_to(p/"match.json")
            with self.assertRaises(FullAnalysisBlocked) as err:locate("FULL ANALYSIS: Inter - Juventus",d)
            self.assertEqual(err.exception.code,"UNSAFE_SOURCE_PATH")
    def test_fail_closed_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            s=run(args(d))
            self.assertEqual(s["reason_code"],"MATCH_NOT_FOUND")
            self.assertFalse(s["operational_eligible"])
            self.assertTrue((Path(d)/"output"/"FULL_ANALYSIS_STATUS.json").is_file())
    def test_native_runner_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            fixture(d,"one")
            s=run(args(d))
            self.assertEqual(s["reason_code"],"NATIVE_RUNNER_UNAVAILABLE")
    def test_native_exception_no_bet(self):
        with tempfile.TemporaryDirectory() as d:
            fixture(d,"one")
            runner=Path(d)/"betpilot_v3";runner.mkdir()
            (runner/"run_verified_inputs.py").write_text("pass")
            s=run(args(d),launch=Mock(return_value=SimpleNamespace(returncode=2)))
            self.assertEqual(s["reason_code"],"NATIVE_CHAIN_FAILED")
    def test_zero_exit_without_native_audit_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            fixture(d,"one")
            runner=Path(d)/"betpilot_v3";runner.mkdir()
            (runner/"run_verified_inputs.py").write_text("pass")
            s=run(args(d),launch=Mock(return_value=SimpleNamespace(returncode=0)))
            self.assertEqual(s["status"],"NO_BET_DATA_OR_INTEGRATION_BLOCKED")
            self.assertEqual(s["reason_code"],"NATIVE_OUTPUT_INVALID")

    def test_tampered_native_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            fixture(d,"one")
            runner=Path(d)/"betpilot_v3";runner.mkdir()
            (runner/"run_verified_inputs.py").write_text("pass")
            def fake_launch(*cmd,**kwargs):
                out=Path(d)/"output"/"NATIVE_CHAIN"/"BA3"
                out.mkdir(parents=True)
                (out/"BETANALYZER_3_ANALYSIS.json").write_text(
                    json.dumps({"operational_eligible":False,"research_only":True,
                                "decision":"NO_BET_INSUFFICIENT_MARKET_EVIDENCE"}))
                (out/"INTEGRATION_AUDIT.json").write_text(
                    json.dumps({"ba3_original_cli_executed":True,"no_ga2_ma2_fallback":True}))
                (out/"BA3_HANDOFF_FROM_MA3.json").write_text("{}")
                (out/"BETANALYZER_3_REPORT.md").write_text("SYNTHETIC TEST")
                (out/"INTEGRATION_MANIFEST.json").write_text(
                    json.dumps({"engines_modified":False,"file_sha256":{
                        name:"0"*64 for name in (
                            "BA3_HANDOFF_FROM_MA3.json","BETANALYZER_3_ANALYSIS.json",
                            "BETANALYZER_3_REPORT.md","INTEGRATION_AUDIT.json")}}))
                return SimpleNamespace(returncode=0)
            s=run(args(d),launch=fake_launch)
            self.assertEqual(s["reason_code"],"NATIVE_MANIFEST_MISMATCH")
            self.assertFalse(s["operational_eligible"])

    def test_metadata_filters(self):
        with tempfile.TemporaryDirectory() as d:
            fixture(d,"one")
            self.assertEqual(locate("FULL ANALYSIS: Inter - Juventus",d,competition="serie a")["match"]["competition"],"Serie A")
            for kwargs in ({"event_id":"wrong"},{"competition":"Premier League"},{"kickoff":"2026-11-03T20:00:00+00:00"}):
                with self.assertRaises(FullAnalysisBlocked):locate("FULL ANALYSIS: Inter - Juventus",d,**kwargs)


# This replays the full five-motor native chain on a strictly SYNTHETIC
# existing MA3 example, in the installed GitHub CI environment only.
class NativeSyntheticFullAnalysisTest(unittest.TestCase):
    def test_single_command_native_end_to_end(self):
        import os
        import shutil
        work = os.environ.get("BETPILOT_UPSTREAM_WORKDIR")
        if not work:
            self.skipTest("Frozen native sources not available outside V3 CI")
        source = (Path(work) / "BETPILOT_MARKET_ANALYZER_3_0_NATIVE_RC2" /
                  "examples" / "native_synthetic")
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix="ba3_full_cmd_") as td:
            native_root = Path(td) / "sources"
            bundle = native_root / "single_fixture"
            bundle.mkdir(parents=True)
            for name in REQUIRED:
                shutil.copy2(source / name, bundle / name)
            match = json.loads((bundle / "match.json").read_text("utf8"))
            a = SimpleNamespace(request="FULL ANALYSIS: " + match["home"] + " - " + match["away"],
                 sources_root=native_root, repo_root=repo, output_dir=Path(td)/"out",
                 competition=match["competition"], kickoff=match["kickoff"], event_id=match["event_id"])
            result = run(a)
            self.assertEqual(result["status"], "RESEARCH_REPORT_AVAILABLE", result)
            self.assertFalse(result["operational_eligible"])
            self.assertTrue(result["full_integration_verified_on_supplied_inputs"])
            self.assertEqual(result["decision"], "NO_BET_INSUFFICIENT_MARKET_EVIDENCE")
            self.assertTrue((Path(td)/"out"/"NATIVE_CHAIN"/"BA3"/"INTEGRATION_AUDIT.json").is_file())

if __name__=="__main__":
    unittest.main()
