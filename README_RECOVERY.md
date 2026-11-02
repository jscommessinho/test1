# BETPILOT | PREMATCH — Recovery package 2026-11-02

This directory is an isolated, corrected **copy** of the supplied `monnezzaai-main(2).zip` archive; it is not a merge into `main`, LIVEPICK, or BUCO NERO. The four `betpilot_v3/frozen/*.zip` original engines are SHA-256-identical to the uploaded originals and have not been patched or recalibrated. Legacy FootyStats/GA2/MA2 directories are included only to preserve the source baseline and are **not** used by the new command.

## One command

Run from this repository's root:

```bash
python -m betpilot_v3.command "BETPILOT: Deportivo La Coruña - Osasuna"
```

The command prints `BETPILOT_STATUS.json` to the terminal and writes `output/betpilot/BETPILOT_STATUS.json` and `BETPILOT_REPORT.md`. It checks local preverified six-file native bundles first, then attempts the existing authenticated PitchAPI + Bet365/API-Sports integration if source archives and provider keys are configured. It does not use web search, public match websites, synthetic values for real fixtures, or alternative providers. A real prediction requires real source data and valid keys available **to the executing machine**. No separate data entry is required per request when the trusted publisher has already prepared native bundles.

### One-time environment configuration

For a historical warehouse and authenticated quote runtime, configure environment variables in the **trusted host**, never in this repository:

- `PITCHAPI_API_KEY` and `APISPORTS_KEY` — existing provider credentials.
- `BETPILOT_SOURCES_ROOT` — optional persistent folder of fully verified native per-match bundles; preferred when available.
- `BETPILOT_WAREHOUSE_ROOT` — persistent PitchAPI worldwide daily archive root.
- `BETPILOT_HISTORY_ROOT` — original historical per-competition PitchAPI JSON archive root.
- `BETPILOT_TEAM_STATS_ROOT` — optional original team `/stats` archive root.
- `BETPILOT_FIXTURE_INDEX` — optional previously captured, authenticated PitchAPI fixture index for selecting the day without entering it by hand.

These are **one-time technical settings**, not recurring match JSONs. Without a verified fixture index, automatic live mode searches only the current UTC date to limit API cost and latency; if the match is on another date, use the authenticated fixture publisher or the optional `--day-utc` flag. The launcher does not claim complete fixture discovery for all dates and leagues.

## Recovery changes (outside frozen mathematical engines)

- Extended `full_analysis.parse_request` to accept the requested `BETPILOT:` trigger as well as `FULL ANALYSIS:`.
- Added the centralized executable `betpilot_v3/command.py` with consistent statuses, no invented sources, offline-bundle first, explicit API credentials and per-step fail-closed behavior.
- Added two-worker parallel execution **of GA3 and GTS3** in `pitchapi_native_phase1.py` after the original shared verified PitchAPI data preparation. The real-quote acquisition still precedes that stage so that source-cutoff chronology remains correct. Full three-way concurrency is not yet demonstrated.
- Fixed the real `QuotaClient.used` versus `client.count` integration mismatch in the external legacy API-Football adapter; the **RC15 mathematical engine remains unmodified**.
- Added `derived_markets.py` to compute 46 extra **unquoted** markets from the original GA3 joint score matrix using the original MA3 settlement methods. These are sidecar results, with no bookmaker prices invented; the core 47-market MA3 and BA3 Decision Engine remain unchanged. Additional unquoted markets are **not yet evaluated by BA3 as priced candidates**.
- Added targeted offline tests and audit files in `recovery_evidence/`.

## Reproduction / actual test results

```bash
NUMBA_NUM_THREADS=4 python betpilot_v3/run_replay.py --test
NUMBA_NUM_THREADS=4 python betpilot_v3/run_replay.py --output-dir /tmp/betpilot_replay
python -m betpilot_v3.command "BETPILOT: Deportivo La Coruña - Osasuna"
```

Five native test suites: **94 + 20 + 107 + 22 + 16 = 259 tests**, with two skips and no other failures in the final reproduction. Original-engine synthetic five-stage software handoff (including RC15 + Verdetto C and 47 MA3 markets, 46 supplemental markets, and BA3 Decision Engine) was tested with the release's labelled synthetic example. The unified wrapper took **4,191 ms** in one local synthetic replay. This does not certify a real-match full analysis under 60 seconds.

The required real command `BETPILOT: Deportivo La Coruña - Osasuna` was actually invoked in this sandbox. It returned `AUTHENTICATION_ERROR / MISSING_PROVIDER_CREDENTIALS` because the running environment does not have authorized `PITCHAPI_API_KEY` and `APISPORTS_KEY` secrets, and no authentic matching native fixture bundle was supplied. **No fixture ID, bookmaker quote, real match probability or pronostic was invented.** This required acceptance test is **NOT PASSED**; it is preserved in `recovery_evidence/DEPORTIVO_OSASUNA_STATUS.json`.

### Outstanding blockers — do not call this production-ready

1. Deploy the recovered executable into an authorized persistent host with actual PitchAPI archives and the two existing provider secrets; secrets were intentionally not copied into this ZIP.
2. Arrange source fixture-index publishing for dates other than current UTC day (or maintain native six-file bundles automatically).
3. Complete real, time-stamped Deportivo–Osasuna data retrieval and verify all five engines on that same authentic event.
4. Resolve **original eligibility policy conflicts** without falsely claiming that the four frozen releases have changed: original GA3 RC2 still has a 3-day match-availability lag, a `min_train=150` default and a first-of-month model-fit cutoff in its frozen CLI; frozen MA3 requires GTS3 historical lag and sample constraints. The external adapter has not bypassed these native checks, because doing so without a coordinated, tested native compatibility change would invalidate integrity/parity claims. No changes were made to lambda formula, 365-day half-life or ridge weights.
5. Integrate supplemental markets with native BA3 pricing/decision only when authentic market-specific odds exist and the original handoff contracts can be extended safely. Otherwise label `ODDS_UNAVAILABLE`.
6. Wire this executable to ChatGPT through an authorized remote execution connector. Merely typing a string in a normal ChatGPT chat does not autonomously invoke local Python or a GitHub workflow. This ZIP supplies the actual executable component, not a verified platform-level trigger.
7. Run real-world latency profiling and independent chronological predictive validation before claiming reliable predictions or economic value.

**Status:** Technical recovery completed only to the explicitly tested extent; entire user-mandated production activation is **INCOMPLETE**. NO BET when authentic inputs are missing.

## API credentials received on 2026-11-02 — safe live-provider preflight

`betpilot_v3.provider_preflight` was added after credentials were supplied in the conversation. It **never embeds or writes keys** and accepts them only through the process environment (`PITCHAPI_API_KEY`, `APISPORTS_KEY`). To check provider connectivity on an authorized machine **with internet access** and already configured secrets:

```bash
python -m betpilot_v3.provider_preflight --output output/betpilot/provider_preflight.json
```

The preflight only calls PitchAPI `/v1/leagues` (max 1 request) and API-Sports `/status` (1 quota check); it does not search the web, fetch fixtures, fetch odds or place bets. Its report never includes keys or raw authenticated API responses. A successful connectivity preflight **does not** prove that the required match, Bet365 quotes, or the historical PitchAPI archives are available. Run the main command only after genuine warehouses and original source bundles are configured in the runner.

This chat's filesystem does **not** have network access to either API and does **not** contain the persistent original PitchAPI warehouse/history directories. Live-key validity and the Deportivo–Osasuna real-data acceptance test are therefore **NOT VERIFIED** here. The supplied keys were not copied into this ZIP, saved to a `.env`, or uploaded to GitHub. Because credentials were shared directly in a chat, consider rotating them and injecting replacements through protected runner secrets.

The unified command now redacts both provider keys (if configured in the executing process) from output JSON/Markdown, including unexpected exception messages.
