"""PitchAPI five-league historical backfill and Tuesday/Friday incremental sync.

Only team-level endpoints selected by the project are collected. A finished match
gets summary, team stats, events, lineups, momentum and team advanced analytics.
Shot xG/location and per-player statistics are never requested or stored.

Usage: python -m collector.sync --warehouse ../warehouse --mode full
Requires PITCHAPI_API_KEY in the environment. No hardcoded season cap or fixture cap.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db import pitchapi_store as store

BASE = "https://api.pitchapi.dev"
LEAGUES = {
    ("Premier League", "ENG"): "eng-premier",
    ("Serie A", "ITA"): "ita-seriea",
    ("La Liga", "ESP"): "esp-laliga",
    ("LaLiga", "ESP"): "esp-laliga",
    ("LaLiga EA SPORTS", "ESP"): "esp-laliga",
    ("Primera División", "ESP"): "esp-laliga",
    ("Bundesliga", "GER"): "ger-bundesliga",
    ("Ligue 1", "FRA"): "fra-ligue1",
}
MATCH_DATASETS = ("match_summary", "stats", "events", "lineups", "momentum", "advanced")
STATUS_FINAL = {"finished", "after_extra_time", "after_penalties"}
RETRY_CODES = {429, 500, 502, 503, 504}


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_part(value: str) -> str:
    """Do not allow provider values to alter local file paths."""
    if not value or not all(ch.isalnum() or ch in "-_" for ch in value):
        raise ValueError("Unusable provider season or ID component")
    return value


def season_filename(season: str) -> str:
    # Provider's YYYY/YYYY or YYYY season label, no arbitrary age limit.
    if not season or not all(c.isdigit() or c == "/" for c in season):
        raise ValueError(f"Unexpected season label {season!r}")
    return season.replace("/", "-")


class ProviderError(RuntimeError):
    def __init__(self, code, message, *, status=None):
        super().__init__(f"PitchAPI {status or ''} {code}: {message}")
        self.code = code
        self.status = status


class PitchAPI:
    def __init__(self, key: str, *, interval: float = 0.15, retries: int = 6):
        if not key or not key.strip():
            raise RuntimeError("PITCHAPI_API_KEY is missing in the NEW GitHub repository")
        self.key = key.strip()
        self.interval = max(0.0, interval)
        self.retries = retries
        self.last_call = 0.0
        self.calls = 0

    def get(self, endpoint: str, params: dict | None = None):
        if not endpoint.startswith("/v1/") or "://" in endpoint or ".." in endpoint:
            raise ValueError("Unexpected API path")
        url = BASE + endpoint
        if params:
            url += "?" + urllib.parse.urlencode(params)
        for attempt in range(self.retries + 1):
            elapsed = time.monotonic() - self.last_call
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
            request = urllib.request.Request(
                url, headers={"X-API-KEY": self.key, "Accept": "application/json", "User-Agent": "BETPILOT-Prematch/1.0"},
                method="GET",
            )
            self.last_call = time.monotonic()
            self.calls += 1
            try:
                with urllib.request.urlopen(request, timeout=40) as response:
                    raw = response.read(12_000_000 + 1)
                    if len(raw) > 12_000_000:
                        raise ProviderError("RESPONSE_TOO_LARGE", endpoint)
                    payload = json.loads(raw.decode("utf-8"))
                    if not isinstance(payload, dict) or "data" not in payload:
                        raise ProviderError("BAD_SCHEMA", "Missing data envelope")
                    return raw, payload
            except urllib.error.HTTPError as exc:
                raw = exc.read(4096)
                try:
                    body = json.loads(raw)
                    error = body.get("error", {})
                    code = error.get("code", str(exc.code))
                    msg = error.get("message", "")
                except (ValueError, TypeError, AttributeError):
                    code, msg = str(exc.code), "HTTP response not JSON"
                if exc.code not in RETRY_CODES or attempt >= self.retries:
                    raise ProviderError(code, msg, status=exc.code) from exc
                retry_after = exc.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 2 ** attempt
                except ValueError:
                    delay = 2 ** attempt
                time.sleep(min(120, max(0.5, delay) + random.uniform(0, 0.5)))
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt >= self.retries:
                    raise ProviderError("NETWORK", str(exc)) from exc
                time.sleep(min(60, 2 ** attempt + random.uniform(0, 0.4)))
        raise AssertionError("Retry loop exhausted")


def read_status(path: Path, match: str, dataset: str):
    with store.connect(path) as db:
        item = db.execute(
            """SELECT state,last_checked_at_utc FROM endpoint_coverage
               WHERE dataset=? AND resource_key=?""",
            (dataset, match),
        ).fetchone()
    return item


def mark_unavailable(path: Path, league: str, season: str, dataset: str,
                     match: str, code: str):
    with store.connect(path) as db:
        db.execute(
            """INSERT INTO endpoint_coverage
               (league_slug,season_label,dataset,resource_key,state,last_checked_at_utc,note)
               VALUES (?,?,?,?,'unavailable',?,?)
               ON CONFLICT(league_slug,season_label,dataset,resource_key)
               DO UPDATE SET state='unavailable',last_checked_at_utc=excluded.last_checked_at_utc,
                             note=excluded.note""",
            (league, season, dataset, match, stamp(), code),
        )


def ready_to_refetch(path: Path, match: str, dataset: str,
                     *, recent: bool, unavailable_days: int = 30):
    previous = read_status(path, match, dataset)
    if previous is None:
        return True
    state, date_str = previous
    if recent and state in ("present", "empty"):
        # Match data can settle/change after the final whistle.
        return True
    if state in ("present", "empty"):
        return False
    if state == "unavailable" and date_str:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - dt >= timedelta(days=unavailable_days)
    return True


def finish_status(dbpath: Path, match_id: str):
    with store.connect(dbpath) as db:
        record = db.execute(
            """SELECT fixture_status,home_goals,away_goals FROM fixtures
               WHERE pitchapi_fixture_id=?""",
            (match_id,),
        ).fetchone()
    return bool(record and (record[0] or "").lower() in STATUS_FINAL
                and record[1] is not None and record[2] is not None)


def summarize(dbpath: Path, required: tuple = MATCH_DATASETS):
    with store.connect(dbpath) as db:
        complete = db.execute(
            """SELECT count(*) FROM fixtures WHERE lower(fixture_status) IN
               ('finished','after_extra_time','after_penalties')
               AND home_goals IS NOT NULL AND away_goals IS NOT NULL"""
        ).fetchone()[0]
        accepted = db.execute(
            """SELECT COUNT(*) FROM endpoint_coverage
               WHERE state IN ('present','empty') AND dataset IN
               ('match_summary','stats','events','lineups','momentum','advanced')"""
        ).fetchone()[0]
        unavailable = db.execute(
            "SELECT COUNT(*) FROM endpoint_coverage WHERE state='unavailable'"
        ).fetchone()[0]
        return {"finished_matches": complete, "accepted_endpoint_responses": accepted,
                "unavailable_endpoint_responses": unavailable}


def sync(api: PitchAPI, warehouse: Path, *, max_matches: int = 0,
         only_league: str | None = None, refresh_days: int = 10):
    """Backfill all advertised seasons, then fill missing match endpoints.

    The DB is split per league-season for small durable Git commits. Re-running
    reuses data, refreshes settled recent games, and discovers newly played games.
    """
    warehouse.mkdir(parents=True, exist_ok=True)
    catalog_raw, catalog = api.get("/v1/leagues")
    catalog_db = warehouse / "data" / "catalogue.sqlite"
    store.initialize(catalog_db)
    store.catalogue_from_bytes(catalog_db, catalog_raw)
    items = catalog.get("data", {}).get("leagues", [])
    if not isinstance(items, list):
        raise ProviderError("BAD_CATALOGUE", "Expected data.leagues[]")
    found = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        league = LEAGUES.get((item.get("name"), item.get("country_code")))
        if league is None or (only_league and league != only_league):
            continue
        lid, seasons = item.get("id"), item.get("seasons")
        if not isinstance(lid, str) or not lid.startswith("l_"):
            raise ProviderError("BAD_CATALOGUE", f"Missing provider ID: {league}")
        if not isinstance(seasons, list) or not all(isinstance(s, str) for s in seasons):
            raise ProviderError("BAD_CATALOGUE", f"Missing seasons: {league}")
        found[league] = (lid, seasons)
    expected = set(LEAGUES.values()) if not only_league else {only_league}
    if set(found) != expected:
        spanish_names = sorted({str(x.get("name")) for x in items
                               if isinstance(x, dict) and x.get("country_code") == "ESP"})
        raise ProviderError("INCOMPLETE_CATALOGUE",
                            f"Expected {sorted(expected)}, got {sorted(found)}; ESP catalogue names={spanish_names}")
    report = {"updated_at_utc": stamp(), "seasons": {}, "requests": 0,
              "scope": "five requested leagues; all provider-listed seasons"}
    handled = 0
    recent_limit = datetime.now(timezone.utc) - timedelta(days=refresh_days)
    for league, (lid, seasons) in sorted(found.items()):
        for season in sorted(set(seasons)):
            dbpath = warehouse / "data" / league / (season_filename(season) + ".sqlite")
            store.initialize(dbpath)
            encoded = urllib.parse.quote(lid, safe="_")
            raw, listing = api.get(
                f"/v1/leagues/{encoded}/matches", {"season": season, "status": "played"}
            )
            data = listing.get("data", {})
            matches = data.get("matches") if isinstance(data, dict) else None
            if not isinstance(matches, list):
                raise ProviderError("BAD_LISTING", f"Missing matches array: {league} {season}")
            listed_ids = []
            for row in matches:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                    raise ProviderError("BAD_LISTING", f"Bad match identity {league} {season}")
                listed_ids.append(row["id"])
            if len(listed_ids) != len(set(listed_ids)):
                raise ProviderError("DUPLICATE_MATCH_IDS", f"{league} {season}")
            source_id = store.archive_bytes(
                dbpath, league=league, season=season, dataset="league_matches",
                resource_key="played", endpoint=f"/v1/leagues/{lid}/matches",
                raw=raw,
            )
            projection = store.project_response(dbpath, source_id)
            # Projection count must match source listing, otherwise do not mark complete.
            if projection["projected"] != len(matches):
                raise ProviderError("PARTIAL_LISTING_PROJECTION", f"{league} {season}")
            for match in matches:
                match_id = match["id"]
                if not finish_status(dbpath, match_id):
                    # A match may be postponed/cancelled; never fabricate final stats.
                    continue
                date_value = match.get("time_utc")
                recent = False
                if date_value:
                    try:
                        recent = datetime.fromisoformat(date_value.replace("Z", "+00:00")) >= recent_limit
                    except ValueError:
                        pass
                # Always process missing datasets, even for very old matches.
                for dataset in MATCH_DATASETS:
                    if not ready_to_refetch(dbpath, match_id, dataset, recent=recent):
                        continue
                    endpoint = f"/v1/matches/{urllib.parse.quote(match_id, safe='_')}"
                    if dataset != "match_summary":
                        endpoint += "/" + dataset
                    try:
                        detail_raw, _ = api.get(endpoint)
                    except ProviderError as exc:
                        if exc.status == 404 and exc.code in (
                            "ANALYTICS_UNAVAILABLE", "RESOURCE_NOT_FOUND", "HALVES_UNAVAILABLE"
                        ):
                            mark_unavailable(dbpath, league, season, dataset, match_id, exc.code)
                            continue
                        raise
                    rid = store.archive_bytes(
                        dbpath, league=league, season=season, dataset=dataset,
                        resource_key=match_id, match_id=match_id,
                        endpoint=endpoint, raw=detail_raw,
                    )
                    store.project_response(dbpath, rid)
                handled += 1
                if max_matches and handled >= max_matches:
                    report["seasons"][f"{league}/{season}"] = {
                        "listed": len(matches), **summarize(dbpath)}
                    report["requests"] = api.calls
                    write_report(warehouse, report)
                    return report
            report["seasons"][f"{league}/{season}"] = {
                "listed": len(matches), **summarize(dbpath)}
            report["requests"] = api.calls
            write_report(warehouse, report)
            print(f"[{stamp()}] {league} {season}: {len(matches)} listed, "
                  f"{report['seasons'][f'{league}/{season}']['finished_matches']} stored; "
                  f"total {api.calls} API requests", flush=True)
    write_report(warehouse, report)
    return report


def write_report(warehouse: Path, report: dict):
    path = warehouse / "reports" / "pitchapi-sync.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--max-matches", type=int, default=0,
                        help="0 = all played fixtures in all advertised seasons")
    parser.add_argument("--only-league", choices=sorted(LEAGUES.values()))
    parser.add_argument("--interval", type=float, default=0.15)
    parser.add_argument("--refresh-days", type=int, default=10)
    args = parser.parse_args()
    api = PitchAPI(os.environ.get("PITCHAPI_API_KEY", ""), interval=args.interval)
    try:
        report = sync(api, args.warehouse, max_matches=args.max_matches,
                      only_league=args.only_league, refresh_days=args.refresh_days)
    finally:
        print(f"Total attempted PitchAPI HTTP requests: {api.calls}", flush=True)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
