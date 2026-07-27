# -*- coding: utf-8 -*-
import sqlite3

import pytest


def _open_route_db(tmp_path):
    from app import db

    db.DB_PATH = str(tmp_path / "route-document-schema.db")
    db.init_db()
    return db, db.connect()


def _insert_route_and_stops(con):
    route_id = con.execute(
        "INSERT INTO routes(number, name) VALUES(?, ?)",
        ("T1", "Тестовый маршрут"),
    ).lastrowid
    first_stop_id = con.execute(
        "INSERT INTO stops(name) VALUES(?)",
        ("Первая остановка",),
    ).lastrowid
    second_stop_id = con.execute(
        "INSERT INTO stops(name) VALUES(?)",
        ("Вторая остановка",),
    ).lastrowid
    return route_id, first_stop_id, second_stop_id


def test_route_stop_day_and_night_runtimes_are_backfilled_repeat_safely(tmp_path):
    db, con = _open_route_db(tmp_path)
    try:
        route_id, stop_id, _ = _insert_route_and_stops(con)
        route_stop_id = con.execute(
            """
            INSERT INTO route_stops(
              route_id, direction, stop_id, sequence, run_time_sec
            ) VALUES(?,?,?,?,?)
            """,
            (route_id, "forward", stop_id, 1, 125),
        ).lastrowid

        db.migrate_route_network(con)
        db.migrate_route_network(con)

        columns = {
            row["name"]: row
            for row in con.execute("PRAGMA table_info(route_stops)")
        }
        assert columns["run_time_day_sec"]["notnull"] == 1
        assert columns["run_time_day_sec"]["dflt_value"] == "0"
        assert columns["run_time_night_sec"]["notnull"] == 1
        assert columns["run_time_night_sec"]["dflt_value"] == "0"

        runtime = con.execute(
            """
            SELECT run_time_day_sec, run_time_night_sec
            FROM route_stops
            WHERE id=?
            """,
            (route_stop_id,),
        ).fetchone()
        assert tuple(runtime) == (125, 125)
    finally:
        con.close()


def test_route_depot_stops_has_required_columns_unique_key_and_stop_index(tmp_path):
    db, con = _open_route_db(tmp_path)
    try:
        db.migrate_route_network(con)
        db.migrate_route_network(con)

        columns = {
            row["name"]: row
            for row in con.execute("PRAGMA table_info(route_depot_stops)")
        }
        assert {
            "id",
            "route_id",
            "direction",
            "stop_id",
            "sequence",
            "distance_from_prev_km",
            "run_time_day_sec",
            "run_time_night_sec",
            "source",
            "source_detail",
            "created_at",
            "updated_at",
        } == set(columns)
        assert columns["distance_from_prev_km"]["notnull"] == 1
        assert columns["distance_from_prev_km"]["dflt_value"] == "0"
        assert columns["run_time_day_sec"]["notnull"] == 1
        assert columns["run_time_day_sec"]["dflt_value"] == "0"
        assert columns["run_time_night_sec"]["notnull"] == 1
        assert columns["run_time_night_sec"]["dflt_value"] == "0"
        assert columns["source"]["notnull"] == 1
        assert columns["source"]["dflt_value"] == "'manual'"

        indexes = {
            row["name"]: row
            for row in con.execute("PRAGMA index_list(route_depot_stops)")
        }
        unique_indexes = [
            name for name, row in indexes.items() if row["unique"] == 1
        ]
        assert any(
            [
                row["name"]
                for row in con.execute(f"PRAGMA index_info({name})")
            ]
            == ["route_id", "direction", "sequence"]
            for name in unique_indexes
        )
        assert "idx_route_depot_stops_stop" in indexes
        assert [
            row["name"]
            for row in con.execute(
                "PRAGMA index_info(idx_route_depot_stops_stop)"
            )
        ] == ["stop_id"]
    finally:
        con.close()


def test_route_depot_stops_enforces_direction_uniqueness_and_nonnegative_values(
    tmp_path,
):
    _, con = _open_route_db(tmp_path)
    try:
        route_id, first_stop_id, second_stop_id = _insert_route_and_stops(con)
        con.execute(
            """
            INSERT INTO route_depot_stops(
              route_id, direction, stop_id, sequence,
              distance_from_prev_km, run_time_day_sec, run_time_night_sec
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (route_id, "depot_out", first_stop_id, 1, 1.5, 120, 140),
        )

        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                """
                INSERT INTO route_depot_stops(
                  route_id, direction, stop_id, sequence
                ) VALUES(?,?,?,?)
                """,
                (route_id, "depot_out", second_stop_id, 1),
            )
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                """
                INSERT INTO route_depot_stops(
                  route_id, direction, stop_id, sequence
                ) VALUES(?,?,?,?)
                """,
                (route_id, "forward", second_stop_id, 2),
            )
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                """
                INSERT INTO route_depot_stops(
                  route_id, direction, stop_id, sequence,
                  distance_from_prev_km
                ) VALUES(?,?,?,?,?)
                """,
                (route_id, "depot_in", second_stop_id, 1, -0.1),
            )
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                """
                INSERT INTO route_depot_stops(
                  route_id, direction, stop_id, sequence,
                  run_time_day_sec, run_time_night_sec
                ) VALUES(?,?,?,?,?,?)
                """,
                (route_id, "depot_in", second_stop_id, 1, -1, -1),
            )
    finally:
        con.close()
