# -*- coding: utf-8 -*-


def test_route_schema_is_idempotent_and_has_required_tables(tmp_path):
    import app.db as db

    db.DB_PATH = str(tmp_path / "route-schema.db")
    db.init_db()
    db.init_db()
    con = db.connect()
    try:
        tables = {
            row["name"]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"stops", "route_stops", "route_migration_log"} <= tables

        indexes = {
            row["name"]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert "uq_route_stops_direction_sequence" in indexes
        assert "uq_stops_external_code" in indexes
    finally:
        con.close()
