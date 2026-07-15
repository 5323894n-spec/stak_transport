# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "period-preview.db")
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


def create_route(client, number="P1", forward=40, backward=50):
    payload = {"number": number, "name": "Route " + number}
    if forward is not None:
        payload["trip_time_min"] = forward
    if backward is not None:
        payload["trip_time_back_min"] = backward
    response = client.post("/api/refs/routes", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def save_periods(client, route_id, items):
    response = client.put(
        f"/api/routes/{route_id}/periods/weekday", json={"items": items}
    )
    assert response.status_code == 200, response.text


def test_preview_calculates_departures_and_demand_without_writing_trips(client):
    route_id = create_route(client)
    save_periods(client, route_id, [
        {"name": "Peak", "start": "06:00", "end": "07:00", "interval_min": 10},
        {"name": "Day", "start": "07:00", "end": "08:00", "interval_min": 20},
    ])
    before = client.get(
        f"/api/trips?route_id={route_id}&day_type=weekday"
    ).json()["items"]

    response = client.post(
        f"/api/routes/{route_id}/periods/weekday/preview",
        json={"terminal_layover_min": 5},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["departures"][0] == {"minute": 360, "time": "06:00"}
    assert data["departures"][-1]["time"] == "07:40"
    assert data["max_buses_required"] == 10
    assert [row["buses_required"] for row in data["periods"]] == [10, 5]
    assert data["route"]["forward_min"] == 40
    assert data["route"]["backward_min"] == 50

    after = client.get(
        f"/api/trips?route_id={route_id}&day_type=weekday"
    ).json()["items"]
    assert after == before == []


def test_preview_requires_periods_and_route_travel_times(client):
    route_id = create_route(client, "P2")
    response = client.post(
        f"/api/routes/{route_id}/periods/weekday/preview", json={}
    )
    assert response.status_code == 400

    incomplete_id = create_route(client, "P3", None, None)
    save_periods(client, incomplete_id, [
        {"name": "Day", "start": "06:00", "end": "20:00", "interval_min": 15}
    ])
    response = client.post(
        f"/api/routes/{incomplete_id}/periods/weekday/preview", json={}
    )
    assert response.status_code == 400


def test_preview_returns_demand_jump_warning(client):
    route_id = create_route(client, "P4", 30, 30)
    save_periods(client, route_id, [
        {"name": "Quiet", "start": "06:00", "end": "07:00", "interval_min": 30},
        {"name": "Peak", "start": "07:00", "end": "08:00", "interval_min": 10},
    ])
    response = client.post(
        f"/api/routes/{route_id}/periods/weekday/preview",
        json={"terminal_layover_min": 0},
    )
    assert response.status_code == 200, response.text
    warnings = response.json()["warnings"]
    assert warnings == [{"code": "demand_jump", "from": "Quiet", "to": "Peak", "delta": 4}]
