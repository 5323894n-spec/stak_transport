# -*- coding: utf-8 -*-

import pytest
from fastapi.testclient import TestClient

from tests.test_route_shift_manual import DAY, _seed_output


@pytest.fixture
def client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "route-shift-output-list.db")
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


def _seed_assignments(route_id, first_shift_id, second_shift_id):
    import app.db as db

    con = db.connect()
    try:
        driver_ids = []
        for suffix in ("A", "B", "C"):
            driver_ids.append(con.execute(
                "INSERT INTO drivers(tab_number,fio,status) VALUES(?,?,'работает')",
                (f"LIST-{suffix}", f"List driver {suffix}"),
            ).lastrowid)
        for driver_id, date, shift_id, shift_number in (
            (driver_ids[0], "2026-07-06", first_shift_id, 1),
            (driver_ids[1], "2026-07-07", first_shift_id, 1),
            (driver_ids[2], "2026-07-08", second_shift_id, 2),
        ):
            con.execute(
                "INSERT INTO roster_assignments(driver_id,date,route_id,day_type,"
                "output_number,shift_number,output_shift_id) VALUES(?,?,?, ?,1,?,?)",
                (driver_id, date, route_id, DAY, shift_number, shift_id),
            )
        con.execute(
            "UPDATE output_shifts SET source='manual',is_manual_locked=1,"
            "manual_reason='boundary review' WHERE id=?",
            (first_shift_id,),
        )
        con.commit()
    finally:
        con.close()


def test_output_shift_list_returns_details_and_all_date_assignment_totals(client):
    route_id, trips, shifts, _, _ = _seed_output("LIST-1")
    _seed_assignments(route_id, shifts[0], shifts[1])

    response = client.get(
        f"/api/routes/{route_id}/output-shifts?day_type={DAY}"
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["route_id"] == route_id
    assert data["day_type"] == DAY
    assert data["assignment_count_scope"] == "all_dates"
    assert [item["id"] for item in data["items"]] == shifts
    first, second = data["items"]
    assert first["output_number"] == 1
    assert first["shift_number"] == 1
    assert first["trip_from_id"] == trips[0]
    assert first["trip_to_id"] == trips[0]
    assert first["trip_from_number"] == 1
    assert first["trip_to_number"] == 1
    assert first["start_sec"] == 21600
    assert first["end_sec"] == 25200
    assert first["duration_sec"] == 3600
    assert first["driver_slots"] == 1
    assert first["source"] == "manual"
    assert first["is_manual_locked"] is True
    assert first["manual_reason"] == "boundary review"
    assert first["shift_type_code"] == "single_8h"
    assert first["shift_type_name"]
    assert first["shift_type_color"] == "#2563eb"
    assert first["assignment_count"] == 2
    assert second["assignment_count"] == 1


def test_output_shift_list_requires_day_type(client):
    route_id, _, _, _, _ = _seed_output("LIST-2")

    response = client.get(f"/api/routes/{route_id}/output-shifts")

    assert response.status_code == 400
    assert "тип дня" in response.json()["detail"].lower()


def test_output_shift_list_requires_existing_route(client):
    response = client.get("/api/routes/999999/output-shifts?day_type=будни")

    assert response.status_code == 404
    assert "маршрут" in response.json()["detail"].lower()
