# BETPILOT PREMATCH V3 — Generic fixture orchestration (77 pinned leagues)

**Scope:** research-only integration, no wagers, no modification of GA3 RC2,
GTS3 RC1, RC15 + Verdict C, MA3 Native RC2, BA3 RC1, or collector workflows.

## What has changed

The former PR #7 bridge was hardcoded for Spain's LaLiga. It now discovers one
future match from the existing API-Football authenticated date feed, without
requiring a numeric API league ID, and independently verifies the selected
fixture through PitchAPI's date, event-detail and league-catalogue endpoints.
It resolves the PitchAPI event/league against the **original 77-league pinned
registry**, and compares cross-provider team identity, home/away orientation,
kickoff, country and season. The independent provider fixture IDs are retained.
The seven original odds, their capture timestamp and provider evidence are not
fabricated or re-dated. Match identity is rebound *only after verification*.

The previous five-file Phase 1 runner is reused unchanged in concept and calls
the frozen original GA3 and GTS3 executables; original GTS raw team stats can be
located by pinned league key when the pre-existing archives exist.

One generic GitHub Actions workflow, `prematch-v3-universal.yml`, uses user
inputs (home, away, UTC date, optional country/competition disambiguation),
first resolves the precise original source league, restores that league's
existing GitHub Actions historical caches, restores the daily data branch
read-only and invokes this same one-command integration. It has **no schedule**
and does not change LIVEPICK or BUCO NERO.

## Inputs

Run from repository root in an authorized environment where the secrets
`PITCHAPI_API_KEY` and `APISPORTS_KEY` already exist:

```bash
python -m betpilot_v3.real_prematch_bridge \
  --request "FULL ANALYSIS: Inter - Juventus" \
  --day-utc 2026-11-15 \
  --warehouse-root /existing/warehouse \
  --history-root /existing/pitchapi_historical_checkpoint_tree \
  --team-stats-root /existing/pitchapi_team_stats \
  --output-dir /analysis/new_fixture
```

The date shown is an **illustration of command syntax**, not a verified match
or forecast. Country, competition, API league, season or exact fixture ID can
also be supplied to disambiguate; they are not required for unique matches.

A generic workflow may be dispatched after the workflow definition is
available to GitHub Actions on the repository default branch. A workflow
existing on a draft branch is *not* proof that it has already executed.

## Actual support versus catalogue discovery

- **Fixture discovery:** provider catalogue currently pins 77 competitions,
  not all leagues globally. Within that catalogue, a future event must be
  available from both authenticated providers, and uniquely identifiable.
- **Statistical execution:** legacy GA3 league history with a valid cutoff and
  sufficient native GTS3 source raw team stats are essential. Some historical
  `/stats` caches exist for the four major European leagues, but coverage is
  NOT established for every one of the 77. GTS3 frozen period must match the
  target season and predate the observation cutoff; the engine's supported
  periods must never be extended by invented parameters.
- **Market/decision:** valid seven Bet365 odds and identical event timestamps
  are necessary. The native MA3/BA3 may refuse missing evidence and issue
  a legitimate `NO BET`.
- **No direct chat deployment:** a text command to ChatGPT does not currently
  dispatch GitHub Actions automatically. This workflow provides a GitHub-native
  input surface, not an installed chat execution bridge.
- **Scientific validation:** even after an authenticated full-chain test,
  probability calibration, prospective results and financial efficacy remain
  distinct and unproven.

## Safety, failure modes and reproducibility

API-Football requests: a preflight quota check then at most 1 date-fixture
request and 1 odds request per execution; client caps at 3 charged requests and
reserves 20 daily credits. Phase 1 PitchAPI requests remain budgeted at 3.
The generic workflow's initial league selection uses at most three additional
PitchAPI calls to locate the exact cache (duplicated selection is a deliberate
independent guard, not repeated historical download).

Original quote JSON, metadata and independent identity proof are stored beside
`PREMATCH_V3_EXECUTION_STATUS.json`. The native pipeline runs only after all
cross-provider checks. Unsupported/unavailable leagues, non-matching team names,
ambiguous fixtures, missing source archives, unfrozen periods, stale quotes,
missing secrets and cutoff conflicts produce explicit research-only error states.
They must never trigger prior-version fallbacks or imaginary pronostici.

## Tests

`betpilot_v3/tests/test_general_fixture.py`: original 77-pins count,
direct source reconciliation across five countries/leagues, same-named leagues
in different countries, translated aliases, fixture ambiguity and mismatch
fail-closed. Existing source and five-engine synthetic regression suites
remain active in GitHub CI.

**Verdict before genuine authenticated E2E:** software integration in draft,
research-only, not fully operational for all 77 matches. No merge authorization
is inferred from synthetic CI alone.
