"""Authenticated odds + frozen five-export join for any supported prematch event.

No synthetic fallback, no changed Analyzer, no stakes. API-Football identity
is retained separately; the transformed RC15 event is permitted only after
an exact checked PitchAPI/Bet365 fixture crosswalk.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from betpilot_v3.pitchapi_preflight import PreflightBlocked, instant, require
from betpilot_v3.general_fixture import (
    load_registry, fixture_from_api, acquire_original_quote, reconcile, pinned
)

SCHEMA = "BETPILOT_V3_EXTERNAL_QUOTE_JOIN_V1"
# Explicit provider-specific identity pin, never fuzzy league name matching.
PINNED_LEAGUES = {
    ("l_0ErfuF", "LaLiga", "ESP"): (140, "Spain La Liga"),
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


def write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True,
                   allow_nan=False) + "\n", encoding="utf8")


def normalized(s):
    # Safe for strict same-label comparison; no assumptions about FC suffixes.
    import unicodedata
    text = unicodedata.normalize("NFKD", str(s).casefold())
    return " ".join("".join(ch for ch in text if not unicodedata.combining(ch)).split())


def align_authenticated_quote(native_match, identity, pitch_target, quote, *,
                              api_league_id, api_season):
    """Preserve the original quote and produce a new auditable V3 RC15 input.

    The provider-specific event identifiers differ. Only a fully matching
    prematch fixture can be rebound; do not silently reinterpret odds.
    """
    require(pitch_target.get("pitchapi_event_id") == native_match.get("event_id"),
            "SOURCE_PITCHAPI_ID_CONFLICT", "PitchAPI event changed")
    require(pitch_target.get("pitchapi_league_id") == identity.get("league_id"),
            "SOURCE_PITCHAPI_LEAGUE_CONFLICT", "PitchAPI league changed")
    pin = (identity["league_id"], native_match["competition"],
           pitch_target.get("country"))
    require(pin in PINNED_LEAGUES, "SOURCE_LEAGUE_NOT_PINNED",
            "No explicitly reviewed cross-provider league mapping")
    expected_id, expected_name = PINNED_LEAGUES[pin]
    require(int(api_league_id) == expected_id, "BOOKMAKER_LEAGUE_CONFLICT",
            "API-Football league ID contradicts exact PitchAPI pin")
    season = identity.get("season")
    require(season == f"{api_season}/{int(api_season)+1}",
            "BOOKMAKER_SEASON_CONFLICT",
            "API-Football season and PitchAPI season disagree")
    quote_match = quote.get("match") or {}
    require(quote_match.get("competition") == f"{expected_name} {api_season}",
            "BOOKMAKER_COMPETITION_CONFLICT",
            "API-Football league/country/season identity differs")
    provider_id = quote_match.get("event_id")
    require(isinstance(provider_id, str) and
            re.fullmatch(r"API-FOOTBALL-fixture-[0-9]{1,12}", provider_id),
            "BOOKMAKER_EVENT_ID_INVALID", "Unrecognized authenticated quote event")
    for side in ("home", "away"):
        require(normalized(quote_match.get(side)) ==
                normalized(native_match.get(side)),
                "BOOKMAKER_TEAM_CONFLICT", side + " team names differ")
    kickoff = instant(native_match["kickoff"])
    require(instant(quote_match["kickoff"]) == kickoff,
            "BOOKMAKER_KICKOFF_CONFLICT",
            "Kickoff differs between independently queried providers")
    cutoff = instant(native_match["cutoff"])
    capture = instant(quote_match["cutoff"])
    snapshot = quote.get("odds_snapshot") or {}
    quote_time = instant(snapshot.get("timestamp"))
    require(snapshot.get("bookmaker") == "Bet365" and
            snapshot.get("provenance") == "VERIFIED_EXTERNAL_SNAPSHOT",
            "BOOKMAKER_PROVENANCE_INVALID",
            "Need authenticated Bet365-labelled API-Football snapshot")
    require(quote_time <= capture <= cutoff < kickoff,
            "BOOKMAKER_CUTOFF_CONFLICT",
            "Quote acquisition was not demonstrably before PitchAPI cutoff")
    require(isinstance(quote.get("odds"), dict) and
            all(isinstance(quote["odds"].get(k), (float, int))
                and not isinstance(quote["odds"][k], bool)
                and 1 < quote["odds"][k] < 100000
                for k in ("q1", "qx", "q2", "qo", "qu", "qgg", "qng")),
            "BOOKMAKER_ODDS_INCOMPLETE", "RC15 requires seven valid prices")
    require(isinstance(snapshot.get("snapshot_id"), str) and
            snapshot["snapshot_id"].startswith("APIFOOTBALL-"),
            "BOOKMAKER_SNAPSHOT_INVALID", "Missing authenticated snapshot ID")
    # Identity is deliberately rebound only in a separate derived copy.
    merged = json.loads(json.dumps(quote))
    merged["match"] = dict(native_match)
    audit = {
        "schema": SCHEMA, "mode": "RESEARCH_ONLY",
        "betting_enabled": False, "independent_fixture_id_crosswalk": {
            "pitchapi_event_id": native_match["event_id"],
            "api_football_event_id": provider_id,
            "pitchapi_league_id": identity["league_id"],
            "api_football_league_id": expected_id,
            "season": season,
            "kickoff": native_match["kickoff"],
            "alignment_basis": "exact teams, pinned leagues, season and kickoff",
            "separate_provider_id_validation": True,
            "bookmaker_direct_capture_verified": False,
        },
        "original_quote_sha256": None,
        "provider_quote_cutoff": quote_match["cutoff"],
        "pitchapi_cutoff": native_match["cutoff"],
        "quote_time": snapshot["timestamp"],
        "provider_quote_snapshot_id": snapshot["snapshot_id"],
        "odds_provenance_preserved": True,
    }
    return merged, audit


def execute(args, *, odds_run=None, client_factory=None,
            native_phase1=None, full_analysis=None):
    """One invocation; secrets remain GitHub runner environment variables."""
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    result = {"schema": SCHEMA, "mode": "RESEARCH_ONLY",
              "operational_eligible": False,
              "betting_enabled": False, "full_analysis_completed": False}
    try:
        require(bool(os.environ.get("APISPORTS_KEY")),
                "APISPORTS_KEY_UNAVAILABLE", "Configured odds key unavailable")
        require(bool(os.environ.get("PITCHAPI_API_KEY")),
                "PITCHAPI_KEY_UNAVAILABLE", "Configured PitchAPI key unavailable")
        require(Path(args.warehouse_root).is_dir() and
                Path(args.history_root).is_dir(),
                "ARCHIVED_SOURCE_MISSING",
                "Read-only daily and historical source directories required")
        from betpilot.quota_smart import QuotaClient, run as quote_fetch
        from betpilot_v3.pitchapi_native_phase1 import execute as phase1
        registry = load_registry(getattr(args, 'registry',
            Path(__file__).resolve().parents[1] / 'data/pitchapi/league_pins_77.json'))
        from betpilot_v3.full_analysis import run as native_run
        from betpilot_v3.full_analysis import parse_request
        home, away = parse_request(args.request)
        # Authentication before source cutoff is essential: later odds cannot
        # be falsely presented as known at an earlier prediction time.
        quote_dir = out / "authenticated_quote"
        if odds_run is None:
            quote_dir.mkdir(parents=True, exist_ok=True)
        client = (client_factory or QuotaClient)(
            os.environ["APISPORTS_KEY"], reserve=20, max_calls=5)
        if odds_run is not None:
            # Compatibility with pre-existing deterministic Phase 1 smoke mocks;
            # production does not use this country-specific path.
            fetched = odds_run(client,
                home=home, away=away, match_date=args.day_utc,
                league_id=args.api_league_id, season=args.api_season,
                cache_dir=out / "quote_cache", output_dir=quote_dir,
                run_native=False)
            result["api_football_billable_calls"] = fetched["requests_this_run"]
            original_quote = read(quote_dir / "RC15_EVENT_INPUT.json")
            evidence = None
        else:
            client.preflight()
            day = args.day_utc
            auto = None
            if day is None:
                from betpilot_v3.fixture_autodiscovery import next_fixture
                auto = next_fixture(client, args.request)
                day = auto['day_utc']
            fixture, row = fixture_from_api(
                client, args.request, day,
                league_id=auto['api_league_id'] if auto else getattr(args, 'api_league_id', None),
                season=auto['api_season'] if auto else getattr(args, 'api_season', None),
                fixture_id=auto['api_fixture_id'] if auto else getattr(args, 'api_fixture_id', None))
            original_quote, evidence = acquire_original_quote(client, fixture, row)
            write(quote_dir / "RC15_EVENT_INPUT.json", original_quote)
            write(quote_dir / "API_FOOTBALL_FIXTURE_EVIDENCE.json", evidence)
            result["api_football_billable_calls"] = client.used
        if odds_run is None:
            result['discovered_fixture_day_utc'] = day
        # Existing original PitchAPI publisher runs only after verified quote
        # capture. The resulting source cutoff is at least as recent as quotes.
        phase_root = out / "phase1"
        phase_args = SimpleNamespace(
            output_dir=phase_root, request=args.request, day_utc=(day if odds_run is None else args.day_utc),
            country_code=getattr(args, 'country_code', None),
            competition=getattr(args, 'competition', None),
            warehouse_root=Path(args.warehouse_root),
            history_root=Path(args.history_root),
            team_stats_archive=getattr(args, 'team_stats_archive', None),
            team_stats_root=getattr(args, 'team_stats_root', None))
        phase = (native_phase1 or phase1)(phase_args)
        result["phase1_status"] = phase.get("status")
        require(phase.get("status") == "PITCHAPI_NATIVE_FIVE_EXPORTS_VERIFIED",
                phase.get("reason_code", "PHASE1_BLOCKED"),
                phase.get("reason", "Original GA3/GTS3 inputs unavailable"))
        native_dir = Path(phase["native_bundle"])
        match = read(native_dir / "match.json")
        identity = read(native_dir / "pitchapi_identity.json")
        # Original per-fixture provenance is in the publisher's source manifest.
        # The original fixture evidence is separately retained by Phase1;
        # identity binding here uses only signed fields, never inferred team IDs.
        if evidence is None:
            # Legacy injected test path, never used by authenticated production.
            target = {
                "pitchapi_event_id": match["event_id"],
                "pitchapi_league_id": identity["league_id"],
                "country": args.country_code,
            }
            aligned, audit = align_authenticated_quote(
                match, identity, target, original_quote,
                api_league_id=args.api_league_id, api_season=args.api_season)
            api_id = audit["independent_fixture_id_crosswalk"]["api_football_event_id"]
        else:
            candidates = [
                (signature, metadata)
                for signature, metadata in registry.items()
                if signature[0] == identity["league_id"]
                and signature[1] == match["competition"]
            ]
            require(len(candidates) == 1, "PITCHAPI_PIN_UNRESOLVED",
                    "Original fixture league not unique in 77-league registry")
            country = candidates[0][0][2]
            require(getattr(args, "country_code", None) in (None, country),
                    "PITCHAPI_COUNTRY_CONFLICT", "Requested country disagrees with original pin")
            target = {
                "pitchapi_event_id": match["event_id"],
                "pitchapi_league_id": identity["league_id"],
                "country": country,
            }
            aligned, audit = reconcile(match, identity, target,
                                        original_quote, evidence, registry)
            api_id = original_quote["match"]["event_id"]
        quote_file = quote_dir / "RC15_EVENT_INPUT.json"
        audit["original_quote_sha256"] = digest(quote_file)
        six_root = out / "six_verified_inputs"
        destination = six_root / "fixture"
        require(not destination.exists(), "INPUT_BUNDLE_EXISTS",
                "Do not overwrite prior research source bundle")
        destination.mkdir(parents=True)
        five_manifest = read(native_dir / "PITCHAPI_NATIVE_MANIFEST.json")
        for name, expected in five_manifest["five_file_sha256"].items():
            source = native_dir / name
            require(source.is_file() and digest(source) == expected,
                    "PHASE1_DIGEST_MISMATCH", "Native file changed: " + name)
            shutil.copyfile(source, destination / name)
        write(destination / "RC15_EVENT_INPUT.json", aligned)
        audit["derived_quote_sha256"] = digest(destination / "RC15_EVENT_INPUT.json")
        write(out / "CROSS_PROVIDER_IDENTITY_AUDIT.json", audit)
        from integrate_native import check_payload
        check_payload(aligned)
        decision = (full_analysis or native_run)(SimpleNamespace(
            request="FULL ANALYSIS: " + match["home"] + " - " + match["away"],
            sources_root=six_root,
            repo_root=Path(__file__).resolve().parents[1],
            output_dir=out / "five_engine_analysis",
            competition=match["competition"],
            kickoff=match["kickoff"], event_id=match["event_id"]))
        require(decision.get("status") == "RESEARCH_REPORT_AVAILABLE",
                decision.get("reason_code", "NATIVE_CHAIN_BLOCKED"),
                decision.get("reason", "Original five-motor chain rejected input"))
        result.update(status="PREMATCH_RESEARCH_REPORT_AVAILABLE",
                      full_analysis_completed=True,
                      decision=decision.get("decision"),
                      betting_enabled=False,
                      operational_eligible=False,
                      pitchapi_event_id=match["event_id"],
                      api_football_event_id=api_id,
                      cross_provider_audit_sha256=digest(
                          out / "CROSS_PROVIDER_IDENTITY_AUDIT.json"))
    except (PreflightBlocked, ValueError, KeyError, TypeError,
            OSError, ImportError, RuntimeError) as exc:
        result.update(status="NO_BET_PREMATCH_CHAIN_BLOCKED",
                      reason_code=getattr(exc, "code", type(exc).__name__),
                      reason=str(exc)[:500])
    write(out / "PREMATCH_V3_EXECUTION_STATUS.json", result)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--request", required=True)
    p.add_argument("--day-utc", required=True,
                   help="UTC fixture date; required to avoid guessing future fixtures")
    p.add_argument("--country-code", help="Optional PitchAPI 3-letter country disambiguator")
    p.add_argument("--competition", help="Optional exact PitchAPI competition")
    p.add_argument("--api-league-id", type=int, help="Optional API league filter")
    p.add_argument("--api-season", type=int, help="Optional season filter")
    p.add_argument("--api-fixture-id", type=int, help="Optional unambiguous API match ID")
    p.add_argument("--registry", type=Path,
                   default=Path(__file__).resolve().parents[1] /
                   "data/pitchapi/league_pins_77.json")
    p.add_argument("--warehouse-root", type=Path, required=True)
    p.add_argument("--history-root", type=Path, required=True)
    p.add_argument("--team-stats-archive", type=Path,
                   help="Existing raw PitchAPI /stats checkpoint (not shots aggregates)")
    p.add_argument("--team-stats-root", type=Path,
                   help="Existing raw /stats archives by pinned 77 league key")
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args(argv)
    result = execute(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PREMATCH_RESEARCH_REPORT_AVAILABLE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
