# PitchAPI database: source-first v3

## Scope and boundaries
- **Leagues:** Premier League, Serie A, Ligue 1, La Liga, Bundesliga only.
- **Seasons:** no earliest/latest or maximum season is hardcoded. Load all **actually advertised** seasons for each league from the real `GET /v1/leagues` response.
- **Source:** official [PitchAPI documentation](https://pitchapi.dev/), accessed read-only for field-level design. No requests to PitchAPI are made by any database command.
- **Collectors:** not connected. Historical backfill and recurring downloads remain **OFF**.
- **Isolation:** no changes to other GitHub repos/projects/collectors.
- **Data completeness:** neither a provider catalogue entry nor a successful archive is evidence that every endpoint exists for every match. Missing, unavailable and empty are different conditions.

## Why this schema matches actual PitchAPI
1. Real provider league catalogue returns `data.leagues[].seasons`, an array of strings. There is **no pre-seeded fixed year range**.
2. `/v1/leagues/{id}/matches` returns match lists; `/v1/matches/{id}` returns a single summary. Scores may be null before kick-off. Kickoff is mutable.
3. `/v1/matches/{id}/shots` returns `data.periods[].shots[]`; shot IDs are unique only *inside their match*. The primary key is **(match_id, shot_id)**.
4. `/v1/matches/{id}/stats` uses groups and **formatted string** values, such as `329 (82%)`. Store the full string and `format_type`; only parse a simple numeric when unambiguous. No null becomes 0.
5. Lineups can be predicted or confirmed. Every changed source response has its own immutable compressed version. `lineup_snapshots` preserves prediction/confirmation.
6. Player statistics differ by position/half, while advanced metrics are nested and may expand with new provider keys. The original JSON is always stored, even when no normalized projection is available.
7. Events, momentum, heatmaps, pass networks, H2H, advanced/player endpoints are preserved as full original JSON. Common searchable subfields are indexed when a supported projection exists.

## Physical storage

**SQLite v3** designed as an initial single-writer private database. The source of truth is `source_responses.body_gzip` (gzip-compressed exact original UTF-8 response bytes); store SHA-256 checksum, sizes, UTC fetched timestamp, endpoint, league, season, optional fixture ID and immutable per-version identity. Each new version is retained, while an identical response is deduplicated.

A separate `catalogue_snapshots` table holds real league catalogue JSON even before seasons are known. New provider endpoints can be archived **without a schema change**. Existing v1/v2 `raw_payloads` remains for backward compatibility; v3 ingestion writes **only** to the compressed `source_responses` table, not both.

Optimized query tables: `fixtures`, `teams`, `players`, `match_shots`, `match_events`, `match_team_stats`, `match_player_stats`, `lineup_snapshots`, `match_momentum`, `advanced_metrics`. All extracted facts point to the exact immutable source response via `source_response_id`. `endpoint_coverage` records `present` versus `empty`, with additional `unavailable` and `error` states reserved for future importer/collector failure reporting. Ingestion logs and per-resource cursors exist for future batch collection, but none is activated.

Foreign keys and indexes prevent invalid joins and support league/season, match, team, player, metric and endpoint queries. Writes run in transactions. SQLite WAL, busy_timeout and a bounded cache improve local reads and writer contention. There is **no claim** of a benchmarked throughput improvement or guaranteed capacity.

## Commands: offline only

```bash
# 1. Create/upgrade in place; original data retained:
python db/manage.py --db data/pitchapi.sqlite init
python db/manage.py --db data/pitchapi.sqlite verify

# 2. Import the *real* provider catalogue response, already saved locally:
python db/manage.py --db data/pitchapi.sqlite import-pitchapi-catalogue \
  --file /private/pitchapi/leagues.json

# 3. Archive exact provider response, optionally create indexed projections:
python db/manage.py --db data/pitchapi.sqlite ingest-json \
  --league eng-premier --season 2021/2022 --dataset league_matches \
  --resource-key league-2021-played --endpoint /v1/leagues/PROVIDER_ID/matches \
  --file /private/pitchapi/matches-2021.json --project

# 4. For a match, once its fixture has been projected:
python db/manage.py --db data/pitchapi.sqlite ingest-json \
  --league eng-premier --season 2021/2022 --dataset shots \
  --resource-key m_MATCH --fixture-id m_MATCH \
  --endpoint /v1/matches/m_MATCH/shots \
  --file /private/pitchapi/shots.json --project

# 5. Rebuild a searchable projection or restore exact source bytes:
python db/manage.py --db data/pitchapi.sqlite project-response --response-id 1
python db/manage.py --db data/pitchapi.sqlite restore-response \
  --response-id 1 --output /private/pitchapi/restored.json

python db/manage.py --db data/pitchapi.sqlite status
python -m unittest discover -s tests -v
```

Note: `PROVIDER_ID` and `m_MATCH` are placeholders, **not real IDs**. The actual response ID is printed when importing. To avoid pretending a field is available, unsupported `dataset` kinds are stored fully without extracting guessed fields. Supported projection names at this stage: `league_matches`, `match_summary`, `shots`, `stats`, `events`, `lineups`, `momentum`, `players`, `advanced`.

**Projection dependency:** match-specific data needs the fixture/teams projected first; its source JSON is safe even if that projection fails. If provider responses contain a new/unsupported structure, store the raw JSON first and implement a separate parser.

## Long-term durability & privacy

GitHub holds **code and schemas**, not a durable running SQL database. A .sqlite file downloaded here is a snapshot, not a persistent server. When enabling actual downloads, select a persistent encrypted disk or a managed private PostgreSQL service, with periodic backups, recovery tests, and provider-data retention/usage permissions confirmed. GitHub Actions' temporary runners and artifacts must **never** be the only copy of the database. Keep API keys in secret storage, never in this repository. Do not put provider datasets on a public branch.

## Not implemented / still required before production
- Authenticated PitchAPI collector and actual end-to-end ingest: **disabled / not built here**.
- Endpoint-by-endpoint coverage audit and missing-data retry policy.
- Database hosting, backup/recovery, capacity benchmark and monitoring.
- Optimized columnar/partitioned warehouse, if measurements justify scaling beyond SQLite.
- An explicit one-time conversion tool for any pre-existing v1/v2 uncompressed rows. The original `raw_payloads` table is left untouched.
