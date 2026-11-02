# BET PILOT — PitchAPI backfill & incremental sync

## Current operating status (2026-11-01)

The automated workflow is installed and **enabled**, but the first run
([GitHub Actions run 41310483067](https://github.com/jscommessinho/BET-PILOT-Prematch-Analyzer/actions/runs/41310483067))
stopped before downloading because this repository does not have the
`PITCHAPI_API_KEY` secret. No API key is embedded in the repository.

## One-time required action by the owner

1. Open [repository Actions secrets](https://github.com/jscommessinho/BET-PILOT-Prematch-Analyzer/settings/secrets/actions).
2. New repository secret: **PITCHAPI_API_KEY**. Value: your valid PitchAPI key
   from [PitchAPI dashboard](https://pitchapi.dev/). Never paste the key into
   chat, README, Git files or workflow logs.
3. To begin backfilling immediately, open [PitchAPI sync workflow](https://github.com/jscommessinho/BET-PILOT-Prematch-Analyzer/actions/workflows/pitchapi-sync.yml),
   choose **Run workflow**, branch **main**, leave `max_matches=0`
   and `only_league` empty, then run.

## Automatic operation

Every **Tuesday and Friday at 07:17 Europe/Rome** (DST-aware via
GitHub Actions timezone-aware cron), the same workflow:
- Reads the authenticated `GET /v1/leagues` catalogue.
- For each of the five requested leagues, reads **every** season actually
  advertised by PitchAPI, without a hardcoded 2021/22–2026/27 limit.
- Retrieves `/v1/leagues/{id}/matches?season=...&status=played`,
  reconciles match IDs, and inserts newly settled fixtures.
- For completed matches, acquires missing `match_summary`, `stats`,
  `events`, `lineups`, `momentum`, and team `advanced` endpoints.
- Rechecks recent matches for corrections. Retains old responses immutably
  and deduplicates exact versions via SHA-256.
- Separates unavailable provider analytics (404 ANALYTICS_UNAVAILABLE)
  from temporary failures and retries 429/5xx using backoff.
- Never requests `/shots`, player statistics, shot coordinates, or
  per-shot xG.
- Writes a JSON report at `reports/pitchapi-sync.json`.

## Durable database location

The five-league data is **NOT** kept on ephemeral GitHub Actions runners.
Every run loads the private branch `pitchapi-warehouse` and writes
small SQLite shards:

```text
data/catalogue.sqlite
data/eng-premier/2021-2022.sqlite
data/eng-premier/2022-2023.sqlite
...
data/ita-seriea/...
data/esp-laliga/...
data/ger-bundesliga/...
data/fra-ligue1/...
reports/pitchapi-sync.json
```

The action commits every successfully written shard to the **private**
`pitchapi-warehouse` branch even if a later network request fails.
Subsequent runs restore this branch. It does not modify any other repository.
As data grows, GitHub's large-file restrictions and repository growth need
monitoring; migrate to managed private object storage if needed. An independent
backup outside GitHub is still recommended.

## Meaning of complete

A historical season is complete *for the available requested metrics* only
when all finished matches in the API listing have corresponding ingested
records for each endpoint that PitchAPI supports. Missing provider ratings
remain clearly indicated as unavailable; they are not fabricated. A normal
run's success alone is not proof of all-statistic completeness.

## Tests

Offline mocks verify: all-season discovery; no per-shot/player storage;
versioned compressed archive; historical initial synchronization; second
synchronization doesn't duplicate matches; a third synchronization discovers
and stores an additional completed match.
