# BETPILOT | STEP 5 — Operational Readiness + FootyStats → GA2

**Status:** local software pipeline verified. **No authenticated HTTP run performed in the present session.** Sources were tested offline; all output is `RESEARCH_ONLY`. Frozen GA2/RC15/MA2 and Step4 adapter remain unchanged.

## 1. What has been added

- `footystats_ga2_adapter.py` — import FootyStats **league-matches JSON** or same-column CSV, validate completed matches and cutoff, build frozen original GA2 inputs, call original GA2 code *without changing its formulas or locked hyperparameters*. It uses goal counts, not post-match xG of the target fixture.
- `step5_operational.py` — join an **already validated RC15_EVENT_INPUT.json** from API-Football with a **verified cross-provider team-ID mapping** and GA2 history. Execute native RC15 → MA2 → BetAnalyzer. Output explicitly `RESEARCH_ONLY`, never betting-eligible.
- `.github/workflows/betpilot_prematch_manual.yml` — optional manual-only live API-Football smoke test using repository secret `APISPORTS_KEY` (GitHub repository dedicated to PREMATCH; **do not add to the BUCO NERO repository**).
- `tests/test_step5_pipeline.py` — controlled synthetic-input tests, including fully native end-to-end execution. Synthetic data do not establish real-world predictive accuracy.

## 2. Data source reality: FootyStats Premium ≠ FootyStats API

FootyStats offers a dedicated paid API (see https://footystats.org/api/documentations/setup-the-api). Premium website login does not by itself provide the API key. There are TWO supported acquisition paths:

**A. No extra subscription:** export historical match data from your authenticated Premium site and convert them to the documented fields `id,homeID,awayID,date_unix,status,homeGoalCount,awayGoalCount,competition_id,season` in CSV/JSON. CSV columns from the website may differ and require mapping; never invent missing columns. Provide **multiple seasons from the same competition** sufficient for >=500 completed historical matches. The source file is never fetched automatically in this chat without an authorized session/browser.

**B. Dedicated FootyStats API key:** provision private environment variable `FOOTYSTATS_API_KEY`; use `--fetch-season-ids ID1 ID2 ...` with `footystats_ga2_adapter.py`. Requires separate API authorization and subscription; the free `example` key cannot be used for arbitrary leagues. Access is NOT assumed.

API reference: https://footystats.org/api/documentations/match-schedule-and-stats (league-matches); https://footystats.org/api/documentations/league-teams (team IDs). `league-matches` includes `date_unix` **for kickoff**, not necessarily date of result publication or delayed completion. This release conservatively applies a fixed 2-day lag and marks historical point-in-time provenance `false`, excluding suspended/incomplete rows. It is not proof of exact availability. The frozen GA2 model is fitted as of the **first day of the month** of the analysis cutoff, on the available prior history.

Team identity: FootyStats `homeID`/`awayID` are NOT interchangeable with API-Football team IDs. Create a `team_map.json` mapping FootyStats IDs to verified API-Football club names. Mandatory operator attestation prevents silent identity conflation. Do not use fixture IDs to infer team IDs.

## 3. Smoke test: API-Football Bet365 only (no FootyStats needed)

The original Step4 command is unchanged:

```bash
python api_football_adapter.py --fixture-id FUTURE_FIXTURE_ID --max-requests 2 --output-dir ./live_run --run-native
```

`APISPORTS_KEY` must be present in the process environment via a **secret manager**. Never paste your API key into ChatGPT, a terminal command shared publicly, or repository files. The command makes 2 authenticated GET calls if successful: `/fixtures` and `/odds?fixture=...&bookmaker=8`. Captures include timestamps and hashes, not API credentials.

**Optional GitHub Actions, manual only:** create/use a separate **private BETPILOT PREMATCH repository** containing this package at repository root; configure **Settings → Secrets and variables → Actions → New repository secret** named `APISPORTS_KEY`; visit **Actions → BETPILOT PREMATCH — Manual Bet365 smoke test → Run workflow**, supplying a *future* fixture ID. The workflow is not active just because this ZIP exists. It runs only after repository setup and explicit dispatch. Inspect run artifacts, not raw secret.

## 4. GA2-only research calculation from FootyStats history

Convert genuine league-match records to the supported JSON or CSV fields (see above). Example:

```bash
python footystats_ga2_adapter.py \
  --history exported_2024.json exported_2025.json exported_2026.json \
  --home-fs-id FOOTYSTATS_HOME_TEAM_ID --away-fs-id FOOTYSTATS_AWAY_TEAM_ID \
  --home "Exact API-Football Home Name" --away "Exact API-Football Away Name" \
  --event-id "API-FOOTBALL-fixture-FIXTURE_ID" \
  --kickoff "2026-11-03T19:00:00+00:00" \
  --cutoff "2026-11-02T12:00:00+00:00" \
  --output-dir ./ga2_research_run
```

This standalone command trusts the identity mapping provided by the operator. For full integration **use step5_operational.py**, which requires the mapping file and matches against the RC15 event automatically.

If a separate FootyStats API key is legitimately configured, use `--fetch-season-ids` instead of `--history` (observe API coverage, 1 call per season, and pagination limitations). The command outputs `GA2_RESEARCH_OUTPUT.json` and `GA2_INPUT_AUDIT.json`. Data and model limitations remain explicit.

## 5. Full integration (once the two data sources are available)

First run Step4 to get verified `RC15_EVENT_INPUT.json` for the same fixture. Then supply historical match records and a verified mapping file:

```json
{
  "mapping_verified_by_operator": true,
  "competition": "Exact competition string from RC15_EVENT_INPUT.json",
  "teams": {"FOOTYSTATS_HOME_ID": "Exact API-Football Home Name", "FOOTYSTATS_AWAY_ID": "Exact API-Football Away Name"}
}
```

```bash
python step5_operational.py \
  --rc15-input ./live_run/RC15_EVENT_INPUT.json \
  --history exported_2024.json exported_2025.json exported_2026.json \
  --team-map ./team_map.json \
  --output-dir ./integrated_run
```

Expected `native_full/BETANALYZER_ANALYSIS.json` with status `FULL_NATIVE_EXECUTION_RESEARCH_ONLY`; not proof of model performance. If <500 complete prior matches or either team is unseen, the pipeline **abstains** rather than producing spurious lambda values.

## 6. Limits, next actions

- No private credentials accessible in the present execution environment. Live authenticated HTTP calls have not been attempted or passed.
- FootyStats Premium website sessions in other Work chats are **not automatically carried over** to this runtime.
- Cross-season continuity and same-league provenance must be verified; merely combining arbitrary match files can invalidate a model.
- Original GA2 was developed mainly on Serie A, and **new leagues are not scientifically validated**. Goal intensities are strictly experimental and are never certified winning probabilities.
- GTS is not connected; HT/2H are not certified. MA2 rankings are descriptive.
- API-Football prematch odds are normally provided up to 14 days before kickoff, updated around every three hours, with brief historical retention. Capture timestamps and preserve original snapshots if future validation is desired.
- For true automatic daily `ANALYSIS` in Work, it must have actual execution plus access to both the private secret and a dataset export or authorized FootyStats session. This ZIP does not grant credentials automatically.

**Decision:** software pipeline ready for external deployment/credentialed pilot; NOT yet live-verified and NOT promoted to operational prediction status.
