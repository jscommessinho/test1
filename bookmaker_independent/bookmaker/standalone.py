"""Bookmaker RC15 + Verdetto C standalone; never invokes Step5 or any other Analyzer."""
from __future__ import annotations
import csv
import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path

from .verdict_c import canonical_competition, reference_mismatches, mask_historical_text

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine_rc15"
SEVEN = ("q1", "qx", "q2", "qo", "qu", "qgg", "qng")
OPTIONAL = ("over15", "under15", "over35", "under35", "ah_line_home", "ah_home_odds", "ah_away_odds")
FIELDS = ("bookmaker", "match_day", "analysis_kind", "league", "home", "away") + SEVEN + OPTIONAL


class BookmakerError(ValueError):
    pass


def digest(file):
    return hashlib.sha256(Path(file).read_bytes()).hexdigest()


def date_with_zone(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.utcoffset() is None:
            raise ValueError("Missing timezone")
        return d
    except (TypeError, ValueError):
        raise BookmakerError("Timestamp must include timezone") from None


def validate_payload(payload):
    if not isinstance(payload, dict):
        raise BookmakerError("Expected RC15_EVENT_INPUT JSON object")
    match, odds, snapshot = payload.get("match"), payload.get("odds"), payload.get("odds_snapshot")
    if not all(isinstance(v, dict) for v in (match, odds, snapshot)):
        raise BookmakerError("match, odds and odds_snapshot required")
    if not all(isinstance(match.get(k), str) and match[k].strip() for k in
               ("event_id", "home", "away", "competition", "kickoff", "cutoff")):
        raise BookmakerError("Incomplete match identity")
    if match["home"].strip().casefold() == match["away"].strip().casefold():
        raise BookmakerError("Identical teams")
    if date_with_zone(match["cutoff"]) >= date_with_zone(match["kickoff"]):
        raise BookmakerError("Prematch cutoff is not before kickoff")
    if snapshot.get("bookmaker") != "Bet365":
        raise BookmakerError("Only Bet365 source accepted")
    if snapshot.get("provenance") not in ("SYNTHETIC", "USER_SUPPLIED_NOT_TIMESTAMP_VERIFIED", "VERIFIED_EXTERNAL_SNAPSHOT"):
        raise BookmakerError("Invalid odds provenance")
    if date_with_zone(snapshot.get("timestamp")) > date_with_zone(match["cutoff"]):
        raise BookmakerError("Quote timestamp exceeds prematch cutoff")
    if snapshot["provenance"] == "VERIFIED_EXTERNAL_SNAPSHOT" and not snapshot.get("external_capture_reference"):
        raise BookmakerError("Authenticated provider capture reference missing")
    if set(odds) - set(SEVEN + OPTIONAL):
        raise BookmakerError("Unknown odds field")
    if any(odds.get(k) is None for k in SEVEN):
        raise BookmakerError("All seven Bet365 quotes are required")
    for key, val in odds.items():
        if val is None:
            continue
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            raise BookmakerError("Non-finite odds")
        if key != "ah_line_home" and not 1 < val < 100000:
            raise BookmakerError("Invalid decimal quote: " + key)
    for optional in (("over15", "under15"), ("over35", "under35"),
                     ("ah_line_home", "ah_home_odds", "ah_away_odds")):
        n = sum(odds.get(k) is not None for k in optional)
        if n and n != len(optional):
            raise BookmakerError("Incomplete optional market " + str(optional))
    return payload


def analyse_row(row, output_dir, provenance=None):
    """Invoke ORIGINAL RC15 only, then non-invasive Verdetto C C01/C02."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if row.get("bookmaker") != "Bet365":
        raise BookmakerError("Only Bet365 accepted")
    if not all(str(row.get(k, "")).strip() for k in ("league", "home", "away", *SEVEN)):
        raise BookmakerError("RC15 required fields missing")
    engine_row = dict(row, league=canonical_competition(row["league"]))
    in_file = out / "RC15_native_input.csv"
    with in_file.open("w", newline="", encoding="utf8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow({k: engine_row.get(k, "") for k in FIELDS})
    if str(ENGINE) not in sys.path:
        sys.path.insert(0, str(ENGINE))
    from rc15_intelligence import run
    report_dir = out / "rc15_native"
    manifest = run(in_file, report_dir)
    analysis_file = report_dir / "RC15_Analysis.json"
    analysis = json.loads(analysis_file.read_text("utf8"))
    if manifest["analyzed"] != 1 or manifest["rejected"] != 0 or len(analysis["reports"]) != 1:
        raise BookmakerError("Original RC15 rejected event: " + str(analysis.get("errors")))
    report = analysis["reports"][0]
    identity = report["identity"]
    if any(identity[k] != v for k, v in
           (("home", row["home"]), ("away", row["away"]), ("league", engine_row["league"]))):
        raise BookmakerError("RC15 reported a different match")
    original_calibration = json.loads((ENGINE / "RC12_Fitted_Calibrators.json").read_text("utf8"))
    mapping = {"source": row["league"], "engine": engine_row["league"],
               "applied": row["league"] != engine_row["league"],
               "preserved_external_event_identity": True}
    report["data_quality"]["verdict_c_competition_mapping"] = mapping
    mismatches = reference_mismatches(report, original_calibration)
    analysis_file.write_text(json.dumps(analysis, indent=2, ensure_ascii=False, allow_nan=False) + "\n", "utf8")
    mask_historical_text(report_dir, mismatches)
    audit = {"schema": "BETPILOT_BOOKMAKER_VERDICT_C_V1",
             "source": "unmodified RC15, RC12 and BISE; independent invocation, NO STEP5",
             "competition_mapping": mapping, "unavailable_reference_events": mismatches,
             "coefficients_changed": False, "historical_recalibration_claimed": False,
             "prospective_prediction_validated": False,
             "external_data_provenance": provenance or {"source": "manual", "timestamp_verified": False}}
    (out / "BOOKMAKER_VERDICT_C_AUDIT.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False, allow_nan=False) + "\n", "utf8")
    result = {"status": "BOOKMAKER_RC15_VERDETTO_C_RESEARCH_ONLY",
              "engine": "Bookmaker Analyzer 2.0 RC15 + Verdetto C",
              "independent": True, "other_analyzers_executed": [],
              "requested_identity": {"home": row["home"], "away": row["away"],
                                     "competition": row["league"], "match_day": row.get("match_day")},
              "report": report}
    (out / "BOOKMAKER_ANALYSIS.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf8")
    hashes = {str(p.relative_to(out)): digest(p) for p in out.rglob("*")
              if p.is_file() and p.name != "RUN_MANIFEST.json"}
    (out / "RUN_MANIFEST.json").write_text(json.dumps(
        {"schema": "BOOKMAKER_INDEPENDENT_MANIFEST_V1", "sha256": hashes,
         "engines_executed": ["RC15", "Verdetto C"], "frozen_models_modified": False},
        indent=2, ensure_ascii=False) + "\n", "utf8")
    return result


def from_payload(payload, out):
    validate_payload(payload)
    match, snapshot, odds = payload["match"], payload["odds_snapshot"], payload["odds"]
    row = {"bookmaker": "Bet365", "match_day": date_with_zone(match["kickoff"]).date().isoformat(),
           "analysis_kind": "synthetic_integration_test" if snapshot["provenance"] == "SYNTHETIC"
                            else "prematch_user_supplied",
           "league": match["competition"], "home": match["home"], "away": match["away"]}
    row.update(odds)
    result = analyse_row(row, out, provenance={
        "source": "API-Football/Bet365" if snapshot["provenance"] == "VERIFIED_EXTERNAL_SNAPSHOT"
                  else snapshot["provenance"],
        "snapshot_time": snapshot["timestamp"], "fixture_id": match["event_id"],
        "provider_capture_reference": snapshot.get("external_capture_reference"),
        "bet365_direct_timestamp_verified": False})
    path = Path(out) / "RC15_EVENT_INPUT.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf8")
    mp = Path(out) / "RUN_MANIFEST.json"
    mani = json.loads(mp.read_text("utf8"))
    mani["sha256"]["RC15_EVENT_INPUT.json"] = digest(path)
    mp.write_text(json.dumps(mani, indent=2, ensure_ascii=False) + "\n", "utf8")
    return result


def from_csv(csv_file, out):
    with Path(csv_file).open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if any(fld not in (reader.fieldnames or []) for fld in ("bookmaker", "league", "home", "away", *SEVEN)):
            raise BookmakerError("Input CSV missing seven odds or team identity")
        rows = list(reader)
    if len(rows) != 1:
        raise BookmakerError("Standalone single-match CSV requires exactly one row")
    return analyse_row(rows[0], out)
