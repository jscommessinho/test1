"""Offline, source-first PitchAPI storage. No HTTP, API key or automation.

v3 adds gzip-compressed, byte-exact REST payloads and query projections. Seasons
are discovered dynamically. Unknown/new endpoint shapes stay intact in source.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = Path("data/pitchapi.sqlite")
LEAGUE_KEYS = {
    ("Premier League", "ENG"): "eng-premier",
    ("Serie A", "ITA"): "ita-seriea",
    ("Ligue 1", "FRA"): "fra-ligue1",
    ("La Liga", "ESP"): "esp-laliga",
    ("LaLiga", "ESP"): "esp-laliga",
    ("LaLiga EA SPORTS", "ESP"): "esp-laliga",
    ("Primera División", "ESP"): "esp-laliga",
    ("Bundesliga", "GER"): "ger-bundesliga",
}


# User-selected dataset policy: team/match statistics, lineups, chronology.
# No individual shot data, shot locations, or player statistical datasets.
ALLOWED_DATASETS = frozenset({
    "league_matches", "match_summary", "stats", "events",
    "lineups", "momentum", "advanced",
})
DISALLOWED_ENDPOINT_FRAGMENTS = ("/shots", "/players", "/heatmaps", "/pass-network")


def check_dataset(dataset: str, endpoint: str) -> None:
    if dataset not in ALLOWED_DATASETS:
        raise ValueError(f"Dataset {dataset!r} excluded; only approved team/match data is stored")
    if any(fragment in endpoint.lower() for fragment in DISALLOWED_ENDPOINT_FRAGMENTS):
        raise ValueError("Individual player/shot endpoint excluded by database policy")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-16384")  # 16 MiB per connection
    return conn


def initialize(path: Path) -> None:
    """Create/upsert schema and remove only the three explicitly excluded data groups.

    Existing approved legacy responses are copied to the compressed source table
    before the obsolete table is removed. Other repositories are never accessed.
    """
    with connect(path) as conn:
        conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        conn.executescript((ROOT / "optimized_schema.sql").read_text(encoding="utf-8"))
        conn.execute("PRAGMA secure_delete=ON")
        with conn:
            legacy = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_payloads'"
            ).fetchone()
            if legacy:
                for league, season, dataset, key, match, endpoint, fetched, payload in conn.execute(
                    """SELECT league_slug,season_label,dataset,resource_key,fixture_id,
                              endpoint,fetched_at_utc,payload_json FROM raw_payloads"""
                ):
                    try:
                        check_dataset(dataset, endpoint or "offline-import:unspecified")
                    except ValueError:
                        continue
                    raw = payload.encode("utf-8")
                    compressed = gzip.compress(raw, compresslevel=6, mtime=0)
                    conn.execute(
                        """INSERT OR IGNORE INTO source_responses
                           (league_slug,season_label,dataset,resource_key,match_id,
                            endpoint,fetched_at_utc,sha256_hex,original_bytes,
                            compressed_bytes,body_gzip)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (league,season,dataset,key,match,endpoint or "offline-import:unspecified",
                         fetched,hashlib.sha256(raw).hexdigest(),len(raw),len(compressed),compressed),
                    )
            # Remove only records user explicitly excluded from old versions.
            excluded = """dataset NOT IN ('league_matches','match_summary','stats',
                                  'events','lineups','momentum','advanced')
                       OR lower(endpoint) LIKE '%/shots%'
                       OR lower(endpoint) LIKE '%/players%'
                       OR lower(endpoint) LIKE '%/heatmaps%'
                       OR lower(endpoint) LIKE '%/pass-network%'"""
            conn.execute("DROP TABLE IF EXISTS match_shots")
            conn.execute("DROP TABLE IF EXISTS match_player_stats")
            conn.execute("DELETE FROM advanced_metrics WHERE subject_type <> 'team'")
            conn.execute(f"DELETE FROM endpoint_coverage WHERE source_response_id IN (SELECT response_id FROM source_responses WHERE {excluded}) OR dataset NOT IN ('league_matches','match_summary','stats','events','lineups','momentum','advanced')")
            conn.execute(f"DELETE FROM source_responses WHERE {excluded}")
            conn.execute("DROP TABLE IF EXISTS team_metrics")
            old_raw_table = conn.execute(
                "SELECT type FROM sqlite_master WHERE name='raw_payloads'"
            ).fetchone()
            if old_raw_table and old_raw_table[0] == "table":
                conn.execute("DROP TABLE raw_payloads")
            # Read-only zero-row compatibility view for older reporting clients.
            # It is NOT a data table and cannot store any legacy rows.
            conn.execute(
                """CREATE VIEW IF NOT EXISTS raw_payloads AS
                   SELECT league_slug,season_label FROM source_responses WHERE 0"""
            )
            conn.execute("PRAGMA user_version=4")


def _decoded(raw: bytes):
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Expected a UTF-8 JSON response from PitchAPI") from exc
    if not isinstance(payload, (dict, list)):
        raise ValueError("Expected a JSON object or array")
    return payload


def _season(conn: sqlite3.Connection, league: str, season: str) -> None:
    if not league or not season:
        raise ValueError("League and season must be supplied")
    if conn.execute("SELECT 1 FROM leagues WHERE slug=?", (league,)).fetchone() is None:
        raise ValueError("Unsupported league; only the five requested leagues are configured")
    conn.execute(
        "INSERT OR IGNORE INTO seasons(league_slug,season_label) VALUES (?,?)",
        (league, season),
    )


def catalogue_from_bytes(path: Path, raw: bytes) -> dict:
    """Import ACTUAL seasons from a saved GET /v1/leagues JSON response."""
    payload = _decoded(raw)
    entries = payload.get("data", {}).get("leagues") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ValueError("Expected the provider envelope data.leagues[]")
    # Keep only the five selected leagues: discard other providers' catalogue items.
    chosen = [entry for entry in entries if isinstance(entry, dict)
              and (entry.get("name"), entry.get("country_code")) in LEAGUE_KEYS]
    reduced = json.dumps({"data": {"leagues": chosen}}, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(reduced).hexdigest()
    fetched = utc_now()
    leagues_seen, new_seasons = set(), 0
    with connect(path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO catalogue_snapshots
               (fetched_at_utc,sha256_hex,original_bytes,body_gzip)
               VALUES (?,?,?,?)""",
            (fetched, digest, len(reduced),
             gzip.compress(reduced, compresslevel=6, mtime=0)),
        )
        for entry in chosen:
            if not isinstance(entry, dict):
                continue
            slug = LEAGUE_KEYS.get((entry.get("name"), entry.get("country_code")))
            if slug is None:
                continue
            provider_id = entry.get("id")
            if not isinstance(provider_id, str) or not provider_id:
                raise ValueError("Missing real PitchAPI league ID")
            stored = conn.execute(
                "SELECT pitchapi_league_id FROM leagues WHERE slug=?", (slug,)
            ).fetchone()[0]
            if stored and stored != provider_id:
                raise ValueError("PitchAPI league ID changed: investigate before continuing")
            conn.execute(
                "UPDATE leagues SET pitchapi_league_id=? WHERE slug=?",
                (provider_id, slug),
            )
            leagues_seen.add(slug)
            seasons = entry.get("seasons")
            if not isinstance(seasons, list):
                raise ValueError("Missing provider season list for " + slug)
            for season in seasons:
                if not isinstance(season, str) or not season.strip():
                    raise ValueError("Invalid provider season label")
                found = conn.execute(
                    "SELECT 1 FROM seasons WHERE league_slug=? AND season_label=?",
                    (slug, season),
                ).fetchone()
                _season(conn, slug, season)
                if found is None:
                    new_seasons += 1
    return {"leagues_matched": sorted(leagues_seen), "new_seasons": new_seasons}


def _empty(data: object) -> bool:
    if data is None or data == [] or data == {}:
        return True
    if isinstance(data, dict):
        for key in ("matches", "shots", "events", "points", "periods", "players"):
            if key in data and data[key] == []:
                return True
    return False


def archive_bytes(
    path: Path, *, league: str, season: str, dataset: str,
    resource_key: str, endpoint: str, raw: bytes,
    match_id: str | None = None,
) -> int:
    """Save an immutable provider-response version without losing any field."""
    if not dataset or not resource_key or not endpoint:
        raise ValueError("Dataset, resource key and provider endpoint are required")
    check_dataset(dataset, endpoint)
    payload = _decoded(raw)
    if dataset == "advanced" and isinstance(payload, dict):
        detail = payload.get("data", {})
        if isinstance(detail, dict) and "players" in detail:
            raise ValueError("Individual player advanced metrics excluded")
    digest = hashlib.sha256(raw).hexdigest()
    packed = gzip.compress(raw, compresslevel=6, mtime=0)
    fetched = utc_now()
    state = "empty" if _empty(payload.get("data") if isinstance(payload, dict) else payload) else "present"
    with connect(path) as conn:
        _season(conn, league, season)
        conn.execute(
            """INSERT OR IGNORE INTO source_responses
               (league_slug,season_label,dataset,resource_key,match_id,
                endpoint,fetched_at_utc,sha256_hex,original_bytes,compressed_bytes,body_gzip)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (league, season, dataset, resource_key, match_id,
             endpoint, fetched, digest, len(raw), len(packed), packed),
        )
        response_id = conn.execute(
            """SELECT response_id FROM source_responses
               WHERE league_slug=? AND season_label=? AND dataset=?
                 AND resource_key=? AND sha256_hex=?""",
            (league, season, dataset, resource_key, digest),
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO endpoint_coverage
               (league_slug,season_label,dataset,resource_key,state,last_checked_at_utc,source_response_id)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(league_slug,season_label,dataset,resource_key)
               DO UPDATE SET state=excluded.state,
               last_checked_at_utc=excluded.last_checked_at_utc,
               source_response_id=excluded.source_response_id
               WHERE excluded.last_checked_at_utc >= endpoint_coverage.last_checked_at_utc""",
            (league, season, dataset, resource_key, state, fetched, response_id),
        )
        return response_id


def archive_file(path: Path, *, league: str, season: str, dataset: str,
                 resource_key: str, endpoint: str, file: Path,
                 match_id: str | None = None) -> int:
    return archive_bytes(path, league=league, season=season, dataset=dataset,
                         resource_key=resource_key, endpoint=endpoint,
                         raw=file.read_bytes(), match_id=match_id)


def restore_bytes(path: Path, response_id: int) -> bytes:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT body_gzip,sha256_hex,original_bytes FROM source_responses WHERE response_id=?",
            (response_id,),
        ).fetchone()
    if row is None:
        raise KeyError("Unknown source response")
    raw = gzip.decompress(row[0])
    if len(raw) != row[2] or hashlib.sha256(raw).hexdigest() != row[1]:
        raise ValueError("PitchAPI response checksum/size mismatch")
    return raw


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    if isinstance(value, str):
        try:
            result = float(value)
            return result if math.isfinite(result) else None
        except ValueError:
            return None
    return None


def _identity(conn, team: dict, fetched: str) -> str | None:
    team_id = team.get("id") if isinstance(team, dict) else None
    if not team_id:
        return None
    conn.execute(
        """INSERT INTO teams(pitchapi_team_id,team_name,last_seen_utc)
           VALUES (?,?,?)
           ON CONFLICT(pitchapi_team_id)
           DO UPDATE SET team_name=COALESCE(excluded.team_name,teams.team_name),
               last_seen_utc=excluded.last_seen_utc""",
        (team_id, team.get("name"), fetched),
    )
    return team_id


def _fixture(conn, item: dict, league: str, season: str, fetched: str) -> int:
    mid = item.get("id")
    h, a = _identity(conn, item.get("home_team") or {}, fetched), _identity(
        conn, item.get("away_team") or {}, fetched
    )
    if not mid or not h or not a:
        return 0
    old = conn.execute(
        "SELECT league_slug,season_label FROM fixtures WHERE pitchapi_fixture_id=?", (mid,)
    ).fetchone()
    if old and old != (league, season):
        raise ValueError("Match ID appears under a different competition/season")
    conn.execute(
        """INSERT INTO fixtures
           (pitchapi_fixture_id,league_slug,season_label,kickoff_utc,home_team_id,
            away_team_id,fixture_status,home_goals,away_goals,raw_json,last_seen_utc)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(pitchapi_fixture_id) DO UPDATE SET
             kickoff_utc=COALESCE(excluded.kickoff_utc,fixtures.kickoff_utc),
             home_team_id=excluded.home_team_id,
             away_team_id=excluded.away_team_id,
             fixture_status=COALESCE(excluded.fixture_status,fixtures.fixture_status),
             home_goals=COALESCE(excluded.home_goals,fixtures.home_goals),
             away_goals=COALESCE(excluded.away_goals,fixtures.away_goals),
             raw_json=excluded.raw_json,
             last_seen_utc=excluded.last_seen_utc
           WHERE excluded.last_seen_utc >= fixtures.last_seen_utc""",
        (mid, league, season, item.get("time_utc"), h, a,
         item.get("status"), item.get("score_home"), item.get("score_away"),
         json.dumps(item, ensure_ascii=False, separators=(",", ":")), fetched),
    )
    return 1


def _flatten(value, prefix=""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _flatten(v, f"{prefix}.{k}" if prefix else str(k))
    elif _number(value) is not None:
        yield prefix, _number(value)


def project_response(path: Path, response_id: int) -> dict:
    """Project approved team/match endpoints to fast-query tables.

    A fixture must be projected before its match-specific stats. Projection
    failure rolls back only projections; source responses remain archived.
    """
    with connect(path) as conn:
        dataset_row = conn.execute("SELECT dataset,endpoint FROM source_responses WHERE response_id=?", (response_id,)).fetchone()
    if dataset_row is None:
        raise KeyError("Unknown response")
    check_dataset(*dataset_row)
    payload = _decoded(restore_bytes(path, response_id))
    if not isinstance(payload, dict) or "data" not in payload:
        raise ValueError("Expected PitchAPI data envelope")
    data = payload["data"]
    with connect(path) as conn:
        row = conn.execute(
            """SELECT league_slug,season_label,dataset,match_id,fetched_at_utc
               FROM source_responses WHERE response_id=?""", (response_id,)
        ).fetchone()
        if row is None:
            raise KeyError("Unknown response")
        league, season, dataset, match_id, fetched = row
        count = 0
        if dataset in ("league_matches", "match_summary"):
            matches = data.get("matches", []) if dataset == "league_matches" else [data]
            if not isinstance(matches, list):
                raise ValueError("Bad match listing shape")
            for match in matches:
                if isinstance(match, dict):
                    count += _fixture(conn, match, league, season, fetched)
        elif dataset in ("events", "stats", "lineups", "momentum", "advanced"):
            if not isinstance(data, (dict, list)):
                raise ValueError("Invalid match data shape")
            actual_mid = data.get("match_id") if isinstance(data, dict) else None
            mid = match_id or actual_mid
            if not mid:
                raise ValueError("Match ID required for projection")
            if actual_mid and actual_mid != mid:
                raise ValueError("Match ID does not agree with provider payload")
            if conn.execute("SELECT 1 FROM fixtures WHERE pitchapi_fixture_id=?", (mid,)).fetchone() is None:
                raise ValueError("Project league_matches or match_summary before per-match data")
            if dataset == "events":
                for i, event in enumerate(data.get("events", [])):
                    conn.execute(
                        """INSERT INTO match_events
                           (match_id,event_ordinal,event_type,period,minute,minute_added,
                            team_id,player_id,raw_event_json,source_response_id)
                           VALUES (?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(match_id,event_ordinal) DO UPDATE SET
                           event_type=excluded.event_type,period=excluded.period,
                           minute=excluded.minute,minute_added=excluded.minute_added,
                           team_id=excluded.team_id,player_id=excluded.player_id,
                           raw_event_json=excluded.raw_event_json,
                           source_response_id=excluded.source_response_id""",
                        (mid, i, event.get("event_type"), event.get("period"),
                         _number(event.get("minute")), _number(event.get("minute_added")),
                         event.get("team_id"), (event.get("player") or {}).get("id"),
                         json.dumps(event, ensure_ascii=False), response_id),
                    )
                    count += 1
            elif dataset == "stats":
                teams = {
                    "home": (data.get("home_team") or {}).get("id"),
                    "away": (data.get("away_team") or {}).get("id"),
                }
                for period in data.get("periods", []):
                    name = str(period.get("period", "All"))
                    for group in period.get("groups", []):
                        group_key = str(group.get("group_name", "unknown"))
                        for item in group.get("items", []):
                            metric = item.get("key")
                            if not metric:
                                continue
                            for side, team_id in teams.items():
                                if not team_id:
                                    continue
                                value = item.get(side)
                                textval = str(value) if value is not None else None
                                fmt = item.get("format_type") or ""
                                numeric = _number(value) if fmt in ("", "integer", "double") else None
                                conn.execute(
                                    """INSERT INTO match_team_stats
                                       (match_id,team_id,period,group_key,metric_key,format_type,
                                        display_value,numeric_value,source_response_id)
                                       VALUES (?,?,?,?,?,?,?,?,?)
                                       ON CONFLICT(match_id,team_id,period,group_key,metric_key)
                                       DO UPDATE SET format_type=excluded.format_type,
                                       display_value=excluded.display_value,numeric_value=excluded.numeric_value,
                                       source_response_id=excluded.source_response_id""",
                                    (mid, team_id, name, group_key, metric, fmt, textval, numeric, response_id),
                                )
                                count += 1
            elif dataset == "lineups":
                for side in ("home", "away"):
                    details = data.get(side)
                    if not isinstance(details, dict):
                        continue
                    confirmed = details.get("confirmed")
                    conn.execute(
                        """INSERT OR IGNORE INTO lineup_snapshots
                           (match_id,side,source_response_id,confirmed,lineup_type,
                            formation,raw_side_json) VALUES (?,?,?,?,?,?,?)""",
                        (mid, side, response_id, int(confirmed) if isinstance(confirmed, bool) else None,
                         details.get("lineup_type"), details.get("formation"),
                         json.dumps(details, ensure_ascii=False)),
                    )
                    count += 1
            elif dataset == "momentum":
                for i, point in enumerate(data.get("points", [])):
                    conn.execute(
                        """INSERT INTO match_momentum
                           (match_id,point_ordinal,minute,pressure_value,source_response_id)
                           VALUES (?,?,?,?,?)
                           ON CONFLICT(match_id,point_ordinal) DO UPDATE SET
                           minute=excluded.minute,pressure_value=excluded.pressure_value,
                           source_response_id=excluded.source_response_id""",
                        (mid, i, _number(point.get("minute")), _number(point.get("value")), response_id),
                    )
                    count += 1
            elif dataset == "advanced":
                for subject in data.get("teams", []):
                    team_id = (subject.get("team") or {}).get("id")
                    if not team_id:
                        continue
                    for k, value in _flatten({a: v for a, v in subject.items() if a != "team"}):
                        conn.execute(
                            """INSERT INTO advanced_metrics
                               (match_id,subject_type,subject_id,metric_path,metric_value,source_response_id)
                               VALUES (?,?,?,?,?,?)
                               ON CONFLICT(match_id,subject_type,subject_id,metric_path)
                               DO UPDATE SET metric_value=excluded.metric_value,
                               source_response_id=excluded.source_response_id""",
                            (mid, "team", team_id, k, value, response_id),
                        )
                        count += 1
        else:
            return {"dataset": dataset, "projected": 0, "raw_retained": True}
    return {"dataset": dataset, "projected": count, "raw_retained": True}
