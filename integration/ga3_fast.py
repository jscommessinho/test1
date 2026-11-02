"""One-command Goal Analyzer 3.0: any archived or future PitchAPI competition.

Uses the UNMODIFIED, SHA-256-pinned original Goal Analyzer RC2.post2 core;
only goals are loaded from local SQLite. No network, no credentials, no DB
modifications, no per-match workflow generation, and no model recalibration.

Examples:
  python -m integration.ga3_fast --warehouse ../pitchapi-warehouse --list-leagues
  python -m integration.ga3_fast --warehouse ../pitchapi-warehouse --league 'Serie A' --list-teams
  python -m integration.ga3_fast --warehouse ../pitchapi-warehouse --league 'Serie A' --home Juventus --away Napoli
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ZIP_SHA256 = "ec449d8305a341f36c29cc1710f3285dcddb94522718de99d50e2fd360281382"
FILE_SHA256 = {
    "goal_analyzer3/__init__.py": "f4ff752422aa8d0bce31d0dc432c13183d4b393b702a1310c6feaada28559f80",
    "goal_analyzer3/data.py": "69cccff5838179a1d10300d268ee2efaba9bf399d53b4a0f2751ae50982d217a",
    "goal_analyzer3/model.py": "aec65a210bb3ef91e8297db90b1794d47231747a88ddf71d96a1e9c93f12614f",
    "goal_analyzer3/probability.py": "dee2455e25782076c773d95896541665714230210e8cb4d3dfeb4f16ae937f20",
}


def pin_original_engine() -> str:
    """Return the verified ZIP bytes; refuse any unapproved engine change."""
    path = Path(__file__).with_name("authentic_ga3_core.b64")
    raw = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
    if hashlib.sha256(raw).hexdigest() != ZIP_SHA256:
        raise RuntimeError("GA3 original engine archive hash mismatch")
    with zipfile.ZipFile(__import__("io").BytesIO(raw)) as archive:
        if set(archive.namelist()) != set(FILE_SHA256):
            raise RuntimeError("GA3 ZIP members differ from approved release")
        for name, digest in FILE_SHA256.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise RuntimeError("GA3 engine source checksum mismatch: " + name)
    return raw


def parse_datetime(value: str, field: str) -> datetime:
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(field + " requires ISO-8601, e.g. 2026-11-05T20:45:00+01:00") from exc
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError(field + " must include a UTC offset or Z")
    return when.astimezone(timezone.utc)


def run(
    *, warehouse: Path, league: str | None = None, home: str | None = None,
    away: str | None = None, kickoff: datetime | None = None,
    as_of: datetime | None = None, list_leagues: bool = False,
    list_teams: bool = False, top: int = 5,
) -> dict:
    start_timer = time.perf_counter()
    # Check engine digest before importing anything that can affect mathematics.
    verified = pin_original_engine()
    with tempfile.TemporaryDirectory(prefix="ga3_engine_") as directory:
        path = Path(directory) / "ga3_engine.zip"
        path.write_bytes(verified)
        sys.path.insert(0, str(path))
        try:
            from integration.ga3_sqlite_adapter import (
                available_competitions, load_from_warehouse, predict,
                IntegrationError,
            )
            from goal_analyzer3.model import ModelConfig
            from integration.derived_markets import from_joint_score_grid
            now = datetime.now(timezone.utc)
            if list_leagues:
                return {"status": "OK", "competitions": available_competitions(warehouse)}
            if not league:
                raise IntegrationError("Specify --league or --list-leagues")
            if list_teams:
                rows, names, quality = load_from_warehouse(warehouse, league, now)
                return {"status": "OK", "teams": [
                    {"team_id": key, "name": value} for key, value in
                    sorted(names.items(), key=lambda x: (x[1].casefold(), x[0]))
                ], "quality": quality}
            if not (home and away):
                raise IntegrationError("--home and --away are required")
            # Normal use: no fixture lookup and no kickoff needed. Use the
            # current verified archive snapshot, with an honest status label.
            if kickoff is not None:
                if kickoff.tzinfo is None or kickoff.utcoffset() is None:
                    raise IntegrationError("Kickoff must have an explicit timezone")
                kickoff = kickoff.astimezone(timezone.utc)
            if as_of is not None:
                if as_of.tzinfo is None or as_of.utcoffset() is None:
                    raise IntegrationError("as_of must have a timezone")
                as_of = as_of.astimezone(timezone.utc)
            elif kickoff is None or now < kickoff:
                as_of = now
            else:
                raise IntegrationError(
                    "Kickoff has passed. For retrospective prematch "
                    "supply an explicit --as-of before the kickoff.")
            if as_of > now:
                raise IntegrationError("as_of cannot be in the future")
            if kickoff is not None and as_of >= kickoff:
                raise IntegrationError("as_of must be strictly before kickoff")
            analysis_kind = (
                ("PREMATCH" if now < kickoff else "RETROSPECTIVE_PREMATCH")
                if kickoff is not None else "CURRENT_DATA_SNAPSHOT"
            )
            config = ModelConfig()  # exact approved goals-only defaults, no user knobs
            if config.xg_blend != 0 or config.min_train != 1:
                raise IntegrationError("Unexpected GA3 release or configuration")
            raw = predict(warehouse, league, home, away, as_of, config)
            inference_ms = round((time.perf_counter() - start_timer) * 1000, 2)
            market = raw["prediction"]
            score_grid = market["exact_scores"]
            likely_scores = sorted(
                [{"score": f"{i}-{j}", "probability": probability}
                 for i, line in enumerate(score_grid)
                 for j, probability in enumerate(line)],
                key=lambda x: x["probability"], reverse=True,
            )[:top]
            probabilities = market["markets"]
            try:
                additional_markets = from_joint_score_grid(score_grid)
            except (ValueError, TypeError) as exc:
                # A malformed score grid must never corrupt the original GA3.
                additional_markets = {
                    "status": "INVALID_JOINT_GRID", "reason": str(exc),
                    "markets": {}, "bookmaker_odds_generated": False,
                    "ga3_lambda_formula_changed": False,
                }
            return {
                "status": "OK",
                "analysis_type": analysis_kind,
                "kickoff_utc": kickoff.isoformat() if kickoff is not None else None,
                "fixture_status_verified": False,
                "data_as_of_utc": as_of.isoformat(),
                "competition_slug": raw["quality"]["league_slug"],
                "competition_pitchapi_id": raw["quality"]["pitchapi_league_id"],
                "teams": raw["team_names"],
                "team_ids": raw["teams"],
                "goals_only": True, "uses_xg": False,
                "training_matches": raw["model"]["train_n"],
                "home_historical_matches": raw["quality"]["per_team_historical_matches"][raw["teams"][0]],
                "away_historical_matches": raw["quality"]["per_team_historical_matches"][raw["teams"][1]],
                "model_config": raw["model"]["config"],
                "expected_goals_from_poisson_not_xg": {
                    "home": market["lambda_home"], "away": market["lambda_away"]},
                "markets": probabilities,
                "additional_markets": additional_markets,
                "most_likely_scores": likely_scores,
                "calibration_or_model_changed": False,
                "prediction_validated": False,
                "point_in_time_publication_verified": False,
                "timing_ms": {
                    "load_and_fit_and_infer": inference_ms,
                    "total": round((time.perf_counter()-start_timer)*1000,2),
                },
            }
        finally:
            sys.path.remove(str(path))
            for name in tuple(sys.modules):
                if name == "goal_analyzer3" or name.startswith("goal_analyzer3."):
                    del sys.modules[name]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Any PitchAPI match; goals-only original GA3; direct local SQLite")
    parser.add_argument("--warehouse", type=Path, required=True,
                        help="Persistent checkout containing data/<league>/<season>.sqlite")
    parser.add_argument("--league", help="Any league slug, provider ID or unique league name")
    parser.add_argument("--home", help="Official team ID or exact team name")
    parser.add_argument("--away", help="Official team ID or exact team name")
    parser.add_argument("--kickoff", help="Optional target kickoff; no calendar lookup required")
    parser.add_argument("--as-of", help="Optional historical snapshot time; normally omitted")
    parser.add_argument("--list-leagues", action="store_true")
    parser.add_argument("--list-teams", action="store_true")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--output", type=Path, help="Optional JSON output filename")
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.top <= 20:
            raise ValueError("--top must be between 1 and 20")
        result = run(
            warehouse=args.warehouse, league=args.league, home=args.home,
            away=args.away, kickoff=parse_datetime(args.kickoff, "kickoff") if args.kickoff else None,
            as_of=parse_datetime(args.as_of, "as_of") if args.as_of else None,
            list_leagues=args.list_leagues, list_teams=args.list_teams, top=args.top)
        output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print("GA3: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
