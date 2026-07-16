# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "timetable-adjustments.db")
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
def scheduled_trip(client):
    import app.db as db

    con = db.connect()
    try:
        route_id = con.execute(
            "INSERT INTO routes(number,name,trip_time_min,trip_time_back_min) "
            "VALUES(?,?,?,?)",
            ("A1", "Adjustment route", 15, 15),
        ).lastrowid
        route_stop_ids = []
        for sequence in range(1, 5):
            stop_id = con.execute(
                "INSERT INTO stops(external_code,name,active) VALUES(?,?,1)",
                (f"A{sequence}", f"Stop {sequence}"),
            ).lastrowid
            route_stop_ids.append(
                con.execute(
                    "INSERT INTO route_stops(route_id,direction,sequence,stop_id,"
                    "run_time_sec,dwell_time_sec,is_timing_point) "
                    "VALUES(?,?,?,?,?,?,1)",
                    (route_id, "forward", sequence, stop_id,
                     0 if sequence == 1 else 300, 0),
                ).lastrowid
            )
        trip_id = con.execute(
            "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,"
            "trip_number,direction,dep_time,arr_time,distance_km) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (route_id, "будни", 1, 1, 1, "прямое", "06:00", "06:15", 4.0),
        ).lastrowid
        con.commit()
    finally:
        con.close()
    response = client.post(f"/api/trips/{trip_id}/stop-times/recalculate")
    assert response.status_code == 200, response.text
    return route_id, trip_id, route_stop_ids


def _times(client, route_id):
    response = client.get(f"/api/routes/{route_id}/stop-times?day_type=будни")
    assert response.status_code == 200, response.text
    return response.json()["trips"][0]["times"]


def test_shift_following_moves_selected_and_later_stops(client, scheduled_trip):
    route_id, trip_id, stops = scheduled_trip
    response = client.patch(
        f"/api/trips/{trip_id}/stop-times/{stops[1]}",
        json={
            "departure_time": "06:07",
            "strategy": "shift_following",
            "reason": "Задержка на участке",
        },
    )
    assert response.status_code == 200, response.text
    rows = _times(client, route_id)
    assert [row["departure_time"] for row in rows] == [
        "06:00", "06:07", "06:12", "06:17"
    ]
    assert rows[1]["is_manual_override"] is True
    assert rows[1]["override_strategy"] == "shift_following"

    import app.db as db
    con = db.connect()
    try:
        assert con.execute(
            "SELECT arr_time FROM route_trips WHERE id=?", (trip_id,)
        ).fetchone()[0] == "06:17"
    finally:
        con.close()


def test_redistribute_remaining_keeps_terminal_time(client, scheduled_trip):
    route_id, trip_id, stops = scheduled_trip
    response = client.patch(
        f"/api/trips/{trip_id}/stop-times/{stops[1]}",
        json={
            "departure_time": "06:07",
            "strategy": "redistribute_remaining",
            "reason": "Уточнение контрольной точки",
        },
    )
    assert response.status_code == 200, response.text
    assert [row["departure_time"] for row in _times(client, route_id)] == [
        "06:00", "06:07", "06:11", "06:15"
    ]


def test_invalid_selected_only_is_rejected_without_partial_write(client, scheduled_trip):
    route_id, trip_id, stops = scheduled_trip
    before = _times(client, route_id)
    response = client.patch(
        f"/api/trips/{trip_id}/stop-times/{stops[1]}",
        json={
            "departure_time": "23:59",
            "strategy": "selected_only",
            "reason": "Проверка валидации",
        },
    )
    assert response.status_code == 400
    assert _times(client, route_id) == before


def test_adjustment_requires_reason(client, scheduled_trip):
    _, trip_id, stops = scheduled_trip
    response = client.patch(
        f"/api/trips/{trip_id}/stop-times/{stops[1]}",
        json={"departure_time": "06:07", "strategy": "shift_following"},
    )
    assert response.status_code == 400


def test_reset_manual_adjustments_recalculates_original_times(client, scheduled_trip):
    route_id, trip_id, stops = scheduled_trip
    changed = client.patch(
        f"/api/trips/{trip_id}/stop-times/{stops[1]}",
        json={
            "departure_time": "06:07",
            "strategy": "shift_following",
            "reason": "Временная корректировка",
        },
    )
    assert changed.status_code == 200, changed.text

    response = client.post(
        f"/api/routes/{route_id}/stop-times/reset-manual",
        json={"day_type": "будни", "trip_id": trip_id},
    )
    assert response.status_code == 200, response.text
    assert response.json()["updated"] == 1
    rows = _times(client, route_id)
    assert [row["departure_time"] for row in rows] == [
        "06:00", "06:05", "06:10", "06:15"
    ]
    assert not any(row["is_manual_override"] for row in rows)
