# -*- coding: utf-8 -*-


def test_stage_four_shift_schema_and_defaults_are_repeat_safe(tmp_path):
    from app import db

    db.DB_PATH = str(tmp_path / "stage-four.db")
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
            "shift_types",
            "route_shift_settings",
            "output_shifts",
            "shift_generation_previews",
        } <= tables

        shift_types = {
            row[0]: tuple(row[1:])
            for row in con.execute(
                """
                SELECT code, planned_duration_min, max_duration_min,
                       driver_slots
                FROM shift_types
                ORDER BY code
                """
            )
        }
        assert shift_types == {
            "single_8h": (480, 600, 1),
            "single_12h": (720, 780, 1),
            "split": (480, 600, 1),
            "two_driver_long": (900, 1080, 2),
        }

        trip_columns = {
            row[1] for row in con.execute("PRAGMA table_info(route_trips)")
        }
        roster_columns = {
            row[1]
            for row in con.execute("PRAGMA table_info(roster_assignments)")
        }
        assert "output_shift_id" in trip_columns
        assert "output_shift_id" in roster_columns
        roster_indexes = {
            row[1]
            for row in con.execute("PRAGMA index_list(roster_assignments)")
        }
        assert "idx_roster_assignments_output_shift_date" in roster_indexes
    finally:
        con.close()
