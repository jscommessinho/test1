"""BETPILOT: Home - Away -- no web search, no manually prepared per-match JSON.

This launcher uses the ORIGINAL pinned GA3/GTS3/RC15/MA3/BA3 pipeline.
It first looks for previously verified native fixture bundles; otherwise it
uses the already-implemented authenticated PitchAPI + API-Sports bridge when
persistent warehouse/history, credentials and the event day are available.
The complete five-engine process is never claimed without a real, matching
source bundle and native engine execution. No betting instructions are issued.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from betpilot_v3.full_analysis import FullAnalysisBlocked, parse_request

SCHEMA = "BETPILOT_ONE_COMMAND_RECOVERY_V1"


def _redact_provider_secrets(value):
    """Never echo authorized provider credentials in persisted/user-facing output."""
    secrets = [os.environ.get(k, "") for k in ("PITCHAPI_API_KEY", "APISPORTS_KEY")]
    secrets = [v for v in secrets if isinstance(v, str) and len(v) >= 8]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED_PROVIDER_SECRET]")
        return value
    if isinstance(value, dict):
        return {k: _redact_provider_secrets(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_provider_secrets(v) for v in value]
    if isinstance(value, tuple):
        return [_redact_provider_secrets(v) for v in value]
    return value


def _path(value):
    return Path(value).resolve() if value else None


def _write(out: Path, payload: dict):
    out.mkdir(parents=True, exist_ok=True)
    (out / "BETPILOT_STATUS.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# BETPILOT — Prematch",
        "",
        f"**Richiesta:** {payload['request']}",
        f"**Stato:** {payload['status']}",
        f"**Decisione:** {payload.get('decision') or 'NO BET / NON DISPONIBILE'}",
        f"**Motivo:** {payload.get('reason') or 'Vedi report nativo per dettagli'}",
        f"**Fonte statistiche:** PitchAPI originale (mai altri provider)",
        f"**Quote:** Bet365 via API-Football soltanto se verificate",
        f"**Tempo del comando:** {payload['timing_ms']['total']} ms",
        "",
        "I test software non equivalgono alla validazione predittiva o economica.",
    ]
    (out / "BETPILOT_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fixture_day_from_index(index_path: Path | None, home: str, away: str, now: datetime):
    """Read a locally captured PitchAPI fixture index; never infer event dates."""
    if index_path is None or not index_path.is_file() or index_path.is_symlink():
        return None
    from betpilot_v3.general_fixture import team
    from betpilot_v3.pitchapi_preflight import instant
    content = json.loads(index_path.read_text("utf8"))
    if content.get("provider") != "PitchAPI" or not isinstance(content.get("fixtures"), list):
        raise ValueError("Invalid authenticated PitchAPI fixture index")
    eligible = []
    for row in content["fixtures"]:
        if not isinstance(row, dict):
            continue
        if (team(row.get("home")) == team(home)
                and team(row.get("away")) == team(away)
                and row.get("status") in ("NS", "not_started", "scheduled")):
            when = instant(row["kickoff"])
            if when > now:
                eligible.append((when, row))
    if len(eligible) > 1:
        raise ValueError("Multiple future matches in the authenticated fixture index")
    return eligible[0][0].date().isoformat() if eligible else None


def execute(request, *, output_dir: Path, sources_root: Path | None = None,
            warehouse_root: Path | None = None, history_root: Path | None = None,
            team_stats_root: Path | None = None, fixture_index: Path | None = None,
            day_utc: str | None = None, now: datetime | None = None,
            sqlite_root: Path | None = None,
            native_runner=None, live_runner=None, repo_root: Path | None = None):
    """Run once, with zero hidden retries. Runners injectable for offline tests."""
    start = time.perf_counter()
    staged_sqlite_sources = None
    out = Path(output_dir).resolve()
    result = {
        "schema": SCHEMA, "request": request,
        "status": "NOT_STARTED", "decision": None, "reason": None,
        "reason_code": None, "mode": "RESEARCH_ONLY",
        "betting_execution": False, "model_formulas_changed": False,
        "uses_web_search": False, "sources": ["PitchAPI", "API-Football/Bet365"],
        "five_engines_verified": False, "timing_ms": {},
        "synthetic_input": False, "real_fixture_analysis_confirmed": False,
    }
    try:
        home, away = parse_request(request)
        timestamp = now or datetime.now(timezone.utc)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Current time requires UTC offset")
        # The original final analyzer validates native source identities and all
        # output manifests; the launcher must not bypass those gates.
        native_result = None
        if sources_root and Path(sources_root).is_dir():
            from betpilot_v3.full_analysis import locate, run as native_default
            try:
                selected = locate(request, sources_root)
            except FullAnalysisBlocked as exc:
                if exc.code != "MATCH_NOT_FOUND":
                    raise
            else:
                # Never promote a synthetic replay to a real match analysis.
                native_quote = json.loads((selected["directory"] / "RC15_EVENT_INPUT.json").read_text("utf8"))
                synthetic = (str(selected["match"]["event_id"]).upper().startswith("SYN") or
                             (native_quote.get("odds_snapshot") or {}).get("provenance") == "SYNTHETIC")
                result["synthetic_input"] = synthetic
                native_result = (native_runner or native_default)(SimpleNamespace(
                    request=request, sources_root=sources_root,
                    repo_root=repo_root or Path(__file__).resolve().parents[1],
                    output_dir=out / "native_chain", competition=None,
                    kickoff=None, event_id=None))
        if native_result is not None:
            result.update(mode="VERIFIED_NATIVE_BUNDLE", native_status=native_result.get("status"))
            if native_result.get("status") == "RESEARCH_REPORT_AVAILABLE":
                result.update(status="SYNTHETIC_REPLAY_ONLY" if synthetic else "RESEARCH_REPORT_AVAILABLE",
                              decision=native_result.get("decision"),
                              five_engines_verified=True,
                              real_fixture_analysis_confirmed=not synthetic,
                              native_report="native_chain/NATIVE_CHAIN/BA3/BETANALYZER_3_ANALYSIS.json")
            else:
                result.update(status="NO_BET_DATA_OR_INTEGRATION_BLOCKED",
                              reason=native_result.get("reason", "Native chain blocked"),
                              reason_code=native_result.get("reason_code", "NATIVE_CHAIN_FAILED"))
        else:
            missing = [key for key in ("APISPORTS_KEY", "PITCHAPI_API_KEY")
                       if not os.environ.get(key)]
            if missing:
                result.update(status="AUTHENTICATION_ERROR",
                              reason_code="MISSING_PROVIDER_CREDENTIALS",
                              reason="Credentials unavailable in the authorized runner: " + ", ".join(missing))
            else:
                # A verified read-only original PitchAPI SQLite snapshot can be
                # materialized as ephemeral native *source* inputs. Original
                # model formulas and provider ZIPs are not modified.
                if (sqlite_root and Path(sqlite_root).is_dir() and
                        (not warehouse_root or not history_root or
                         not Path(warehouse_root).is_dir() or
                         not Path(history_root).is_dir())):
                    from betpilot_v3.sqlite_native_export import convert
                    staged_sqlite_sources = tempfile.TemporaryDirectory(prefix='betpilot-pitchapi-')
                    root = Path(staged_sqlite_sources.name)
                    warehouse_root = root / 'warehouse'
                    warehouse_root.mkdir(parents=True)
                    history_root = root / 'history'
                    team_stats_root = root / 'team_stats'
                    audit = convert(sqlite_root, history_root, team_stats_root,
                                    cutoff=timestamp, repo_root=repo_root)
                    result['sqlite_conversion'] = {
                        'status': audit['status'],
                        'history_records': audit['history_records'],
                        'xg_from_team_stats_not_shot_reconstruction': True,
                        'not_historically_captured_prematch': True,
                    }
                if not warehouse_root or not history_root or not (
                        Path(warehouse_root).is_dir() and Path(history_root).is_dir()):
                    # Authentic source still unavailable; do not pretend to have a history archive.
                    sqlite_count = 0
                    if sqlite_root is not None and Path(sqlite_root).is_dir():
                        from betpilot_v3.sqlite_source import _files
                        sqlite_count = sum(1 for _ in _files(Path(sqlite_root)))
                    result.update(status="DATA_UNAVAILABLE",
                                  reason_code=("SQLITE_PRESENT_NATIVE_HISTORY_MISSING" if sqlite_count
                                               else "PERSISTENT_PITCHAPI_ARCHIVES_MISSING"),
                                  reason="Read-only SQLite fixture sources do not replace the original GA3/GTS3 native history archive format",
                                  pitchapi_sqlite_archives_found=sqlite_count)
                else:
                    event_day = day_utc or _fixture_day_from_index(fixture_index, home, away, timestamp)
                    if event_day is None and sqlite_root is not None:
                        from betpilot_v3.sqlite_source import select_fixture
                        indexed = select_fixture(sqlite_root, request, now=timestamp)
                        if indexed is not None:
                            event_day = indexed['kickoff'][:10]
                            date_origin = 'read_only_pitchapi_sqlite'
                    if day_utc:
                        date_origin = "explicit"
                    elif event_day and 'date_origin' not in locals():
                        date_origin = "authenticated_local_index"
                    if not day_utc and not event_day and sqlite_root is not None:
                        date_origin = "sqlite_index_empty"
                    if event_day is None:
                        # Authenticated, bounded API-Sports future-fixture discovery
                        # happens in the native bridge with the same quota client.
                        date_origin = "authenticated_api_sports_autodiscovery"
                    verified_country = None
                    if sqlite_root is not None and Path(sqlite_root).is_dir():
                        from betpilot_v3.sqlite_source import common_competition_country
                        verified_country = common_competition_country(sqlite_root, request)
                    if verified_country:
                        result['country_hint'] = verified_country
                    # Calls the original budget-limited cross-provider source join.
                    from betpilot_v3.real_prematch_bridge import execute as live_default
                    native_result = (live_runner or live_default)(SimpleNamespace(
                        request=request, day_utc=event_day, country_code=verified_country,
                        competition=None, api_league_id=None, api_season=None,
                        api_fixture_id=None, warehouse_root=Path(warehouse_root),
                        history_root=Path(history_root), team_stats_root=team_stats_root,
                        team_stats_archive=None, output_dir=out / "authenticated_native",
                        registry=Path(__file__).resolve().parents[1] /
                                 "data/pitchapi/league_pins_77.json"))
                    result.update(mode="AUTHENTICATED_NATIVE_PIPELINE",
                                  date_utc_checked=event_day,
                                  date_selection=date_origin,
                                  api_source_status=native_result.get("status"))
                    if native_result.get("status") == "PREMATCH_RESEARCH_REPORT_AVAILABLE":
                        result.update(status="RESEARCH_REPORT_AVAILABLE",
                                      decision=native_result.get("decision"), five_engines_verified=True,
                                      real_fixture_analysis_confirmed=True,
                                      native_report="authenticated_native/five_engine_analysis/NATIVE_CHAIN/BA3/BETANALYZER_3_ANALYSIS.json")
                    else:
                        result.update(status="NO_BET_DATA_OR_INTEGRATION_BLOCKED",
                                      reason_code=native_result.get("reason_code", "NATIVE_CHAIN_FAILED"),
                                      reason=native_result.get("reason", "Authentic source unavailable"))

    except (FullAnalysisBlocked, ValueError, OSError, KeyError, TypeError, ImportError) as exc:
        result.update(status="NO_BET_DATA_OR_INTEGRATION_BLOCKED",
                      reason_code=getattr(exc, "code", type(exc).__name__),
                      reason=str(exc)[:500])
    finally:
        result["timing_ms"]["total"] = round((time.perf_counter() - start) * 1000, 2)
        result = _redact_provider_secrets(result)
        _write(out, result)
        if staged_sqlite_sources is not None:
            staged_sqlite_sources.cleanup()
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description="BETPILOT five-analyzer native prematch launcher")
    p.add_argument("request", help='For example: "BETPILOT: Deportivo La Coruña - Osasuna"')
    p.add_argument("--output-dir", type=Path, default=Path("output/betpilot"))
    p.add_argument("--sources-root", type=Path,
                   default=_path(os.getenv("BETPILOT_SOURCES_ROOT")))
    p.add_argument("--warehouse-root", type=Path,
                   default=_path(os.getenv("BETPILOT_WAREHOUSE_ROOT")))
    p.add_argument("--history-root", type=Path,
                   default=_path(os.getenv("BETPILOT_HISTORY_ROOT")))
    p.add_argument("--team-stats-root", type=Path,
                   default=_path(os.getenv("BETPILOT_TEAM_STATS_ROOT")))
    p.add_argument("--fixture-index", type=Path,
                   default=_path(os.getenv("BETPILOT_FIXTURE_INDEX")))
    p.add_argument("--day-utc", help="Optional exact match day if multiple same-team fixtures")
    p.add_argument("--sqlite-root", type=Path,
                   default=_path(os.getenv("BETPILOT_SQLITE_ROOT")),
                   help="Mounted read-only original PitchAPI SQLite archive tree")
    args = p.parse_args(argv)
    result = execute(args.request, output_dir=args.output_dir,
                     sources_root=args.sources_root, warehouse_root=args.warehouse_root,
                     history_root=args.history_root, team_stats_root=args.team_stats_root,
                     fixture_index=args.fixture_index, day_utc=args.day_utc,
                     sqlite_root=args.sqlite_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "RESEARCH_REPORT_AVAILABLE" else 2


if __name__ == "__main__":
    sys.exit(main())
