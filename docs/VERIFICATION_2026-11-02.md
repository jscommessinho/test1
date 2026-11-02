# BETPILOT PREMATCH — verified checkpoint, 2026-11-02

## Correct repository / isolation

- User-selected destination: `jscommessinho/test1` (**public repository**).
- Source restoration branch: `recovery/prematch-five-engines-20261102`.
- Draft pull request: https://github.com/jscommessinho/test1/pull/4.
- `main` contains the owner-only GitHub Issues launcher and the separate official-provider preflight only.
- LIVEPICK and BUCO NERO were not modified. Historical PitchAPI databases from the private source repo were not copied or published.

## Tests on live GitHub Actions

| Verification | Evidence | Observed result |
|---|---|---|
| Restored GA3 and standalone native RC15 + source adapter | https://github.com/jscommessinho/test1/actions/runs/41448380914 | SUCCESS |
| Four frozen original ZIPs, model suites and synthetic five-engine native handoff | https://github.com/jscommessinho/test1/actions/runs/41449688988 | SUCCESS |
| Same full synthetic handoff plus exact Deportivo–Osasuna fail-closed launch | https://github.com/jscommessinho/test1/actions/runs/41449956492 | SUCCESS |
| ChatGPT created owner issue → GitHub Actions → real Python executable → automatic ticket comment | https://github.com/jscommessinho/test1/actions/runs/41450537153 | SUCCESS (launcher routing, *not a prediction*) |
| Real requested command status | https://github.com/jscommessinho/test1/issues/2#issuecomment-6577960515 | DATA_UNAVAILABLE / PERSISTENT_PITCHAPI_ARCHIVES_MISSING |
| Authorized PitchAPI and API-Sports credentials preflight, max 1 request each | https://github.com/jscommessinho/test1/issues/3#issuecomment-6578031193 | PROVIDER_CONNECTIVITY_VERIFIED |

Provider authentication is verified from the GitHub runner. No API key appears in the code, public issues, or generated reports. GitHub Actions secrets must remain protected.

## Genuine software restored

- GA3 original RC2 (original λ mathematics unchanged).
- GTS3 original RC1.
- Bookmaker original RC15 + Verdetto C, including confirmed correction of the transport `QuotaClient.used`/legacy `count` error without altering price-model coefficients.
- Market Analyzer original Native RC2.
- BetAnalyzer original RC1.
- Original frozen ZIPs protected by their byte-identical SHA-256 manifest.
- Native synthetic handoff exercised the five original engines together. Research fixtures and synthetic bookmaker price inputs are **not** real odds for Deportivo–Osasuna.
- Additional model-only dependent markets use the original joint score distribution, but cannot be elevated to priced BA3 decisions without authentic market-specific odds.

## Explicitly UNFINISHED

1. **Full genuine Deportivo La Coruña–Osasuna analysis: NOT VERIFIED.** Fixture ID, date, competition, time-stamped Bet365 prices, and calibrated λ were not obtained for that exact game. Never substitute a synthetic fixture.
2. **Historical data storage:** Runner's `warehouse` and `history` directories are empty. Existing original private PitchAPI branch includes only five top divisions (England Premier League, Spain La Liga, Germany Bundesliga, France Ligue 1, Italy Serie A) and does not directly supply Deportivo's competition. The existing SQLite schema is also not plug-compatible with the verified V3 original history bundles. Do not paste private data into public `test1`.
3. **Raw authenticated fixture lookup beyond today:** not validated for requested date because none supplied; current launcher needs a verified local index or matching bundle for non-today fixtures.
4. **Parallelism:** original GA3 and GTS3 run concurrently in verified native phase, but a fully independent three-way parallel GA3/GTS3/Bookmaker execution is not proven in live end-to-end mode.
5. **Original frozen eligibility gates** such as the three-day historical lag and hard sample minima remain in original frozen engines. No covert bypass; source modifications would require versioned parity and regression testing.
6. **Speed** target <60s for a complete live match is not validated; workflow startup / dependency setup is independent of measured model runtime.
7. **ChatGPT control:** ChatGPT may create an owner GitHub issue `BETPILOT: Home - Away` through the linked GitHub connector; GitHub executes and posts a result. A plain message does not automatically invoke this action unless the assistant starts that linked workflow. No magical local Python execution is implied.

## Data safety

- This is a public repository. Do not commit private PitchAPI archives, provider keys, restricted datasets, pricing cache or sportsbook account data.
- Upload original new warehouse data **only to an approved private durable store**; add authorized, read-only source provisioning to this runner; map the actual fixture and validate source schema/cutoff. Once done, rerun exact acceptance.
- No web searches, substitute providers, invented numbers or real-money betting execution.
- Do not merge draft PR until original frozen release parity and real acceptance tests have been independently reviewed.

**Verdict: working recovery checkpoint with authenticated providers and a real ChatGPT→GitHub dispatcher, NOT FULL PRODUCTION ACCEPTANCE.**
