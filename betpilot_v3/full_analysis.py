"""One-command, fail-closed V3 match selection and native orchestration.

Input bundles are produced by external authorized PitchAPI/Bet365+native
GA3/GTS3 pipelines. This layer does NOT collect data, fit models or place bets.
It calls the already verified run_verified_inputs.py for exactly one fixture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "BETPILOT_FULL_ANALYSIS_V3_CHECKPOINT_V1"
REQUIRED = (
    "match.json", "pitchapi_identity.json", "ga3_native_output.json",
    "gts3_native_output.json", "GTS3_FROZEN_MODEL_PARAMS.json", "RC15_EVENT_INPUT.json",
)
FLAGS = (
    ("match", "match.json"), ("identity", "pitchapi_identity.json"),
    ("ga3-native", "ga3_native_output.json"), ("gts3-native", "gts3_native_output.json"),
    ("gts3-parameters", "GTS3_FROZEN_MODEL_PARAMS.json"),
    ("rc15-input", "RC15_EVENT_INPUT.json"),
)

class FullAnalysisBlocked(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

def timestamp(value):
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if t.utcoffset() is None:
            raise ValueError("missing timezone")
        return t.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError) as e:
        raise FullAnalysisBlocked("INVALID_TIMESTAMP", str(value)) from e

def normalized(value):
    return " ".join(value.split()).casefold()

def parse_request(value):
    if not isinstance(value, str):
        raise FullAnalysisBlocked("INVALID_REQUEST", "Text request required")
    m = re.fullmatch(r"\s*(?:FULL\s+ANALYSIS|BETPILOT)\s*:\s*(.+?)\s+[\-–—]\s+(.+?)\s*", value, re.I)
    if not m or normalized(m[1]) == normalized(m[2]):
        raise FullAnalysisBlocked("INVALID_REQUEST", "Use BETPILOT: Home - Away")
    return m[1].strip(), m[2].strip()

def read_json(path):
    def pairs(items):
        data = {}
        for key, value in items:
            if key in data:
                raise FullAnalysisBlocked("DUPLICATE_JSON_KEY", path.name)
            data[key] = value
        return data
    try:
        data = json.loads(path.read_text("utf8"), object_pairs_hook=pairs,
                          parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as e:
        if isinstance(e, FullAnalysisBlocked):
            raise
        raise FullAnalysisBlocked("INVALID_SOURCE_JSON", path.name + ": " + str(e)) from e
    if not isinstance(data, dict):
        raise FullAnalysisBlocked("INVALID_SOURCE_JSON", path.name + " not an object")
    return data

def locate(request, root, *, competition=None, kickoff=None, event_id=None):
    home, away = parse_request(request)
    root = Path(root).resolve()
    if not root.is_dir():
        raise FullAnalysisBlocked("SOURCE_ROOT_MISSING", "Source-bundle root missing")
    choices = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if child.is_symlink() or not child.is_dir() or child.resolve().parent != root:
            continue
        match_file = child / "match.json"
        if not match_file.is_file() or match_file.is_symlink():
            continue
        match = read_json(match_file)
        keys = ("event_id", "home", "away", "competition", "kickoff", "cutoff")
        if any(not isinstance(match.get(k), str) or not match[k].strip() for k in keys):
            raise FullAnalysisBlocked("INVALID_MATCH", "Incomplete identity: " + child.name)
        if normalized(match["home"]) == normalized(match["away"]) or timestamp(match["cutoff"]) >= timestamp(match["kickoff"]):
            raise FullAnalysisBlocked("INVALID_MATCH", "Inconsistent kickoff/cutoff or teams")
        if (normalized(match["home"]), normalized(match["away"])) != (normalized(home), normalized(away)):
            continue
        if competition is not None and normalized(match["competition"]) != normalized(competition):
            continue
        if event_id is not None and match["event_id"] != event_id:
            continue
        if kickoff is not None and timestamp(match["kickoff"]) != timestamp(kickoff):
            continue
        choices.append((child, match))
    if not choices:
        raise FullAnalysisBlocked("MATCH_NOT_FOUND", "No exact fixture with validated native input exports")
    if len(choices) > 1:
        raise FullAnalysisBlocked("AMBIGUOUS_MATCH", "Multiple matches: specify --event-id or --kickoff")
    directory, match = choices[0]
    missing = [n for n in REQUIRED if not (directory / n).is_file()]
    if missing:
        raise FullAnalysisBlocked("MISSING_NATIVE_OUTPUT", "Missing " + ", ".join(missing))
    hashes = {}
    for name in REQUIRED:
        path = directory / name
        if path.is_symlink() or path.resolve().parent != directory.resolve():
            raise FullAnalysisBlocked("UNSAFE_SOURCE_PATH", "Native source symlink/escape forbidden")
        read_json(path)
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"directory": directory, "match": match, "sha256": hashes}

def write_status(directory, status):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "FULL_ANALYSIS_STATUS.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", "utf8")

def run(args, *, launch=subprocess.run):
    out = Path(args.output_dir).resolve()
    status = {"schema": SCHEMA, "mode": "RESEARCH_ONLY", "operational_eligible": False,
              "betting_execution": False, "source_provenance_upgraded": False}
    try:
        selected = locate(args.request, args.sources_root, competition=args.competition,
                          kickoff=args.kickoff, event_id=args.event_id)
        status.update(status="MATCH_IDENTIFIED", match=selected["match"],
                      native_source_sha256=selected["sha256"])
        write_status(out, status)
        repo = Path(args.repo_root).resolve()
        runner = repo / "betpilot_v3" / "run_verified_inputs.py"
        if not runner.is_file():
            raise FullAnalysisBlocked("NATIVE_RUNNER_UNAVAILABLE", "V3 native runner missing")
        cmd = [sys.executable, str(runner), "--repo-root", str(repo)]
        for arg, filename in FLAGS:
            cmd.extend(("--" + arg, str(selected["directory"] / filename)))
        cmd.extend(("--output-dir", str(out / "NATIVE_CHAIN")))
        try:
            result = launch(cmd, cwd=repo, check=False, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired as e:
            raise FullAnalysisBlocked("NATIVE_CHAIN_TIMEOUT", "Native chain exceeded 600 seconds") from e
        except (OSError, subprocess.SubprocessError) as e:
            raise FullAnalysisBlocked("NATIVE_LAUNCH_FAILED", type(e).__name__) from e
        if result.returncode:
            raise FullAnalysisBlocked("NATIVE_CHAIN_FAILED", "Native analyzer rejected source inputs")
        native = out / "NATIVE_CHAIN" / "BA3"
        try:
            decision = read_json(native / "BETANALYZER_3_ANALYSIS.json")
            audit = read_json(native / "INTEGRATION_AUDIT.json")
            manifest = read_json(native / "INTEGRATION_MANIFEST.json")
        except FullAnalysisBlocked as e:
            raise FullAnalysisBlocked("NATIVE_OUTPUT_INVALID", e.code + ": " + str(e)) from e
        if manifest.get("engines_modified") is not False or not isinstance(manifest.get("file_sha256"), dict):
            raise FullAnalysisBlocked("NATIVE_MANIFEST_INVALID", "Missing or inconsistent integration manifest")
        expected = ("BA3_HANDOFF_FROM_MA3.json", "BETANALYZER_3_ANALYSIS.json",
                    "BETANALYZER_3_REPORT.md", "INTEGRATION_AUDIT.json")
        if set(manifest["file_sha256"]) != set(expected):
            raise FullAnalysisBlocked("NATIVE_MANIFEST_INVALID", "Unexpected native manifest entries")
        for name in expected:
            digest = manifest["file_sha256"][name]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise FullAnalysisBlocked("NATIVE_MANIFEST_INVALID", "Invalid SHA-256 for " + name)
            try:
                actual = hashlib.sha256((native / name).read_bytes()).hexdigest()
            except OSError as e:
                raise FullAnalysisBlocked("NATIVE_OUTPUT_INVALID", "Cannot read " + name) from e
            if digest != actual:
                raise FullAnalysisBlocked("NATIVE_MANIFEST_MISMATCH", name)
        if (decision.get("operational_eligible") is not False or
            decision.get("research_only") is not True or
            audit.get("ba3_original_cli_executed") is not True or
            audit.get("no_ga2_ma2_fallback") is not True):
            raise FullAnalysisBlocked("UNVERIFIED_NATIVE_AUDIT", "Native chain audit invalid")
        status.update(status="RESEARCH_REPORT_AVAILABLE", decision=decision.get("decision"),
                      native_output="NATIVE_CHAIN/BA3/BETANALYZER_3_ANALYSIS.json",
                      full_integration_verified_on_supplied_inputs=True,
                      model_oos_validated=False, quote_capture_independently_verified=False)
        write_status(out, status)
        return status
    except FullAnalysisBlocked as e:
        status.update(status="NO_BET_DATA_OR_INTEGRATION_BLOCKED", reason_code=e.code,
                      reason=str(e), full_integration_verified_on_supplied_inputs=False)
        write_status(out, status)
        return status

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--request", required=True, help="FULL ANALYSIS: Home - Away")
    p.add_argument("--sources-root", required=True, type=Path)
    p.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument("--competition")
    p.add_argument("--kickoff")
    p.add_argument("--event-id")
    args = p.parse_args(argv)
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "RESEARCH_REPORT_AVAILABLE" else 2

if __name__ == "__main__":
    raise SystemExit(main())
