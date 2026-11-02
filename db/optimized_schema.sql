-- BET PILOT / PitchAPI v4: team/match-only schema.
-- Apply after db/schema.sql (v4). No schema object here restricts the number of seasons.
-- Raw original API JSON is compressed and versioned; normalized tables are query indexes,
-- never a replacement for the original response.

CREATE TABLE IF NOT EXISTS source_responses (
  response_id INTEGER PRIMARY KEY,
  league_slug TEXT NOT NULL,
  season_label TEXT NOT NULL,
  dataset TEXT NOT NULL,
  resource_key TEXT NOT NULL,
  match_id TEXT,
  endpoint TEXT NOT NULL,
  fetched_at_utc TEXT NOT NULL,
  sha256_hex TEXT NOT NULL CHECK(length(sha256_hex)=64),
  compression TEXT NOT NULL DEFAULT 'gzip' CHECK(compression='gzip'),
  original_bytes INTEGER NOT NULL CHECK(original_bytes>=0),
  compressed_bytes INTEGER NOT NULL CHECK(compressed_bytes>=0),
  body_gzip BLOB NOT NULL,
  UNIQUE(league_slug,season_label,dataset,resource_key,sha256_hex),
  FOREIGN KEY(league_slug,season_label) REFERENCES seasons(league_slug,season_label)
);
-- Database-level protection against individual shot/player stats or unapproved endpoints.
CREATE TRIGGER IF NOT EXISTS tr_source_dataset_allowed_insert
BEFORE INSERT ON source_responses
WHEN NEW.dataset NOT IN ('league_matches','match_summary','stats','events','lineups','momentum','advanced')
  OR lower(NEW.endpoint) LIKE '%/shots%'
  OR lower(NEW.endpoint) LIKE '%/players%'
  OR lower(NEW.endpoint) LIKE '%/heatmaps%'
  OR lower(NEW.endpoint) LIKE '%/pass-network%'
BEGIN
  SELECT RAISE(ABORT, 'dataset excluded by selected BET PILOT policy');
END;
CREATE TRIGGER IF NOT EXISTS tr_source_dataset_allowed_update
BEFORE UPDATE OF dataset,endpoint ON source_responses
WHEN NEW.dataset NOT IN ('league_matches','match_summary','stats','events','lineups','momentum','advanced')
  OR lower(NEW.endpoint) LIKE '%/shots%'
  OR lower(NEW.endpoint) LIKE '%/players%'
  OR lower(NEW.endpoint) LIKE '%/heatmaps%'
  OR lower(NEW.endpoint) LIKE '%/pass-network%'
BEGIN
  SELECT RAISE(ABORT, 'dataset excluded by selected BET PILOT policy');
END;

CREATE INDEX IF NOT EXISTS ix_source_season_kind
 ON source_responses(league_slug,season_label,dataset);
CREATE INDEX IF NOT EXISTS ix_source_match
 ON source_responses(match_id,dataset,fetched_at_utc);
CREATE INDEX IF NOT EXISTS ix_source_resource_latest
 ON source_responses(league_slug,season_label,dataset,resource_key,fetched_at_utc DESC);

CREATE TABLE IF NOT EXISTS players (
  pitchapi_player_id TEXT PRIMARY KEY,
  player_name TEXT,
  last_seen_utc TEXT
);
-- Team stats are strings in PitchAPI, including fractions and percentages.
-- Both the exact original string and a nullable parsed numeric value are retained.
CREATE TABLE IF NOT EXISTS match_team_stats (
  match_id TEXT NOT NULL,
  team_id TEXT NOT NULL,
  period TEXT NOT NULL,
  group_key TEXT NOT NULL,
  metric_key TEXT NOT NULL,
  format_type TEXT,
  display_value TEXT,
  numeric_value REAL,
  source_response_id INTEGER NOT NULL,
  PRIMARY KEY(match_id,team_id,period,group_key,metric_key),
  FOREIGN KEY(match_id) REFERENCES fixtures(pitchapi_fixture_id),
  FOREIGN KEY(source_response_id) REFERENCES source_responses(response_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_team_stats_metric ON match_team_stats(metric_key,period,match_id);

-- Events have no guaranteed globally unique ID; provider sequence is scoped by match.
CREATE TABLE IF NOT EXISTS match_events (
  match_id TEXT NOT NULL,
  event_ordinal INTEGER NOT NULL,
  event_type TEXT,
  period TEXT,
  minute REAL,
  minute_added REAL,
  team_id TEXT,
  player_id TEXT,
  raw_event_json TEXT NOT NULL CHECK(json_valid(raw_event_json)),
  source_response_id INTEGER NOT NULL,
  PRIMARY KEY(match_id,event_ordinal),
  FOREIGN KEY(match_id) REFERENCES fixtures(pitchapi_fixture_id),
  FOREIGN KEY(source_response_id) REFERENCES source_responses(response_id)
) WITHOUT ROWID;

-- Preserve predicted and confirmed lineups as distinct source snapshots.
CREATE TABLE IF NOT EXISTS lineup_snapshots (
  match_id TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('home','away')),
  source_response_id INTEGER NOT NULL,
  confirmed INTEGER CHECK(confirmed IN (0,1) OR confirmed IS NULL),
  lineup_type TEXT,
  formation TEXT,
  raw_side_json TEXT NOT NULL CHECK(json_valid(raw_side_json)),
  PRIMARY KEY(match_id,side,source_response_id),
  FOREIGN KEY(match_id) REFERENCES fixtures(pitchapi_fixture_id),
  FOREIGN KEY(source_response_id) REFERENCES source_responses(response_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS match_momentum (
  match_id TEXT NOT NULL,
  point_ordinal INTEGER NOT NULL,
  minute REAL,
  pressure_value REAL,
  source_response_id INTEGER NOT NULL,
  PRIMARY KEY(match_id,point_ordinal),
  FOREIGN KEY(match_id) REFERENCES fixtures(pitchapi_fixture_id),
  FOREIGN KEY(source_response_id) REFERENCES source_responses(response_id)
) WITHOUT ROWID;

-- Coverage is measured separately for each endpoint/resource; a 200 with an
-- empty array is different from an endpoint not yet requested.
CREATE TABLE IF NOT EXISTS endpoint_coverage (
  league_slug TEXT NOT NULL,
  season_label TEXT NOT NULL,
  dataset TEXT NOT NULL,
  resource_key TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('present','empty','unavailable','error')),
  last_checked_at_utc TEXT NOT NULL,
  source_response_id INTEGER,
  note TEXT,
  PRIMARY KEY(league_slug,season_label,dataset,resource_key),
  FOREIGN KEY(league_slug,season_label) REFERENCES seasons(league_slug,season_label),
  FOREIGN KEY(source_response_id) REFERENCES source_responses(response_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_coverage_state
 ON endpoint_coverage(league_slug,season_label,state,dataset);


-- Dynamic league catalogue may not have a season yet.
CREATE TABLE IF NOT EXISTS catalogue_snapshots (
  snapshot_id INTEGER PRIMARY KEY,
  fetched_at_utc TEXT NOT NULL,
  sha256_hex TEXT NOT NULL UNIQUE CHECK(length(sha256_hex)=64),
  original_bytes INTEGER NOT NULL,
  body_gzip BLOB NOT NULL
);

-- Flexible advanced analytics (xT, VAEP, PPDA, field tilt, etc.) by subject.
CREATE TABLE IF NOT EXISTS advanced_metrics (
  match_id TEXT NOT NULL,
  subject_type TEXT NOT NULL CHECK(subject_type = 'team'),
  subject_id TEXT NOT NULL,
  metric_path TEXT NOT NULL,
  metric_value REAL,
  source_response_id INTEGER NOT NULL,
  PRIMARY KEY(match_id,subject_type,subject_id,metric_path),
  FOREIGN KEY(match_id) REFERENCES fixtures(pitchapi_fixture_id),
  FOREIGN KEY(source_response_id) REFERENCES source_responses(response_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_advanced_metric ON advanced_metrics(metric_path,match_id);

-- Enforce team-only advanced indexes even when upgrading an older v3 schema.
CREATE TRIGGER IF NOT EXISTS tr_advanced_team_only_insert
BEFORE INSERT ON advanced_metrics
WHEN NEW.subject_type <> 'team'
BEGIN
  SELECT RAISE(ABORT, 'individual player metrics excluded');
END;
CREATE TRIGGER IF NOT EXISTS tr_advanced_team_only_update
BEFORE UPDATE OF subject_type ON advanced_metrics
WHEN NEW.subject_type <> 'team'
BEGIN
  SELECT RAISE(ABORT, 'individual player metrics excluded');
END;
