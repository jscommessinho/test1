#!/usr/bin/env python3
"""Worldwide PitchAPI finished-match snapshots. Provider-internal QA only.

This collector is independent of, and does not modify, any BETPILOT analysis
engine. It preserves raw fixture, shot and team-stat responses in per-day
compressed GitHub snapshots. Incomplete matches are retried on later cycles.
"""
from __future__ import annotations
import argparse
import gzip
import json
import os
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from betpilot.pitchapi_gts_probe import (
    Client, ProbeError, ident, parse_shots, parse_match_stats, _fixture_on_utc_day
)
from betpilot.gts_pitchapi_quality import assess_pitchapi_match
from betpilot.pitchapi_history_archive import is_retryable_provider_error

SCHEMA = "BETPILOT_PITCHAPI_WORLD_DAILY_v1"
COMPLETE = ("PASS_INTERNAL", "WARNING_INTERNAL_DELTA")


def read_day(path: Path, day: str) -> dict:
    if not path.exists():
        return {"schema": SCHEMA, "date_utc": day, "matches": {}}
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        obj = json.load(stream)
    if obj.get("schema") != SCHEMA or obj.get("date_utc") != day:
        raise ValueError("Daily source archive identity mismatch")
    if not isinstance(obj.get("matches"), dict):
        raise ValueError("Daily matches partition invalid")
    return obj


def save_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as tmp:
        json.dump(obj, tmp, ensure_ascii=False, indent=2, allow_nan=False)
        tmp.write("\n")
        name = tmp.name
    os.replace(name, path)


def save_day(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as tmp:
        name = tmp.name
        with gzip.GzipFile(fileobj=tmp, mode="wb", mtime=0, compresslevel=8) as gz:
            gz.write(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False).encode("utf-8"))
    os.replace(name, path)


def fixture_row(m: dict, day: str) -> dict:
    mid = ident(m["id"], "m")
    if m.get("status") != "finished" or not _fixture_on_utc_day(m, day):
        raise ValueError("Match not finished on requested UTC date")
    home, away = m["home_team"], m["away_team"]
    ht, at = ident(home["id"], "t"), ident(away["id"], "t")
    if ht == at:
        raise ValueError("Same team on both sides")
    a, b = m.get("score_home"), m.get("score_away")
    if any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 30
           for v in (a, b)):
        raise ValueError("Final goals missing or invalid")
    return {"id": mid, "date": day, "home_team": home, "away_team": away}


def classify_failure(exc: Exception) -> str:
    msg = str(exc)
    if any(term in msg.lower() for term in
           ("missing", "unavailable", "empty", "no xg", "not found",
            "http status 404", "insufficient", "internal reference", "internal_reference_unavailable")):
        return "INCOMPLETE_SOURCE"
    return "STRUCTURAL_ANOMALY"


def collect_match(client: Client, m: dict, day: str) -> dict:
    result = {"fixture": m, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
              "qa_external": "OFFICIAL_REFERENCE_NOT_VERIFIED"}
    try:
        row = fixture_row(m, day)
        mid = row["id"]
        # Keep each provider response, even if subsequent validation is unsuccessful.
        for kind in ("shots", "stats"):
            payload = client.get("/v1/matches/" + mid + "/" + kind)
            result[kind + "_raw"] = payload
        shots = parse_shots(result["shots_raw"], row)
        stats = parse_match_stats(result["stats_raw"], row)
        h, a = row["home_team"]["id"], row["away_team"]["id"]
        metric = {"match_id": mid, "date": day,
                  "home": {"id": h, **shots[h]},
                  "away": {"id": a, **shots[a]},
                  "team_stats_xg": stats}
        qa = assess_pitchapi_match(metric)
        status = qa["internal_pitchapi_status"]
        if status == "PASS":
            result["status"] = "PASS_INTERNAL"
        elif status == "REJECTED_INTERNAL_ABOVE_0_50":
            result["status"] = "WARNING_INTERNAL_DELTA"
        else:
            result["status"] = "INCOMPLETE_SOURCE"
        result["internal_quality"] = qa
        result["home_shots"] = shots[h]
        result["away_shots"] = shots[a]
        result["team_stats_xg"] = stats
    except (ProbeError, ValueError, KeyError, TypeError, AssertionError) as exc:
        if isinstance(exc, ProbeError) and is_retryable_provider_error(exc):
            raise
        result["status"] = classify_failure(exc)
        result["reason"] = str(exc)[:170]
    return result


def run(today: date, lookback: int, request_limit: int, root: Path) -> dict:
    api_key = os.environ.get("PITCHAPI_API_KEY", "")
    if not api_key:
        raise ValueError("PITCHAPI_API_KEY not configured")
    if not 1 <= lookback <= 21 or not 30 <= request_limit <= 400:
        raise ValueError("Unsafe lookback or API call limit")
    client = Client(api_key, budget=request_limit, pause=.4)
    catalogue = client.get("/v1/leagues").get("leagues")
    if not isinstance(catalogue, list) or len(catalogue) < 50:
        raise ValueError("Source league catalogue unavailable or implausibly small")
    league_ids = {ident(x["id"], "l") for x in catalogue}
    seen = set()
    stats = Counter()
    interruption = None
    days_seen = []
    for offset in range(lookback):
        day = (today - timedelta(days=offset)).isoformat()
        path = root / "daily" / day[:4] / day[5:7] / (day + ".json.gz")
        archive = read_day(path, day)
        # Date-based worldwide feed avoids 77 separate league queries every cycle.
        if client.used >= client.budget:
            interruption = "LOCAL_REQUEST_BUDGET"
            break
        try:
            listing = client.get("/v1/date/" + day, {"status": "all"})
            matches = listing.get("matches")
            if not isinstance(matches, list):
                raise ValueError("PitchAPI date listing lacks match array")
        except (ProbeError, ValueError) as exc:
            interruption = str(exc)[:150]
            break
        candidates = []
        for m in matches:
            if not isinstance(m, dict) or m.get("status") != "finished":
                continue
            try:
                mid = ident(m["id"], "m")
                fixture_row(m, day)
                lid = m.get("league_id") or (
                    m.get("league", {}).get("id") if isinstance(m.get("league"), dict) else None
                )
                if lid is not None and lid not in league_ids:
                    stats["unexpected_league_id"] += 1
                    # Keep source fixture for review rather than silently discarding.
                seen.add(lid or "LEAGUE_NOT_EXPOSED_BY_DATE_ENDPOINT")
                if mid in archive["matches"] and archive["matches"][mid].get("status") in COMPLETE:
                    continue
                candidates.append((mid, m))
            except (ProbeError, ValueError, KeyError, TypeError) as exc:
                stats["invalid_finished_fixture"] += 1
        candidates.sort(key=lambda pair: pair[0])
        for mid, m in candidates:
            if client.used + 2 > client.budget:
                interruption = "LOCAL_REQUEST_BUDGET"
                break
            try:
                item = collect_match(client, m, day)
                archive["matches"][mid] = item
                stats[item["status"]] += 1
            except ProbeError as exc:
                interruption = str(exc)[:150]
                break
        archive["last_checked_utc"] = datetime.now(timezone.utc).isoformat()
        archive["finished_listed_at_last_check"] = sum(
            isinstance(x, dict) and x.get("status") == "finished" for x in matches
        )
        save_day(path, archive)
        days_seen.append(day)
        if interruption:
            break
    report = {
        "schema": SCHEMA + "_AUDIT", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "today_utc": today.isoformat(), "days_checked": days_seen,
        "published_catalogue_leagues": len(league_ids), "league_ids_seen_in_date_feeds": sorted(seen),
        "attempts_and_diagnostics": dict(stats), "requests_used": client.used,
        "interruption": interruption, "external_xg_verified": False,
        "frozen_analysis_engines_modified": False,
        "source": "PitchAPI /v1/date, /v1/matches/*/shots, /v1/matches/*/stats",
        "storage": "Private GitHub data branch, per-day gzip; no extra paid provider requested"
    }
    save_json(root / "reports" / "daily_latest.json", report)
    print("PITCHAPI_WORLD_DAILY " + json.dumps({
        "dates": len(days_seen), "catalogue": len(league_ids),
        "requests": client.used, "diagnostics": dict(stats),
        "interruption": interruption
    }, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--lookback", type=int, default=7)
    parser.add_argument("--max-requests", type=int, default=350)
    parser.add_argument("--output-root", type=Path, default=Path("warehouse"))
    args = parser.parse_args()
    try:
        report = run(date.fromisoformat(args.today), args.lookback,
                     args.max_requests, args.output_root)
        return 0 if report["interruption"] in (None, "LOCAL_REQUEST_BUDGET") else 2
    except (ValueError, ProbeError, OSError) as exc:
        save_json(args.output_root / "reports" / "daily_failed.json",
                  {"status": "ABSTAIN", "reason": str(exc)[:180],
                   "timestamp_utc": datetime.now(timezone.utc).isoformat()})
        print("PITCHAPI_WORLD_DAILY_ABSTAIN " + str(exc)[:180])
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
