# BETPILOT PREMATCH — Original PitchAPI SQLite native source adapter

- Uses the source branch `pitchapi-warehouse` of the original private repository **read-only**. No modifications are made to that repository or to BUCO NERO or LIVEPICK.
- The GitHub Actions secret `PITCHAPI_SOURCE_READ_TOKEN` must grant source repository Contents: Read only. Source data are imported into an ephemeral runner directory, never committed to public `test1` or uploaded as an artifact.
- `betpilot_v3/sqlite_native_export.py` verifies every original gzipped PitchAPI `/stats` response by its stored SHA-256 and byte size; matches the provider fixture ID, team IDs, league and season; and runs the original, hash-pinned **GTS3 native `extract_stats` parser** without modifying it.
- Real historical xG comes from the **team `/stats` All `expected_goals` metric** and real shot counts from `total_shots`; not per-shot reconstructed expected goals. This change of measurement versus the old GA3 archive is reported explicitly; all original GA3 mathematical formulas and GTS3 parameters remain byte-identical.
- Converts validated historical observations into ephemeral GA3 history JSON and GTS3 team-stats archive JSON. Missing, late, invalid or contradicted records are omitted or rejected. No missing metric, date, quote or match is synthesized.
- The original pre-match cutoff, data-lag, frozen GTS3 sample minima, cross-provider fixture equality, and Bet365 odds provenance checks still apply. A lack of true 7-market Bet365 odds is an explicit no-analysis result, not a fake prediction.
- The current private source DB provides two recent seasons in each of five first-tier leagues; it does not cover every league. An arbitrary `BETPILOT: Team Home - Team Away` command may be syntactically valid even when data for its competition are not available.
- The source was captured historically, not before each historical match, and is not an independently verified original prematch snapshot. Native engine replay success is not out-of-sample predictive validation.

Run one-time read-only materialization:

```sh
python -m betpilot_v3.sqlite_native_export --sqlite-root private_pitchapi/data --history-root /tmp/betpilot-history --team-stats-root /tmp/betpilot-team-stats
```

The one-command `python -m betpilot_v3.command 'BETPILOT: Home Team - Away Team'` now invokes the same ephemeral conversion automatically if valid provider keys and only original SQLite archives exist. Result remains no-bet unless all five original engines actually execute on verified same-match data.
