# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "generation-preview.db")
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
def configured_route(client):
    import app.db as db

    con = db.connect()
    try:
        route_id = con.execute(
            "INSERT INTO routes(number,name,trip_time_min,trip_time_back_min,outputs_count) "
            "VALUES(?,?,?,?,?)",
            ("G1", "Generation route", 10, 10, 3),
        ).lastrowid
        stop_ids = []
        for code, name in (("A", "Terminal A"), ("B", "Terminal B")):
            stop_ids.append(
                con.execute(
                    "INSERT INTO stops(external_code,name,active) VALUES(?,?,1)",
                    (code, name),
                ).lastrowid
            )
        for direction, ordered in (
            ("forward", stop_ids), ("backward", list(reversed(stop_ids)))
        ):
            for sequence, stop_id in enumerate(ordered, start=1):
                con.execute(
                    "INSERT INTO route_stops(route_id,direction,sequence,stop_id,"
                    "run_time_sec,dwell_time_sec,is_timing_point) VALUES(?,?,?,?,?,?,1)",
                    (route_id, direction, sequence, stop_id,
                     0 if sequence == 1 else 600, 0),
                )
        con.execute(
            "INSERT INTO day_periods(route_id,day_type,name,start_min,end_min,"
            "interval_min,travel_time_factor,transition_mode,transition_window_min,"
            "color,priority,active,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (route_id, "будни", "Morning", 360, 400, 10, 1.0, "abrupt", 0,
             "#3b82f6", 0, 1, "2026-07-16", "2026-07-16"),
        )
        con.execute(
            "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,"
            "trip_number,direction,dep_time,arr_time,distance_km) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (route_id, "будни", 9, 1, 1, "прямое", "05:00", "05:10", 1.0),
        )
        con.commit()
        return route_id
    finally:
        con.close()


def test_generation_preview_has_returns_stop_times_and_no_writes(
    client, configured_route
):
    route_id = configured_route
    before = client.get(
        f"/api/trips?route_id={route_id}&day_type=будни"
    ).json()["items"]

    response = client.post(
        f"/api/routes/{route_id}/schedule-generation/preview",
        json={"day_type": "будни", "outputs": 3, "terminal_layover_min": 5},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["preview_token"]) == 32
    assert {trip["direction"] for trip in data["trips"]} == {
        "прямое", "обратное"
    }
    assert len(data["trips"]) == 8
    assert all(len(trip["stop_times"]) == 2 for trip in data["trips"])
    assert data["diff"] == {
        "old_trip_count": 1,
        "new_trip_count": 8,
        "old_stop_time_count": 0,
        "new_stop_time_count": 16,
    }
    assert client.get(
        f"/api/trips?route_id={route_id}&day_type=будни"
    ).json()["items"] == before


def test_preview_rejects_output_deficit_without_writes(client, configured_route):
    route_id = configured_route
    response = client.post(
        f"/api/routes/{route_id}/schedule-generation/preview",
        json={"day_type": "будни", "outputs": 1, "terminal_layover_min": 5},
    )
    assert response.status_code == 400
    assert "выход" in response.json()["detail"].lower()
    assert len(client.get(
        f"/api/trips?route_id={route_id}&day_type=будни"
    ).json()["items"]) == 1


def test_preview_requires_both_route_directions(client, configured_route):
    import app.db as db

    route_id = configured_route
    con = db.connect()
    try:
        con.execute(
            "DELETE FROM route_stops WHERE route_id=? AND direction='backward'",
            (route_id,),
        )
        con.commit()
    finally:
        con.close()

    response = client.post(
        f"/api/routes/{route_id}/schedule-generation/preview",
        json={"day_type": "будни", "outputs": 3},
    )
    assert response.status_code == 400
    assert "направлен" in response.json()["detail"].lower()
