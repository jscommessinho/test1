"""Read-only warehouse coverage report; no network, no API keys required."""
from __future__ import annotations
import argparse
import json
import sqlite3
from pathlib import Path

def audit(warehouse: Path) -> dict:
    output = {"leagues": {}, "total_finished_matches": 0,
              "total_source_responses": 0, "seasons": 0}
    for db_file in sorted((warehouse / "data").glob("*/*.sqlite")):
        league, season = db_file.parent.name, db_file.stem.replace("-", "/")
        connection = sqlite3.connect(f"file:{db_file.absolute().as_posix()}?mode=ro", uri=True)
        try:
            valid = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if valid != "ok":
                raise RuntimeError(f"Database integrity failed: {db_file}")
            finished = connection.execute(
                """SELECT COUNT(*) FROM fixtures WHERE lower(fixture_status) IN
                ('finished','after_extra_time','after_penalties')"""
            ).fetchone()[0]
            versions = connection.execute("SELECT COUNT(*) FROM source_responses").fetchone()[0]
            pending = connection.execute(
                """SELECT COUNT(*) FROM fixtures f WHERE lower(f.fixture_status) IN
                ('finished','after_extra_time','after_penalties') AND
                EXISTS (SELECT 1 FROM (SELECT 'match_summary' AS dataset UNION ALL
                SELECT 'stats' UNION ALL SELECT 'events' UNION ALL SELECT 'lineups'
                UNION ALL SELECT 'momentum' UNION ALL SELECT 'advanced') d
                WHERE NOT EXISTS (SELECT 1 FROM endpoint_coverage e WHERE
                  e.dataset=d.dataset AND e.resource_key=f.pitchapi_fixture_id
                  AND e.state IN ('present','empty','unavailable')))"""
            ).fetchone()[0]
            output["leagues"].setdefault(league, {})[season] = {
                "finished_matches": finished, "stored_response_versions": versions,
                "fixtures_with_missing_endpoint_checks": pending
            }
            output["seasons"] += 1
            output["total_finished_matches"] += finished
            output["total_source_responses"] += versions
        finally:
            connection.close()
    return output

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit archived PitchAPI data")
    parser.add_argument("--warehouse", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(audit(args.warehouse), indent=2, sort_keys=True))
