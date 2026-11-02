# BETPILOT | PREMATCH — Original engines recovery (test1)

**Isolation:** this test repository does not modify BETPILOT LIVEPICK or BUCO NERO. The recovery is developed on `recovery/prematch-five-engines-20261102`; no automatic merge.

## Original model preservation
Four exact original releases are stored in `betpilot_v3/frozen/` as ZIP archives. `betpilot_v3/SOURCE_ZIPS_SHA256.json` pins their byte-level SHA-256 hashes. `python betpilot_v3/run_replay.py --test` unpacks them, verifies their original manifests, and runs the unaltered tests. Bookmaker RC15 + Verdetto C, the original PitchAPI source adapters and V3 orchestration are also present. No model coefficients or formulas were modified by this transfer.

## One-command local execution
```sh
python -m betpilot_v3.command "BETPILOT: Deportivo La Coruña - Osasuna"
```

This command is **executable**, but a genuine analysis requires verified original PitchAPI warehouse/history and authentic Bet365/API-Sports quotes on the executing host. To configure **one time**, use protected environment/secrets `PITCHAPI_API_KEY` and `APISPORTS_KEY`, and persistent `BETPILOT_WAREHOUSE_ROOT` / `BETPILOT_HISTORY_ROOT` as described in `README_RECOVERY.md`. The keys are NOT committed. Fallback to other sports providers, web search, or invented fixture IDs/prices is forbidden.

## Verified vs pending
- GitHub Actions `recovery-offline-validation` tests the Bookmaker and GA3 engines with synthetic fixtures.
- GitHub Actions `prematch-v3-native-offline` checks the four frozen archives and attempts all five original engine test suites and a synthetic native replay.
- Real test `BETPILOT: Deportivo La Coruña - Osasuna`: **NOT YET VERIFIED**, no real fixture/odds/forecast asserted.
- Full one-command triggering **inside** ChatGPT requires a separate supported execution connector; GitHub code alone cannot make ChatGPT messages invoke private Python.
- Full three-way GA3/GTS3/Bookmaker concurrency and reliable under-60-second real-match performance are **not yet certified**.
- Original GA3/MA3 eligibility gates are still in some frozen releases and must not be silently bypassed.

CI never launches a match-data web search and the offline pipeline is intentionally not a scheduled data-collection workflow.
