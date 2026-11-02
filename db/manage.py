#!/usr/bin/env python3
"""Initialize and inspect the BET PILOT PitchAPI database.

No network calls, PitchAPI requests, or scheduled downloads exist here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

try:
    from . import pitchapi_store as store
except ImportError:
    import pitchapi_store as store

SCHEMA = Path(__file__).with_name("schema.sql")
DEFAULT_DB = Path("data/pitchapi.sqlite")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def initialize(path: Path) -> None:
    store.initialize(path)



def _upsert_season(connection: sqlite3.Connection, league: str, season: str,
                   provider_season_id: str | None = None) -> bool:
    """Register any available season without a hardcoded year range."""
    if not league.strip() or not season.strip():
        raise ValueError("League and season cannot be empty")
    known = connection.execute("SELECT 1 FROM leagues WHERE slug=?", (league,)).fetchone()
    if known is None:
        raise ValueError(f"Unknown league: {league}")
    existing = connection.execute(
        "SELECT pitchapi_season_id FROM seasons WHERE league_slug=? AND season_label=?",
        (league, season),
    ).fetchone()
    if existing is not None and provider_season_id and existing[0] and existing[0] != provider_season_id:
        raise ValueError("Conflicting PitchAPI season ID for an existing league/season")
    connection.execute(
        """INSERT INTO seasons (league_slug,season_label,pitchapi_season_id)
           VALUES (?,?,?)
           ON CONFLICT(league_slug, season_label) DO UPDATE SET
             pitchapi_season_id=COALESCE(seasons.pitchapi_season_id, excluded.pitchapi_season_id)""",
        (league, season, provider_season_id),
    )
    return existing is None


def register_season(path: Path, league: str, season: str,
                    provider_season_id: str | None = None) -> bool:
    """Manually add one season or register a provider-discovered season."""
    with connect(path) as connection:
        return _upsert_season(connection, league, season, provider_season_id)


def import_seasons(path: Path, source_file: Path) -> int:
    """Import verified league/season identities from an offline, normalized catalogue.

    JSON schema: [{"league_slug": "...", "season_label": "...",
                    "pitchapi_season_id": "optional"}].
    The complete input is validated transactionally; no guessed range or upper bound.
    """
    try:
        data = json.loads(source_file.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Catalogue must be valid UTF-8 JSON") from exc
    if isinstance(data, dict) and "seasons" in data:
        data = data["seasons"]
    if not isinstance(data, list):
        raise ValueError("Catalogue must be a JSON array of season entries")
    added = 0
    with connect(path) as connection:
        for entry in data:
            if not isinstance(entry, dict):
                raise ValueError("Each season entry must be an object")
            league, season = entry.get("league_slug"), entry.get("season_label")
            provider_id = entry.get("pitchapi_season_id")
            if not isinstance(league, str) or not isinstance(season, str):
                raise ValueError("Each entry must have league_slug and season_label strings")
            if provider_id is not None and not isinstance(provider_id, str):
                raise ValueError("PitchAPI season ID must be a string if supplied")
            added += int(_upsert_season(connection, league, season, provider_id))
    return added


def ingest_json(
    path: Path, *, league: str, season: str, dataset: str, resource_key: str,
    source_file: Path, fixture_id: str | None = None, endpoint: str | None = None
) -> bool:
    if not all([league.strip(), season.strip(), dataset.strip(), resource_key.strip()]):
        raise ValueError("league, season, dataset and resource-key must be nonempty")
    payload = source_file.read_bytes()
    # New data is stored once in compressed, checksum-addressable v3 storage.
    digest = hashlib.sha256(payload).hexdigest()
    with connect(path) as connection:
        old = connection.execute(
            """SELECT 1 FROM source_responses WHERE league_slug=? AND season_label=?
               AND dataset=? AND resource_key=? AND sha256_hex=?""",
            (league, season, dataset, resource_key, digest),
        ).fetchone()
    store.archive_bytes(path, league=league, season=season, dataset=dataset,
                        resource_key=resource_key,
                        endpoint=endpoint or "offline-import:unspecified",
                        raw=payload, match_id=fixture_id)
    return old is None


def status(path: Path) -> list[tuple[str, str, str, int, int]]:
    if not path.exists():
        raise FileNotFoundError("Database not initialized; run init")
    with connect(path) as connection:
        return connection.execute(
            """SELECT l.name, s.season_label, s.state,
                      (SELECT count(*) FROM fixtures f
                       WHERE f.league_slug=s.league_slug
                         AND f.season_label=s.season_label) AS fixtures,
                      (SELECT count(*) FROM raw_payloads p
                       WHERE p.league_slug=s.league_slug
                         AND p.season_label=s.season_label)
                      + (SELECT count(*) FROM source_responses v3
                         WHERE v3.league_slug=s.league_slug
                           AND v3.season_label=s.season_label) AS payloads
               FROM seasons s JOIN leagues l ON l.slug=s.league_slug
               ORDER BY l.name, s.season_label"""
        ).fetchall()


def verify(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError('Database not initialized; run init')
    with connect(path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise RuntimeError(
                f"Database not healthy: integrity={integrity}, fk_errors={foreign_keys}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="PitchAPI archive DB, no network access")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create schema and five leagues (no fixed seasons)")
    commands.add_parser("status", help="Read-only counts and ingestion state")
    commands.add_parser("verify", help="Verify database integrity and foreign keys")
    add = commands.add_parser("add-season", help="Register any season, without date limits")
    add.add_argument("--league", required=True)
    add.add_argument("--season", required=True)
    add.add_argument("--pitchapi-season-id")
    catalogue = commands.add_parser("import-seasons", help="Import offline season catalogue JSON")
    catalogue.add_argument("--file", type=Path, required=True)
    catalog = commands.add_parser("import-pitchapi-catalogue",
                                  help="Import real PitchAPI league/season catalogue from saved JSON")
    catalog.add_argument("--file", type=Path, required=True)
    project = commands.add_parser("project-response", help="Index a compressed source response")
    project.add_argument("--response-id", type=int, required=True)
    restore = commands.add_parser("restore-response", help="Recover the exact original provider JSON")
    restore.add_argument("--response-id", type=int, required=True)
    restore.add_argument("--output", type=Path, required=True)
    ingest = commands.add_parser("ingest-json", help="Archive raw provider JSON as verified gzip")
    ingest.add_argument("--league", required=True)
    ingest.add_argument("--season", required=True)
    ingest.add_argument("--dataset", required=True)
    ingest.add_argument("--resource-key", required=True)
    ingest.add_argument("--file", type=Path, required=True)
    ingest.add_argument("--fixture-id")
    ingest.add_argument("--endpoint")
    ingest.add_argument("--project", action="store_true",
                        help="Build searchable tables for a documented endpoint")
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.db)
        print(f"Initialized {args.db}: 5 leagues, NO season cap")
    elif args.command == "add-season":
        added = register_season(args.db, args.league, args.season, args.pitchapi_season_id)
        print("Season registered" if added else "Season already registered")
    elif args.command == "import-seasons":
        added = import_seasons(args.db, args.file)
        print(f"Registered {added} additional seasons, no upper limit")
    elif args.command == "import-pitchapi-catalogue":
        print(store.catalogue_from_bytes(args.db, args.file.read_bytes()))
    elif args.command == "project-response":
        print(store.project_response(args.db, args.response_id))
    elif args.command == "restore-response":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(store.restore_bytes(args.db, args.response_id))
        print("Original JSON restored with verified SHA-256")
    elif args.command == "ingest-json":
        inserted = ingest_json(
            args.db, league=args.league, season=args.season,
            dataset=args.dataset, resource_key=args.resource_key,
            source_file=args.file, fixture_id=args.fixture_id,
            endpoint=args.endpoint,
        )
        digest = hashlib.sha256(args.file.read_bytes()).hexdigest()
        with connect(args.db) as connection:
            response_id = connection.execute(
                """SELECT response_id FROM source_responses
                   WHERE league_slug=? AND season_label=? AND dataset=?
                     AND resource_key=? AND sha256_hex=?""",
                (args.league, args.season, args.dataset, args.resource_key, digest),
            ).fetchone()[0]
        print(f"response_id={response_id}; " +
              ("new compressed source response" if inserted else "existing SHA-256 duplicate"))
        if args.project:
            print(store.project_response(args.db, response_id))
    elif args.command == "verify":
        verify(args.db)
        print("Database integrity OK")
    else:
        print("League | Season | State | Fixtures | Raw responses")
        for name, season, state, fixtures, payloads in status(args.db):
            print(f"{name} | {season} | {state} | {fixtures} | {payloads}")


if __name__ == "__main__":
    main()
