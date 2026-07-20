# -*- coding: utf-8 -*-
import datetime
import json

import pytest
from fastapi.testclient import TestClient


DAY = "будни"


@pytest.fixture
def client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "route-shift-generation.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
    finally:
        con.close()
    result = TestClient(app)
    token = result.post(
        "/api/login", json={"username": "admin", "password": "admin"}
    ).json()["token"]
    result.headers.update({"Authorization": "Bearer " + token})
    return result


@pytest.fixture
def route_id(client):
    import app.db as db

    con = db.connect()
    try:
        value = con.execute(
            "INSERT INTO routes(number,name) VALUES(?,?)",
            ("SG4", "Shift generation route"),
        ).lastrowid
        con.commit()
        return value
    finally:
        con.close()


def add_trip(con, route_id, output, number, dep, arr, shift_number=9):
    return con.execute(
        """
        INSERT INTO route_trips(
          route_id,day_type,output_number,shift_number,trip_number,
          direction,dep_time,arr_time,distance_km
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (route_id, DAY, output, shift_number, number, "прямое", dep, arr, 1.0),
    ).lastrowid


def preview(client, route_id, **payload):
    return client.post(
        f"/api/routes/{route_id}/shift-generation/preview",
        json={"day_type": DAY, **payload},
    )


def apply(client, route_id, token, day_type=DAY):
    return client.post(
        f"/api/routes/{route_id}/shift-generation/apply",
        json={"day_type": day_type, "preview_token": token},
    )


def test_preview_groups_all_outputs_without_mutating_schedule(client, route_id):
    import app.db as db

    con = db.connect()
    try:
        add_trip(con, route_id, 2, 2, "25:10", "25:40")
        add_trip(con, route_id, 1, 2, "24:15", "24:45")
        add_trip(con, route_id, 1, 1, "23:30", "00:00")
        add_trip(con, route_id, 2, 1, "23:50", "24:20")
        con.commit()
    finally:
        con.close()

    response = preview(client, route_id)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["route_id"] == route_id
    assert data["day_type"] == DAY
    assert [item["output_number"] for item in data["outputs"]] == [1, 2]
    assert data["conflicts"] == []
    assert data["diff"] == {
        "old_shift_count": 0,
        "new_shift_count": 2,
        "old_driver_slots": 0,
        "new_driver_slots": 2,
    }
    assert len(data["preview_token"]) == 32

    con = db.connect()
    try:
        assert con.execute("SELECT COUNT(*) FROM output_shifts").fetchone()[0] == 0
        rows = con.execute(
            "SELECT shift_number,output_shift_id FROM route_trips ORDER BY id"
        ).fetchall()
        assert {(row[0], row[1]) for row in rows} == {(9, None)}
        assert con.execute("SELECT COUNT(*) FROM roster_assignments").fetchone()[0] == 0
        saved = con.execute(
            "SELECT expires_at,payload_json FROM shift_generation_previews "
            "WHERE token=?",
            (data["preview_token"],),
        ).fetchone()
        assert saved is not None
        expires = datetime.datetime.fromisoformat(saved["expires_at"])
        assert datetime.timedelta(minutes=29) < expires - datetime.datetime.now() <= datetime.timedelta(minutes=30)
        assert json.loads(saved["payload_json"])["outputs"] == data["outputs"]
    finally:
        con.close()


def test_preview_reports_output_conflict_when_no_handover_exists(client, route_id):
    import app.db as db

    con = db.connect()
    try:
        add_trip(con, route_id, 1, 1, "06:00", "11:00")
        add_trip(con, route_id, 1, 2, "11:05", "16:00")
        con.execute(
            "UPDATE shift_types SET planned_duration_min=300,max_duration_min=300 "
            "WHERE code='single_8h'"
        )
        con.commit()
    finally:
        con.close()

    response = preview(client, route_id)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["outputs"][0]["shifts"] == []
    assert data["conflicts"][0]["output_number"] == 1
    assert "пересмен" in data["conflicts"][0]["message"].lower()
    assert apply(client, route_id, data["preview_token"]).status_code == 400


def test_long_output_uses_two_driver_type_and_counts_slots(client, route_id):
    import app.db as db

    con = db.connect()
    try:
        add_trip(con, route_id, 1, 1, "06:00", "14:00")
        add_trip(con, route_id, 1, 2, "14:10", "21:00")
        con.commit()
    finally:
        con.close()

    response = preview(client, route_id)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["outputs"][0]["shifts"][0]["driver_slots"] == 2
    assert data["diff"]["new_shift_count"] == 1
    assert data["diff"]["new_driver_slots"] == 2


def test_apply_is_one_time_and_scope_bound(client, route_id):
    import app.db as db

    con = db.connect()
    try:
        add_trip(con, route_id, 1, 1, "06:00", "07:00")
        con.commit()
    finally:
        con.close()

    token = preview(client, route_id).json()["preview_token"]
    assert apply(client, route_id, token, "выходные").status_code == 400
    assert apply(client, route_id + 1, token).status_code == 404
    first = apply(client, route_id, token)
    assert first.status_code == 200, first.text
    assert apply(client, route_id, token).status_code == 409


def test_apply_inserts_shifts_and_links_every_trip(client, route_id):
    import app.db as db

    con = db.connect()
    try:
        for output in (1, 2):
            add_trip(con, route_id, output, 1, "06:00", "07:00")
            add_trip(con, route_id, output, 2, "07:10", "08:00")
        con.commit()
    finally:
        con.close()

    token = preview(client, route_id).json()["preview_token"]
    response = apply(client, route_id, token)
    assert response.status_code == 200, response.text
    assert response.json()["shift_count"] == 2

    con = db.connect()
    try:
        shifts = con.execute(
            "SELECT * FROM output_shifts WHERE route_id=? AND day_type=?",
            (route_id, DAY),
        ).fetchall()
        assert len(shifts) == 2
        trips = con.execute(
            """
            SELECT rt.shift_number,rt.output_shift_id,os.route_id,os.day_type,
                   os.output_number
            FROM route_trips rt
            LEFT JOIN output_shifts os ON os.id=rt.output_shift_id
            WHERE rt.route_id=? AND rt.day_type=?
            """,
            (route_id, DAY),
        ).fetchall()
        assert len(trips) == 4
        assert all(row["output_shift_id"] is not None for row in trips)
        assert all(row["route_id"] == route_id and row["day_type"] == DAY for row in trips)
        assert all(row["shift_number"] == 1 for row in trips)
        assert con.execute(
            "SELECT COUNT(*) FROM audit_log WHERE object_type='output_shifts'"
        ).fetchone()[0] == 1
    finally:
        con.close()


def test_corrupt_preview_rolls_back_existing_shifts_and_links(client, route_id):
    import app.db as db

    con = db.connect()
    try:
        first_trip = add_trip(con, route_id, 1, 1, "06:00", "07:00", 7)
        second_trip = add_trip(con, route_id, 1, 2, "07:10", "08:00", 7)
        shift_type_id = con.execute(
            "SELECT id FROM shift_types WHERE code='single_8h'"
        ).fetchone()[0]
        old_shift = con.execute(
            """
            INSERT INTO output_shifts(
              route_id,day_type,output_number,shift_number,shift_type_id,
              trip_from_id,trip_to_id,start_sec,end_sec,driver_slots,
              created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
            """,
            (route_id, DAY, 1, 7, shift_type_id, first_trip, second_trip,
             21600, 28800, 1),
        ).lastrowid
        con.execute(
            "UPDATE route_trips SET output_shift_id=? WHERE id IN (?,?)",
            (old_shift, first_trip, second_trip),
        )
        con.commit()
    finally:
        con.close()

    token = preview(client, route_id).json()["preview_token"]
    con = db.connect()
    try:
        plan = json.loads(con.execute(
            "SELECT payload_json FROM shift_generation_previews WHERE token=?",
            (token,),
        ).fetchone()[0])
        plan["outputs"][0]["shifts"][0]["trip_to_id"] = 999999
        con.execute(
            "UPDATE shift_generation_previews SET payload_json=? WHERE token=?",
            (json.dumps(plan), token),
        )
        con.commit()
    finally:
        con.close()

    response = apply(client, route_id, token)
    assert response.status_code == 400, response.text
    con = db.connect()
    try:
        assert con.execute("SELECT COUNT(*) FROM output_shifts").fetchone()[0] == 1
        assert con.execute("SELECT id FROM output_shifts").fetchone()[0] == old_shift
        rows = con.execute(
            "SELECT shift_number,output_shift_id FROM route_trips ORDER BY id"
        ).fetchall()
        assert [(row[0], row[1]) for row in rows] == [(7, old_shift), (7, old_shift)]
        assert con.execute(
            "SELECT applied_at FROM shift_generation_previews WHERE token=?",
            (token,),
        ).fetchone()[0] is None
    finally:
        con.close()
