# -*- coding: utf-8 -*-
"""Идемпотентная схема остановок и трасс маршрутов."""


ROUTE_NETWORK_SCHEMA = """
CREATE TABLE IF NOT EXISTS stops(
  id INTEGER PRIMARY KEY,
  external_code TEXT,
  name TEXT NOT NULL,
  latitude REAL,
  longitude REAL,
  address TEXT,
  stop_kind TEXT DEFAULT 'обычная',
  is_terminal INTEGER DEFAULT 0,
  has_dispatcher INTEGER DEFAULT 0,
  municipality TEXT,
  registry_flags TEXT DEFAULT '{}',
  source TEXT DEFAULT 'manual',
  active INTEGER DEFAULT 1,
  notes TEXT,
  created_at TEXT,
  updated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_stops_external_code
  ON stops(external_code) WHERE external_code IS NOT NULL AND external_code <> '';

CREATE TABLE IF NOT EXISTS route_stops(
  id INTEGER PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  direction TEXT NOT NULL CHECK(direction IN ('forward','backward')),
  stop_id INTEGER NOT NULL REFERENCES stops(id),
  sequence INTEGER NOT NULL,
  distance_from_prev_km REAL DEFAULT 0 CHECK(distance_from_prev_km >= 0),
  cumulative_km REAL DEFAULT 0 CHECK(cumulative_km >= 0),
  run_time_sec INTEGER DEFAULT 0 CHECK(run_time_sec >= 0),
  dwell_time_sec INTEGER DEFAULT 0 CHECK(dwell_time_sec >= 0),
  distance_source TEXT DEFAULT 'manual',
  boarding_allowed INTEGER DEFAULT 1,
  alighting_allowed INTEGER DEFAULT 1,
  is_timing_point INTEGER DEFAULT 0,
  source_detail TEXT,
  created_at TEXT,
  updated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_route_stops_direction_sequence
  ON route_stops(route_id,direction,sequence);
CREATE INDEX IF NOT EXISTS idx_route_stops_stop ON route_stops(stop_id);

CREATE TABLE IF NOT EXISTS route_migration_log(
  id INTEGER PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  source_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(route_id,source_hash)
);

CREATE TABLE IF NOT EXISTS route_import_previews(
  token TEXT PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  username TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  source_name TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  applied_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_route_import_previews_route
  ON route_import_previews(route_id,created_at);

CREATE TABLE IF NOT EXISTS day_periods(
  id INTEGER PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  day_type TEXT NOT NULL,
  name TEXT NOT NULL,
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  interval_min INTEGER NOT NULL,
  travel_time_factor REAL NOT NULL DEFAULT 1.0,
  transition_mode TEXT NOT NULL DEFAULT 'abrupt',
  transition_window_min INTEGER NOT NULL DEFAULT 0,
  color TEXT NOT NULL DEFAULT '#3b82f6',
  priority INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_day_periods_route_day
  ON day_periods(route_id,day_type,start_min,end_min);

CREATE TABLE IF NOT EXISTS period_templates(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS period_template_items(
  id INTEGER PRIMARY KEY,
  template_id INTEGER NOT NULL REFERENCES period_templates(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  interval_min INTEGER NOT NULL,
  travel_time_factor REAL NOT NULL DEFAULT 1.0,
  transition_mode TEXT NOT NULL DEFAULT 'abrupt',
  transition_window_min INTEGER NOT NULL DEFAULT 0,
  color TEXT NOT NULL DEFAULT '#3b82f6',
  priority INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_period_template_items_template
  ON period_template_items(template_id,start_min,priority);

CREATE TABLE IF NOT EXISTS period_previews(
  token TEXT PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  day_type TEXT NOT NULL,
  username TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  applied_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_period_previews_route_day
  ON period_previews(route_id,day_type,created_at);
"""


def migrate_route_network(con):
    con.executescript(ROUTE_NETWORK_SCHEMA)
