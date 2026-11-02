# BETPILOT PREMATCH — Recovery evidence (2026-11-02)

## Baseline and isolation

- Source repository: `jscommessinho/BET-PILOT-Prematch-Analyzer`.
- Base SHA: `ca3c4182dc5a18da8d2e8fb5f53889c60de2c4ff`.
- Recovery branch: `recovery/20261102-bookmaker-imports-and-isolation`.
- No merge, no main rewrite, no modifications to LIVEPICK or BUCO NERO.
- No web searches, no alternate match-statistics provider or odds provider.
- No API key stored; tests do not call PitchAPI or API-Football.

## Proven root cause: Bookmaker API integration

The failed main-branch GitHub Actions run
`41398435714` (2026-11-02) passed native RC15 regression, standalone
smoke and an RC15 offline report; the live-quote step then crashed with:

```text
AttributeError: 'QuotaClient' object has no attribute 'count'
bookmaker_independent/bookmaker/api_football.py:219
```

`QuotaClient` exposes `used`, not `count`. The adapter now supports the
`used` API without changing the original RC15 model or touching quota
calculations. A mocked acquisition test reproduces the old call chain,
verifies two API requests are counted, and prevents this regression.
An additional unused FootballClient lacked urllib imports; they have been
restored. A dormant Step5 vendor-import hook was removed from quota_client,
preserving Bookmaker independence.

This demonstrates a code bug, not proof that any particular bookmaker market
is available. No live API re-acquisition is claimed.

## New mathematical markets (additive, no original model changes)

`integration/derived_markets.py` evaluates the original GA3 joint score
distribution, and `integration/ga3_fast.py` now exposes these under
`additional_markets` while preserving the existing `markets` field.

Supported:
- Double chance: 1X, X2, 12;
- home and away score, multigol 1–2 / 1–3 and team over 1.5 / 2.5;
- each double-chance selection combined with over 0.5 / 1.5 / 2.5 /
  3.5 / 4.5;
- each double-chance selection combined with total multigol 1–2, 1–3,
  1–4, 2–3, 2–4, 2–5, 3–4 and 3–5.

Dependent probabilities are evaluated over individual score cells, never
multiplied as independent. For incomplete joint grids, only probability
bounds are published. Odds are never inferred or manufactured.

GA3 lambda, calibrators, historical weights and frozen engine SHA pins are
unchanged. GTS, RC15, MA3, BA3 native engines remain unmodified.

## CI evidence, scope and limits

- Run `41434190426`: existing offline DB tests passed, including a
  regression reproducing the original `QuotaClient.count` crash.
- Run `41434309817`: offline full-engine recovery workflow passed:
  26 DB/GA3/derived market tests, 56 native RC15 tests,
  3 independent wrapper tests, plus original RC15 offline example execution.
- All model tests used synthetic/historical local fixtures or bundled example
  prices. This is software validation, **not a live Deportivo–Osasuna
  prematch prediction** nor predictive calibration.
- The full 5-engine end-to-end command was **not** verified.
- Performance of the full live-API analysis **not measured**; the <60s goal
  cannot be certified using isolated CI tests.
- No original earlier merge baseline archive was present in this conversation;
  historical root causes beyond the trace above cannot be established.
- GitHub `pitchapi-warehouse` branch stores 2021/22–2026/27 shards for the
  five major leagues; no Spanish Segunda shard was visible in its Git tree.

## Engine status

| Engine | Confirmed in this repository | Verified this recovery |
|---|---|---|
| GA3 original RC2 mathematical core | SHA-pinned embedded archive and read-only adapter | Offline tests with SciPy passed; added markets test |
| GTS3 native engine | Not present; feature probes only | Not run |
| Bookmaker RC15 + Verdetto C | Original RC15 code and standalone wrapper | Native regression and offline execution passed |
| Market Analyzer 3.0 | Native engine not present | Not run |
| BetAnalyzer 3.0 | Native engine not present | Not run |

`BETPILOT: Deportivo La Coruña - Osasuna` remains **INCOMPLETE /
NOT VERIFIED**. No match fixture, quote or prediction is invented.
The command cannot be wired into ChatGPT and verified as a 5-engine live
pipeline until the missing native engine packages and authenticated runtime
are available.

These changes are intentionally isolated on a review branch. No destructive
merge, fake odds, alternate provider, or alteration of unrelated projects.

## Archived original packages discovered but not extracted

The Project Library lists the following older ZIP entries:
- `monnezzaai-main(1).zip`;
- `BETPILOT_GOAL_ANALYZER_3_0_RC2_PITCHAPI_ONLY_2026-11-01.zip`;
- `BETPILOT_GTS3_RC1_2026-11-01.zip`;
- `BETPILOT_MARKET_ANALYZER_3_0_NATIVE_RC2_VERIFIED_2026-11-01(1).zip`;
- `BETPILOT_BETANALYZER_3_0_RC1_RESEARCH_CHECKPOINT_2026-11-01(2).zip`.

However, the file access layer refused every attempted original-byte
materialization with `This Project file does not have an authorized raw-byte
materialization path`. Only filenames and metadata are confirmed; their
source files were **not** read, inspected, extracted or integrated.
They must be made available as actual readable ZIP uploads (or accessible
repository files) before a genuine 5-engine source recovery can proceed.
