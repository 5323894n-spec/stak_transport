# -*- coding: utf-8 -*-


def test_period_schema_is_repeat_safe(tmp_path):
    from app import db

    db.DB_PATH = str(tmp_path / "periods.db")
    db.init_db()
    db.init_db()
    con = db.connect()
    try:
        tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "day_periods",
            "period_templates",
            "period_template_items",
            "period_previews",
        } <= tables
        period_columns = {
            row[1] for row in con.execute("PRAGMA table_info(day_periods)")
        }
        assert {
            "route_id",
            "day_type",
            "start_min",
            "end_min",
            "interval_min",
            "travel_time_factor",
            "transition_mode",
            "transition_window_min",
            "color",
            "priority",
            "active",
        } <= period_columns
        preview_columns = {
            row[1] for row in con.execute("PRAGMA table_info(period_previews)")
        }
        assert "applied_at" in preview_columns
    finally:
        con.close()
