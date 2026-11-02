# BETPILOT V3 — MA3 Native RC2 → BetAnalyzer 3.0 RC1 (research-only)

## Deployment scope

These are **four byte-identical frozen releases** and an external integration
bridge; GA3 RC2, GTS3 RC1, MA3 Native RC2, BA3 RC1, and RC15 + Verdict C
original source code remain **unmodified**. Legacy `vendor/BETPILOT_STEP5`
and old GA2/MA2 remain untouched but are **not imported** by the V3 runtime.

The four ZIPs are pinned by `SOURCE_ZIPS_SHA256.json` and checked at every
replay and verified-input launch. The GitHub release branch is safe for
synthetic offline replay and integration testing, **not** for real betting.

## Install & reproduce (from repository root)

```bash
python -m pip install -r requirements.txt pytest
python betpilot_v3/run_replay.py --test
python betpilot_v3/run_replay.py --output-dir /tmp/betpilot_v3_replay
```

Both runs use **synthetic** PitchAPI/Bet365 artifacts and write only to
specified output paths. `run_replay.py` extracts frozen sources to temporary
directories, compares SHA-256 and verifies BA3 native CLI parity. No network,
account, secret, stake, or live automation is required.

For the downstream controlled real-input test, first obtain authenticated
and event-aligned source files from their legitimate ingestion pipelines:

```bash
NUMBA_NUM_THREADS=4 python betpilot_v3/run_verified_inputs.py \
  --repo-root . \
  --match /safe/input/MATCH.json \
  --identity /safe/input/PITCHAPI_IDENTITY.json \
  --ga3-native /safe/input/GA3_NATIVE_PREDICT.json \
  --gts3-native /safe/input/GTS3_NATIVE_SCORE.json \
  --gts3-parameters /safe/input/GTS3_FROZEN_MODEL_PARAMS.json \
  --rc15-input /safe/input/RC15_EVENT_INPUT.json \
  --output-dir /safe/output/V3_RESEARCH
```

This entry point verifies the **native** GA3 and GTS3 export contracts, calls
RC15 + Verdict C using the existing repository adapter (without Step5
`run_full`), then MA3 and the immutable BA3 CLI. It does **not** itself
collect PitchAPI fixtures or Bet365 odds, nor guarantee externally supplied
capture timestamps. Claims of accurate real prematch picks/validated EV are
**not** supported by the replay.

## Scientific restrictions

- RESEARCH_ONLY: no automatic wagering, stake recommendation, or profit claim.
- GTS3 is diagnostic; it does not change GA3 probabilities, lambda or xG.
- Quote source provenance and missing out-of-sample calibration remain
  explicitly unverified; uncertainty is not fabricated.
- A mismatch in release SHA, version, event identity, cutoff, output manifest,
  market settlement, or independent quote evidence fails closed.
- BA3 may decide `NO_BET_INSUFFICIENT_MARKET_EVIDENCE` and that is expected.
- Current synthetic replay **does not** certify validated real-data accuracy.

## Status

The original separate release checkpoint reported 184 PASS / 1 SKIP across
its local unit-test suites. GitHub Actions on this branch must independently
pass the newly installed native replay before the integration can be called
CI-verified. Production activation and new FULL ANALYSIS command wiring are
**out of scope**; they need a separate deliberate change after data-source
verification and backtesting.
