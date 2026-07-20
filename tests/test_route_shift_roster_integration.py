# -*- coding: utf-8 -*-

import pytest
from fastapi.testclient import TestClient


DATE = "2026-07-06"
DAY_TYPE = "будни"


def make_client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "route-shift-roster.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
    finally:
        con.close()

    client = TestClient(app)
    token = client.post(
        "/api/login", json={"username": "admin", "password": "admin"}
    ).json()["token"]
    client.headers.update({"Authorization": "Bearer " + token})
    return client


def seed_structural_shift(*, driver_slots=1, output_number=1, shift_number=1):
    import app.db as db

    con = db.connect()
    try:
        route_id = con.execute(
            "INSERT INTO routes(number,name) VALUES(?,?)",
            ("RS-1", "Roster structural shift"),
        ).lastrowid
        first_trip_id = con.execute(
            """
            INSERT INTO route_trips(
              route_id,day_type,output_number,shift_number,trip_number,
              direction,dep_time,arr_time,distance_km
            ) VALUES(?,?,?,?,1,'прямое','06:00','09:00',10)
            """,
            (route_id, DAY_TYPE, output_number, shift_number),
        ).lastrowid
        last_trip_id = con.execute(
            """
            INSERT INTO route_trips(
              route_id,day_type,output_number,shift_number,trip_number,
              direction,dep_time,arr_time,distance_km
            ) VALUES(?,?,?,?,2,'обратное','09:15','13:30',12)
            """,
            (route_id, DAY_TYPE, output_number, shift_number),
        ).lastrowid
        shift_type = con.execute(
            "SELECT id,code,name,color FROM shift_types WHERE driver_slots=? ORDER BY id LIMIT 1",
            (driver_slots,),
        ).fetchone()
        output_shift_id = con.execute(
            """
            INSERT INTO output_shifts(
              route_id,day_type,output_number,shift_number,shift_type_id,
              trip_from_id,trip_to_id,start_sec,end_sec,driver_slots,
              source,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,'manual',datetime('now'),datetime('now'))
            """,
            (
                route_id, DAY_TYPE, output_number, shift_number, shift_type["id"],
                first_trip_id, last_trip_id, 21600, 48600, driver_slots,
            ),
        ).lastrowid
        con.execute(
            "UPDATE route_trips SET output_shift_id=? WHERE id IN (?,?)",
            (output_shift_id, first_trip_id, last_trip_id),
        )
        con.commit()
        return {
            "route_id": route_id,
            "output_shift_id": output_shift_id,
            "shift_type": dict(shift_type),
            "trip_from_id": first_trip_id,
            "trip_to_id": last_trip_id,
        }
    finally:
        con.close()


def create_driver(suffix):
    import app.db as db

    con = db.connect()
    try:
        driver_id = con.execute(
            "INSERT INTO drivers(tab_number,fio,status) VALUES(?,?,'работает')",
            (f"D-{suffix}", f"Driver {suffix}"),
        ).lastrowid
        con.commit()
        return driver_id
    finally:
        con.close()


def assignment_payload(driver_id, seeded, **overrides):
    payload = {
        "driver_id": driver_id, "date": DATE,
        "route_id": seeded["route_id"], "day_type": DAY_TYPE,
        "output_number": 1, "shift_number": 1,
        "trip_from": 1, "trip_to": 2,
        "output_shift_id": seeded["output_shift_id"],
    }
    payload.update(overrides)
    return payload


def test_schedule_options_exposes_structural_shift_fields(tmp_path):
    client = make_client(tmp_path)
    seeded = seed_structural_shift(driver_slots=2)

    response = client.get(
        f"/api/roster/schedule-options?route_id={seeded['route_id']}&date={DATE}"
    )

    assert response.status_code == 200, response.text
    item = response.json()["outputs"][0]
    assert item["output_shift_id"] == seeded["output_shift_id"]
    assert item["shift_type_id"] == seeded["shift_type"]["id"]
    assert item["shift_type_code"] == seeded["shift_type"]["code"]
    assert item["shift_type_name"] == seeded["shift_type"]["name"]
    assert item["shift_type_color"] == seeded["shift_type"]["color"]
    assert item["start_sec"] == 21600
    assert item["end_sec"] == 48600
    assert item["trip_from_id"] == seeded["trip_from_id"]
    assert item["trip_to_id"] == seeded["trip_to_id"]
    assert item["driver_slots"] == 2
    assert item["assignment_count"] == 0
    assert item["available_driver_slots"] == 2


def test_schedule_options_uses_structural_trip_range_as_default(tmp_path):
    import app.db as db

    client = make_client(tmp_path)
    seeded = seed_structural_shift()
    con = db.connect()
    try:
        con.execute(
            """
            INSERT INTO route_trips(
              route_id,day_type,output_number,shift_number,trip_number,
              direction,dep_time,arr_time,distance_km,output_shift_id
            ) VALUES(?,?,?,?,3,'прямое','14:00','15:00',8,NULL)
            """,
            (seeded["route_id"], DAY_TYPE, 1, 1),
        )
        con.commit()
    finally:
        con.close()

    response = client.get(
        f"/api/roster/schedule-options?route_id={seeded['route_id']}&date={DATE}"
        "&output_number=1&shift_number=1"
    )

    assert response.status_code == 200, response.text
    assert [trip["trip_number"] for trip in response.json()["trips"]] == [1, 2]
    assert response.json()["suggestion"]["trip_from"] == 1
    assert response.json()["suggestion"]["trip_to"] == 2


def test_valid_output_shift_link_is_persisted_and_read_back(tmp_path):
    client = make_client(tmp_path)
    seeded = seed_structural_shift()
    driver_id = create_driver("valid")

    saved = client.post(
        "/api/roster/assignment",
        json=assignment_payload(driver_id, seeded),
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["assignment"]["output_shift_id"] == seeded["output_shift_id"]
    response = client.get(
        f"/api/roster/assignments?driver_id={driver_id}&date={DATE}"
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["output_shift_id"] == seeded["output_shift_id"]

    options = client.get(
        f"/api/roster/schedule-options?route_id={seeded['route_id']}&date={DATE}"
    ).json()["outputs"][0]
    assert (options["assignment_count"], options["available_driver_slots"]) == (1, 0)


def test_nonexistent_output_shift_link_returns_404_without_write(tmp_path):
    import app.db as db

    client = make_client(tmp_path)
    seeded = seed_structural_shift()
    driver_id = create_driver("missing")
    payload = assignment_payload(
        driver_id, seeded, output_shift_id=seeded["output_shift_id"] + 9999
    )

    response = client.post("/api/roster/assignment", json=payload)

    assert response.status_code == 404, response.text
    assert "смен" in response.json()["detail"].lower()
    con = db.connect()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM roster_assignments WHERE driver_id=?",
            (driver_id,),
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM roster WHERE driver_id=?", (driver_id,)
        ).fetchone()[0] == 0
    finally:
        con.close()


@pytest.mark.parametrize(
    "mismatch",
    ["route_id", "day_type", "output_number", "shift_number"],
)
def test_output_shift_scope_mismatch_returns_400_without_write(tmp_path, mismatch):
    import app.db as db

    client = make_client(tmp_path)
    seeded = seed_structural_shift()
    driver_id = create_driver(mismatch)
    overrides = {
        "route_id": None,
        "day_type": "выходные",
        "output_number": 2,
        "shift_number": 2,
    }
    if mismatch == "route_id":
        overrides["route_id"] = seed_structural_shift()["route_id"]
    payload = assignment_payload(
        driver_id, seeded, **{mismatch: overrides[mismatch]}
    )

    response = client.post("/api/roster/assignment", json=payload)

    assert response.status_code == 400, response.text
    assert "не соответствует" in response.json()["detail"].lower()
    con = db.connect()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM roster_assignments WHERE driver_id=?",
            (driver_id,),
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_linked_assignment_rejects_time_outside_structural_range(tmp_path):
    import app.db as db

    client = make_client(tmp_path)
    seeded = seed_structural_shift()
    driver_id = create_driver("outside-range")

    response = client.post(
        "/api/roster/assignment",
        json=assignment_payload(
            driver_id, seeded, start_time="05:59", end_time="13:30"
        ),
    )

    assert response.status_code == 400, response.text
    assert "границ" in response.json()["detail"].lower()
    con = db.connect()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM roster_assignments WHERE driver_id=?", (driver_id,)
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_two_driver_shift_allows_two_assignments_but_rejects_third(tmp_path):
    import app.db as db

    client = make_client(tmp_path)
    seeded = seed_structural_shift(driver_slots=2)
    drivers = [create_driver(f"slot-{number}") for number in range(1, 4)]

    first = client.post(
        "/api/roster/assignment", json=assignment_payload(drivers[0], seeded)
    )
    second = client.post(
        "/api/roster/assignment", json=assignment_payload(drivers[1], seeded)
    )
    third = client.post(
        "/api/roster/assignment", json=assignment_payload(drivers[2], seeded)
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert third.status_code == 409, third.text
    assert "свобод" in third.json()["detail"].lower()

    updated = client.post(
        "/api/roster/assignment",
        json=assignment_payload(
            drivers[0], seeded, id=first.json()["assignment"]["id"], comment="updated"
        ),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["assignment"]["comment"] == "updated"

    options = client.get(
        f"/api/roster/schedule-options?route_id={seeded['route_id']}&date={DATE}"
    ).json()["outputs"][0]
    assert (options["assignment_count"], options["available_driver_slots"]) == (2, 0)
    con = db.connect()
    try:
        linked = con.execute(
            "SELECT driver_id FROM roster_assignments WHERE output_shift_id=? ORDER BY driver_id",
            (seeded["output_shift_id"],),
        ).fetchall()
        assert [row[0] for row in linked] == drivers[:2]
    finally:
        con.close()


def test_single_driver_shift_rejects_second_assignment(tmp_path):
    client = make_client(tmp_path)
    seeded = seed_structural_shift(driver_slots=1)
    first_driver = create_driver("single-1")
    second_driver = create_driver("single-2")

    first = client.post(
        "/api/roster/assignment", json=assignment_payload(first_driver, seeded)
    )
    second = client.post(
        "/api/roster/assignment", json=assignment_payload(second_driver, seeded)
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert "свобод" in second.json()["detail"].lower()


def test_legacy_assignment_without_output_shift_link_still_saves(tmp_path):
    client = make_client(tmp_path)
    seeded = seed_structural_shift(driver_slots=1)
    first_driver = create_driver("legacy-1")
    second_driver = create_driver("legacy-2")
    first_payload = assignment_payload(first_driver, seeded)
    second_payload = assignment_payload(second_driver, seeded)
    first_payload.pop("output_shift_id")
    second_payload.pop("output_shift_id")

    first = client.post("/api/roster/assignment", json=first_payload)
    second = client.post("/api/roster/assignment", json=second_payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["assignment"]["output_shift_id"] is None
    assert second.json()["assignment"]["output_shift_id"] is None


def test_scope_mismatch_update_rolls_back_existing_assignment(tmp_path):
    import app.db as db

    client = make_client(tmp_path)
    seeded = seed_structural_shift()
    driver_id = create_driver("update-rollback")
    created = client.post(
        "/api/roster/assignment", json=assignment_payload(driver_id, seeded)
    )
    assert created.status_code == 200, created.text
    assignment_id = created.json()["assignment"]["id"]

    rejected = client.post(
        "/api/roster/assignment",
        json=assignment_payload(
            driver_id,
            seeded,
            id=assignment_id,
            output_number=2,
            start_time="06:00",
            end_time="13:30",
            comment="must not persist",
        ),
    )

    assert rejected.status_code == 400, rejected.text
    con = db.connect()
    try:
        row = con.execute(
            "SELECT output_number,comment,output_shift_id FROM roster_assignments WHERE id=?",
            (assignment_id,),
        ).fetchone()
        assert tuple(row) == (1, "", seeded["output_shift_id"])
    finally:
        con.close()
