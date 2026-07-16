# -*- coding: utf-8 -*-


def test_stage_three_schema_is_repeat_safe(tmp_path):
    from app import db

    db.DB_PATH = str(tmp_path / "stage-three.db")
    db.init_db()
    db.init_db()
    con = db.connect()
    try:
        tables = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "trip_stop_times",
            "route_stop_runtimes",
            "schedule_generation_previews",
        } <= tables

        stop_time_columns = {
            row[1] for row in con.execute("PRAGMA table_info(trip_stop_times)")
        }
        assert {
            "trip_id",
            "route_stop_id",
            "sequence",
            "arrival_sec",
            "departure_sec",
            "is_timing_point",
            "is_manual_override",
            "override_strategy",
            "override_reason",
        } <= stop_time_columns

        preview_columns = {
            row[1]
            for row in con.execute(
                "PRAGMA table_info(schedule_generation_previews)"
            )
        }
        assert {"token", "route_id", "day_type", "username", "payload_json",
                "expires_at", "applied_at"} <= preview_columns

        trip_columns = {
            row[1] for row in con.execute("PRAGMA table_info(route_trips)")
        }
        assert {"period_id", "source", "generation_key"} <= trip_columns
    finally:
        con.close()
