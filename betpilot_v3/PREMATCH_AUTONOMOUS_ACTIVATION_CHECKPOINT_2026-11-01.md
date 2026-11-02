# BETPILOT PREMATCH V3 — Autonomous technical activation checkpoint

**Date:** 2026-11-01 UTC. **Repository:** jscommessinho/betpilot-prematch-analyzer.
**Branch:** feature/prematch-v3-real-quote-identity-bridge-20261101.
**PR:** #7, draft; stacked on Phase 1 PR #6. **Main is untouched.**
**Verdict:** PARTIALLY OPERATIONAL, RESEARCH_ONLY / NO BET.
This report does not certify a real-match end-to-end run.

## A. Independently identifiable integration issues

1. Main's full_analysis.py accepts six native source JSON documents but has no authenticated per-match acquisition routine. The Phase 1 PR #6 provides exactly five, while the earlier quote API workflows call the legacy RC15 -> MA2 path.
2. API-Football assigns `API-FOOTBALL-fixture-N` IDs and `Spain La Liga 2026`; PitchAPI assigns `m_...` IDs and `LaLiga`. Passing these as if they were one fixture, or renaming files without checking identity, breaks MA3/RC15 contracts or misclassifies Spain in the RC15 calibration scope.
3. Existing historical raw `/stats` responses were not consumed by the GTS3 Phase 1 adapter. They are separately collected already by `.github/workflows/prematch-pitchapi-26-league-history.yml` for the Spanish league (schema `BETPILOT_PITCHAPI_TEAM_STATS_v1`, records with original `statistics` and provider season). The five-day daily warehouse is not the full historical dataset.
4. The original external GTS3 period selector compared training end with kickoff; it must compare with the analysis data cutoff, which may be earlier.

## B. Changes implemented

- `betpilot_v3/real_prematch_bridge.py`: new research-only one-command join. Runs the existing budget-aware quote discovery (not legacy MA2), then the original PitchAPI Phase 1 publisher, checks cross-source identity, time order and exact pinned provider league/season, records the unchanged authenticated quote JSON separately, derives a six-file bundle, invokes the existing five-engine `full_analysis.run`. No fabricated odds or probability and no fallback to older analyzers.
- `betpilot/bookmaker_verdict_c.py`: external label alias `LaLiga` -> `Spain La Liga` to retain the approved frozen BIG5 RC15 behavior. Does not alter RC15 formulas, parameters or calibration.
- `betpilot_v3/gts3_original_adapter.py`: optional read-only ingestion of the existing original 26-league team-stats archive; authentic raw stats are passed to the frozen parser. QA, source league, prior retrieval, season, lag, IDs, source SHA and count gates remain in place. The per-shot xG aggregates are never substituted.
- `betpilot_v3/pitchapi_native_phase1.py`: forwards the optional original team-stat archive to GTS3 and selects the frozen GTS period before the actual source cutoff.
- `betpilot_v3/tests/test_real_prematch_bridge.py`: no-network identity/provenance/season/quote/cutoff gating tests, exact LaLiga calibration alias, mocked orchestration and source fail-closed.
- `betpilot_v3/tests/test_existing_original_team_stats_join.py`: no-network fixtures for reuse of the old original `/stats` archive with rejection of bad league, late retrieval and malformed raw team stats.
- `.github/workflows/prematch-v3-getafe-sevilla-once.yml`: one-time, isolated real-input attempt, uses secrets stored in GitHub, restores the pre-existing Spanish stats/cache and read-only daily branch, refuses billable calls if source caches are missing, then invokes the original five-engine bridge with bounded provider clients and publishes its research outputs as an artifact. No recurring schedule.

## C. Definitive engine status

| Engine | Version | Changed | Evidence |
|---|---|---|---|
| Goal Analyzer | 3.0 RC2 PitchAPI only | No | Frozen manifest and existing native CI |
| Goal Threat Score | 3.0 RC1 | No | Original parser, native CI; new external raw source join |
| Bookmaker Analyzer | 2.0 RC15 + Verdict C | No internal change | External competition-label mapping, existing quote CI |
| Market Analyzer | 3.0 Native RC2 | No | Existing native synthetics |
| BetAnalyzer | 3.0 RC1 | No | Existing native CLI in synthetic five-engine tests |

All engines remain research-only until independent real-input execution and evidence review.

## D. Real end-to-end test — Getafe vs Sevilla

**Configured fixture:** LaLiga, November 1, 2026 UTC; PitchAPI league ID `l_0ErfuF`, API-Football league ID `140`, season 2026.
**Evidence needed:** GH Actions artifact produced by the isolated one-time workflow and its explicit `PREMATCH_V3_EXECUTION_STATUS.json`, `FULL_ANALYSIS_STATUS.json`, cross-provider SHA audit and original engine reports.
**Current assessment:** no authenticated five-engine run has been independently inspected or certified in this report. It is not legitimate to infer a prediction from passed synthetic CI.

The one-time workflow does not re-download the historical seasons. It attempts to restore already existing historical artifacts by their cache keys and fails before billable calls if those are absent.

## E. Operational classification and constraints

**PARTIALLY OPERATIONAL.** A real-data command and a bounded GitHub runner are implemented, but the full operational validation gate is not satisfied until a future fixture passes authentic source, timestamp and provenance checks across both providers and all five engines.

Status is intentionally `RESEARCH_ONLY / NO BET`. Technical correctness is independent of OOS calibration, predictive quality and profitability. No stake automation or price-monitoring schedule has been introduced. No changes to LIVE / BUCO NERO or main.

## F. Reproduction

With an authorized local checkout, existing source copies, and the two GitHub-managed environment secrets:

```bash
python -m betpilot_v3.real_prematch_bridge \
  --request "FULL ANALYSIS: Getafe - Sevilla" \
  --day-utc 2026-11-01 --country-code ESP --competition LaLiga \
  --api-league-id 140 --api-season 2026 \
  --warehouse-root /secure/read_only_worldwide_warehouse \
  --history-root /secure/existing_per_league_JSON_archives \
  --team-stats-archive /secure/existing_team_stats/esp-laliga/archive.json \
  --output-dir /secure/analysis/getafe_sevilla
```

GitHub CI regression: see PR #7 checks, including `prematch-v3-native-integration`, general regressions and odds checks. CI uses only synthetic/mock inputs for the new bridge; the one-time workflow is a distinct real-input verification attempt.

**Never publish betting picks or mark PRODUCTION READY merely because CI passes.**
