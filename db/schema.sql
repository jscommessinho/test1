-- BET PILOT | Prematch Analyzer
-- SQLite v4. All provider-discovered seasons are supported; NO season cap.
-- Applying schema makes NO PitchAPI API requests.
-- Only chosen team-level and match-level records are stored.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS leagues (
  slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  country_code TEXT NOT NULL,
  pitchapi_league_id TEXT UNIQUE,
  created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS seasons (
  league_slug TEXT NOT NULL,
  season_label TEXT NOT NULL,
  pitchapi_season_id TEXT,
  state TEXT NOT NULL DEFAULT 'not_started'
    CHECK (state IN ('not_started','partial','verified_complete')),
  PRIMARY KEY (league_slug, season_label),
  FOREIGN KEY (league_slug) REFERENCES leagues(slug)
);

CREATE TABLE IF NOT EXISTS teams (
  pitchapi_team_id TEXT PRIMARY KEY,
  team_name TEXT,
  raw_json TEXT CHECK (raw_json IS NULL OR json_valid(raw_json)),
  last_seen_utc TEXT
);

CREATE TABLE IF NOT EXISTS fixtures (
  pitchapi_fixture_id TEXT PRIMARY KEY,
  league_slug TEXT NOT NULL,
  season_label TEXT NOT NULL,
  kickoff_utc TEXT,
  home_team_id TEXT,
  away_team_id TEXT,
  fixture_status TEXT,
  home_goals INTEGER,
  away_goals INTEGER,
  raw_json TEXT CHECK (raw_json IS NULL OR json_valid(raw_json)),
  last_seen_utc TEXT,
  FOREIGN KEY (league_slug, season_label) REFERENCES seasons(league_slug, season_label),
  FOREIGN KEY (home_team_id) REFERENCES teams(pitchapi_team_id),
  FOREIGN KEY (away_team_id) REFERENCES teams(pitchapi_team_id)
);

CREATE INDEX IF NOT EXISTS idx_fixtures_competition_season
  ON fixtures(league_slug, season_label, kickoff_utc);
-- Sync state for a FUTURE collector. Creating this table starts no jobs.
CREATE TABLE IF NOT EXISTS sync_checkpoints (
  league_slug TEXT NOT NULL,
  season_label TEXT NOT NULL,
  dataset TEXT NOT NULL,
  next_cursor TEXT,
  last_success_utc TEXT,
  state TEXT NOT NULL DEFAULT 'not_started'
    CHECK (state IN ('not_started','partial','blocked','verified_complete')),
  PRIMARY KEY (league_slug, season_label, dataset),
  FOREIGN KEY (league_slug, season_label) REFERENCES seasons(league_slug, season_label)
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
  run_id TEXT PRIMARY KEY,
  started_at_utc TEXT NOT NULL,
  ended_at_utc TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','success','partial','failed')),
  source TEXT NOT NULL,
  items_attempted INTEGER NOT NULL DEFAULT 0,
  items_inserted INTEGER NOT NULL DEFAULT 0,
  error_summary TEXT
);

-- Counts are not proof of full statistical coverage.
CREATE TABLE IF NOT EXISTS coverage_targets (
  league_slug TEXT NOT NULL,
  season_label TEXT NOT NULL,
  expected_fixtures INTEGER CHECK (expected_fixtures IS NULL OR expected_fixtures >= 0),
  expectation_source TEXT,
  verified_at_utc TEXT,
  PRIMARY KEY (league_slug, season_label),
  FOREIGN KEY (league_slug, season_label) REFERENCES seasons(league_slug, season_label)
);

INSERT OR IGNORE INTO leagues (slug, name, country_code) VALUES
  ('eng-premier', 'Premier League', 'ENG'),
  ('ita-seriea', 'Serie A', 'ITA'),
  ('fra-ligue1', 'Ligue 1', 'FRA'),
  ('esp-laliga', 'La Liga', 'ESP'),
  ('ger-bundesliga', 'Bundesliga', 'GER');

-- No fixed year list. Seasons are registered dynamically from imported
-- provider catalogue or actual sourced match data. Existing seasons are preserved.

PRAGMA user_version = 4;
