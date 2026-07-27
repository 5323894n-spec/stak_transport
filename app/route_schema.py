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

CREATE TABLE IF NOT EXISTS route_depot_stops(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  direction TEXT NOT NULL CHECK(direction IN ('depot_out','depot_in')),
  stop_id INTEGER NOT NULL REFERENCES stops(id),
  sequence INTEGER NOT NULL,
  distance_from_prev_km REAL NOT NULL DEFAULT 0
    CHECK(distance_from_prev_km >= 0),
  run_time_day_sec INTEGER NOT NULL DEFAULT 0 CHECK(run_time_day_sec >= 0),
  run_time_night_sec INTEGER NOT NULL DEFAULT 0 CHECK(run_time_night_sec >= 0),
  source TEXT NOT NULL DEFAULT 'manual',
  source_detail TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(route_id,direction,sequence)
);
CREATE INDEX IF NOT EXISTS idx_route_depot_stops_stop
  ON route_depot_stops(stop_id);

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

CREATE TABLE IF NOT EXISTS route_stop_runtimes(
  id INTEGER PRIMARY KEY,
  route_stop_id INTEGER NOT NULL REFERENCES route_stops(id) ON DELETE CASCADE,
  period_id INTEGER NOT NULL REFERENCES day_periods(id) ON DELETE CASCADE,
  run_time_sec INTEGER NOT NULL CHECK(run_time_sec > 0),
  source TEXT NOT NULL DEFAULT 'manual',
  updated_at TEXT NOT NULL,
  UNIQUE(route_stop_id,period_id)
);

CREATE TABLE IF NOT EXISTS trip_stop_times(
  id INTEGER PRIMARY KEY,
  trip_id INTEGER NOT NULL REFERENCES route_trips(id) ON DELETE CASCADE,
  route_stop_id INTEGER NOT NULL REFERENCES route_stops(id) ON DELETE RESTRICT,
  sequence INTEGER NOT NULL,
  arrival_sec INTEGER NOT NULL CHECK(arrival_sec >= 0),
  departure_sec INTEGER NOT NULL CHECK(departure_sec >= arrival_sec),
  is_timing_point INTEGER NOT NULL DEFAULT 0,
  is_manual_override INTEGER NOT NULL DEFAULT 0,
  override_strategy TEXT,
  override_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(trip_id,sequence)
);
CREATE INDEX IF NOT EXISTS idx_trip_stop_times_trip
  ON trip_stop_times(trip_id,sequence);
CREATE INDEX IF NOT EXISTS idx_trip_stop_times_route_stop
  ON trip_stop_times(route_stop_id,departure_sec);

CREATE TABLE IF NOT EXISTS schedule_generation_previews(
  token TEXT PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  day_type TEXT NOT NULL,
  username TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  applied_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_schedule_generation_preview_scope
  ON schedule_generation_previews(route_id,day_type,username,created_at);

CREATE TABLE IF NOT EXISTS shift_types(
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  work_pattern TEXT NOT NULL DEFAULT 'custom',
  planned_duration_min INTEGER NOT NULL CHECK(planned_duration_min > 0),
  max_duration_min INTEGER NOT NULL CHECK(max_duration_min >= planned_duration_min),
  driver_slots INTEGER NOT NULL DEFAULT 1 CHECK(driver_slots IN (1,2)),
  allow_split INTEGER NOT NULL DEFAULT 0,
  color TEXT NOT NULL DEFAULT '#2563eb',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_shift_settings(
  id INTEGER PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  day_type TEXT NOT NULL,
  default_shift_type_id INTEGER NOT NULL REFERENCES shift_types(id),
  long_shift_type_id INTEGER REFERENCES shift_types(id),
  handover_min INTEGER NOT NULL DEFAULT 10 CHECK(handover_min >= 0),
  long_run_threshold_min INTEGER NOT NULL DEFAULT 720
    CHECK(long_run_threshold_min > 0),
  auto_split INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  UNIQUE(route_id,day_type)
);

CREATE TABLE IF NOT EXISTS output_shifts(
  id INTEGER PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  day_type TEXT NOT NULL,
  output_number INTEGER NOT NULL,
  shift_number INTEGER NOT NULL,
  shift_type_id INTEGER NOT NULL REFERENCES shift_types(id),
  trip_from_id INTEGER NOT NULL REFERENCES route_trips(id) ON DELETE CASCADE,
  trip_to_id INTEGER NOT NULL REFERENCES route_trips(id) ON DELETE CASCADE,
  start_sec INTEGER NOT NULL,
  end_sec INTEGER NOT NULL CHECK(end_sec > start_sec),
  driver_slots INTEGER NOT NULL DEFAULT 1 CHECK(driver_slots IN (1,2)),
  handover_after_min INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'generated',
  is_manual_locked INTEGER NOT NULL DEFAULT 0,
  manual_reason TEXT,
  generation_key TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(route_id,day_type,output_number,shift_number)
);
CREATE INDEX IF NOT EXISTS idx_output_shifts_scope
  ON output_shifts(route_id,day_type,output_number,shift_number);

CREATE TABLE IF NOT EXISTS shift_generation_previews(
  token TEXT PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  day_type TEXT NOT NULL,
  username TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  applied_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_shift_generation_preview_scope
  ON shift_generation_previews(route_id,day_type,username,created_at);
"""


def _add_column(con, table, name, definition):
    columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    if name not in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def migrate_route_network(con):
    con.executescript(ROUTE_NETWORK_SCHEMA)
    _add_column(
        con,
        "route_stops",
        "run_time_day_sec",
        "INTEGER NOT NULL DEFAULT 0 CHECK(run_time_day_sec >= 0)",
    )
    _add_column(
        con,
        "route_stops",
        "run_time_night_sec",
        "INTEGER NOT NULL DEFAULT 0 CHECK(run_time_night_sec >= 0)",
    )
    con.execute(
        """
        UPDATE route_stops
        SET run_time_day_sec=run_time_sec
        WHERE run_time_day_sec=0 AND run_time_sec>0
        """
    )
    con.execute(
        """
        UPDATE route_stops
        SET run_time_night_sec=run_time_sec
        WHERE run_time_night_sec=0 AND run_time_sec>0
        """
    )
    _add_column(
        con, "route_trips", "period_id", "INTEGER REFERENCES day_periods(id)"
    )
    _add_column(
        con, "route_trips", "source", "TEXT NOT NULL DEFAULT 'manual'"
    )
    _add_column(con, "route_trips", "generation_key", "TEXT")
    _add_column(
        con,
        "route_trips",
        "output_shift_id",
        "INTEGER REFERENCES output_shifts(id)",
    )
    _add_column(
        con,
        "roster_assignments",
        "output_shift_id",
        "INTEGER REFERENCES output_shifts(id)",
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_roster_assignments_output_shift_date "
        "ON roster_assignments(output_shift_id,date)"
    )
    con.executemany(
        """
        INSERT OR IGNORE INTO shift_types(
          code, name, work_pattern, planned_duration_min, max_duration_min,
          driver_slots, allow_split, color, active, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,1,datetime('now'),datetime('now'))
        """,
        [
            ("single_8h", "Одиночная 8 ч", "single", 480, 600, 1, 0, "#2563eb"),
            ("single_12h", "Одиночная 12 ч", "single", 720, 780, 1, 0, "#7c3aed"),
            ("split", "Разрывная", "split", 480, 600, 1, 1, "#ea580c"),
            ("two_driver_long", "Два водителя", "two_driver", 900, 1080, 2, 0, "#059669"),
        ],
    )
