"""Policy v4: only team/match data, no individual shot or player stats."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from db.manage import initialize, ingest_json, register_season, import_seasons, verify
from db.pitchapi_store import restore_bytes, archive_bytes, check_dataset

EXPECTED_TABLES = {
    "leagues","seasons","teams","fixtures","players",
    "source_responses","catalogue_snapshots","match_team_stats",
    "match_events","lineup_snapshots","match_momentum","advanced_metrics",
    "endpoint_coverage","coverage_targets","sync_checkpoints","ingestion_runs",
}

class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "pitchapi.sqlite"
        initialize(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_only_selected_tables_exist(self):
        with sqlite3.connect(self.db) as con:
            tables = {row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            self.assertEqual(tables, EXPECTED_TABLES)
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 4)
        verify(self.db)

    def test_unlimited_seasons(self):
        for year in range(1980, 2050):
            self.assertTrue(register_season(self.db, "eng-premier", f"{year}/{year+1}"))
        self.assertFalse(register_season(self.db, "eng-premier", "1980/1981"))
        initialize(self.db)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM seasons").fetchone()[0], 70)

    def test_import_catalogue_without_guessing_seasons(self):
        source = self.root/"catalogue.json"
        source.write_text(json.dumps({"seasons":[
            {"league_slug":"ita-seriea","season_label":"2020/2021"},
            {"league_slug":"ita-seriea","season_label":"2055/2056"}
        ]}),encoding="utf-8")
        self.assertEqual(import_seasons(self.db,source),2)
        self.assertEqual(import_seasons(self.db,source),0)

    def test_approved_payload_is_lossless_and_deduplicated(self):
        file = self.root/"source.json"
        original = b'{"data":{"periods":[],"unknown_team_key":42}}'
        file.write_bytes(original)
        args=dict(league="eng-premier",season="2021/2022",dataset="stats",
                  resource_key="m_1",source_file=file,fixture_id="m_1",
                  endpoint="/v1/matches/m_1/stats")
        self.assertTrue(ingest_json(self.db,**args))
        self.assertFalse(ingest_json(self.db,**args))
        self.assertEqual(restore_bytes(self.db,1),original)

    def test_disallowed_data_rejected_without_side_effects(self):
        for dataset, endpoint in [
            ("shots","/v1/matches/m_1/shots"),
            ("players","/v1/matches/m_1/players"),
            ("player_stats","/v1/matches/m_1/player-stats"),
            ("heatmaps","/v1/matches/m_1/heatmaps"),
            ("advanced","/v1/matches/m_1/players/advanced"),
        ]:
            with self.subTest(dataset=dataset):
                with self.assertRaises(ValueError):
                    archive_bytes(self.db,league="eng-premier",season="2021/2022",
                                  dataset=dataset,resource_key="m_1",endpoint=endpoint,
                                  raw=b'{"data":{}}')
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM source_responses").fetchone()[0],0)

if __name__=="__main__":
    unittest.main()
