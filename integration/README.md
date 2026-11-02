# BET PILOT — Goal Analyzer RC2.post2 | Approved control revisions

IMPORTANT: The revised Goal Analyzer is NOT byte-identical to the original RC2. The approved rules change model eligibility. Download and install the revised ZIP delivered in the conversation; version 3.0.0rc2.post2.

This GitHub repository contains the corresponding read-only SQLite adapter, not the complete modified Goal Analyzer engine. No team-match minimum is enforced. Only an entirely empty league training dataset cannot be fitted; the original rc2 is not the revised version.

## Updated behavior

1. Competition names / slugs / official PitchAPI IDs are resolved by unique lookup in provider database mappings; ambiguous records still block.
2. Missing league ID is recovered from the official PitchAPI catalogue when possible; conflicting IDs block.
3. Missing seasons are reconstructed only when provider-registered seasons and dated fixture calendars support a unique identification. Otherwise the fixture is excluded.
4. Identical match-ID duplicates are ignored; contradictory duplicates block.
5. No fixed three-day result waiting period.
6. No first-of-month training cutoff. Every finished historical match prior to the analysis instant is eligible, including the current month.
7. No minimum number of historical matches for either team, across seasons or for the league. Model fitting requires at least one valid match somewhere in the league archive.

Only GOALS are read for GA3 modeling; xG/xGA is never read or applied; nonzero xg_blend is prohibited. Existing Poisson/ridge/probability mathematics remain unchanged.

## Example

    python -m integration.ga3_sqlite_adapter --warehouse /private/pitchapi-warehouse --league 'Premier League' --as-of 2026-11-01T19:00:00Z --list-teams
    python -m integration.ga3_sqlite_adapter --warehouse /private/pitchapi-warehouse --league eng-premier --home ACTUAL_HOME_ID --away ACTUAL_AWAY_ID --as-of 2026-11-01T19:00:00Z

## Boundaries and verification

- Strictly read-only adapter; no changes to PitchAPI historical archive, the collector, any scheduling, other analyzers, BUCO NERO or LIVEPICK.
- The revised standalone package passed 22 adjusted original tests and 10 new rule tests (32/32).
- Repository CI verifies syntax and policy of the adapter, not the revised standalone engine package.
- Research status retained; no independent real-data predictive validation or historic source-publication proof.
