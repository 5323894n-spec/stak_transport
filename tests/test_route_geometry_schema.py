# -*- coding: utf-8 -*-
import sqlite3

import pytest


def _open_route_db(tmp_path):
    from app import db

    db.DB_PATH = str(tmp_path / "route-geometry-schema.db")
    db.init_db()
    return db, db.connect()


def test_route_geometries_schema_is_idempotent_and_has_required_columns(tmp_path):
    db, con = _open_route_db(tmp_path)
    try:
        db.migrate_route_network(con)
        db.migrate_route_network(con)

        columns = {
            row["name"] for row in con.execute("PRAGMA table_info(route_geometries)")
        }
        assert columns == {
            "id",
            "route_id",
            "direction",
            "geometry_json",
            "source",
            "version",
            "updated_by",
            "created_at",
            "updated_at",
        }
    finally:
        con.close()


def test_route_geometries_enforces_direction_uniqueness_and_route_cascade(tmp_path):
    db, con = _open_route_db(tmp_path)
    try:
        db.migrate_route_network(con)
        route_id = con.execute(
            "INSERT INTO routes(number, name) VALUES(?, ?)",
            ("T1", "Тестовый маршрут"),
        ).lastrowid
        geometry = (route_id, "forward", "[]", "manual", "tester")
        con.execute(
            """
            INSERT INTO route_geometries(
              route_id, direction, geometry_json, source, updated_by
            ) VALUES(?,?,?,?,?)
            """,
            geometry,
        )

        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                """
                INSERT INTO route_geometries(
                  route_id, direction, geometry_json, source, updated_by
                ) VALUES(?,?,?,?,?)
                """,
                geometry,
            )

        con.execute("DELETE FROM routes WHERE id=?", (route_id,))
        assert con.execute(
            "SELECT COUNT(*) FROM route_geometries WHERE route_id=?", (route_id,)
        ).fetchone()[0] == 0
    finally:
        con.close()
