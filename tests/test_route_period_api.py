# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "period-api.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
    finally:
        con.close()
    test_client = TestClient(app)
    token = test_client.post(
        "/api/login", json={"username": "admin", "password": "admin"}
    ).json()["token"]
    test_client.headers.update({"Authorization": "Bearer " + token})
    return test_client


@pytest.fixture
def route_id(client):
    response = client.post(
        "/api/refs/routes",
        json={
            "number": "P2",
            "name": "Периодный маршрут",
            "trip_time_min": 40,
            "trip_time_back_min": 45,
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


def test_replace_and_read_complete_period_set(client, route_id):
    response = client.put(
        f"/api/routes/{route_id}/periods/будни",
        json={
            "require_continuous": True,
            "service_start": "06:00",
            "service_end": "22:00",
            "items": [
                {
                    "name": "Утро",
                    "start": "06:00",
                    "end": "10:00",
                    "interval_min": 10,
                    "travel_time_factor": 1.05,
                    "transition_mode": "abrupt",
                    "color": "#ef4444",
                },
                {
                    "name": "День",
                    "start": "10:00",
                    "end": "22:00",
                    "interval_min": 20,
                    "travel_time_factor": 1,
                    "transition_mode": "smooth",
                    "transition_window_min": 30,
                    "color": "#3b82f6",
                },
            ],
        },
    )
    assert response.status_code == 200, response.text
    saved = client.get(f"/api/routes/{route_id}/periods/будни").json()
    assert [item["name"] for item in saved["items"]] == ["Утро", "День"]
    assert saved["items"][1]["start_min"] == 600


def test_rejected_period_set_does_not_replace_existing_rows(client, route_id):
    valid = {
        "items": [
            {
                "name": "Рабочий день",
                "start": "06:00",
                "end": "22:00",
                "interval_min": 15,
            }
        ]
    }
    assert client.put(
        f"/api/routes/{route_id}/periods/будни", json=valid
    ).status_code == 200
    before = client.get(f"/api/routes/{route_id}/periods/будни").json()["items"]
    rejected = client.put(
        f"/api/routes/{route_id}/periods/будни",
        json={
            "items": [
                {"name": "A", "start": "06:00", "end": "12:00", "interval_min": 10},
                {"name": "B", "start": "11:00", "end": "22:00", "interval_min": 20},
            ]
        },
    )
    assert rejected.status_code == 400
    after = client.get(f"/api/routes/{route_id}/periods/будни").json()["items"]
    assert after == before


def test_period_api_rejects_unknown_route(client):
    assert client.get("/api/routes/99999/periods/будни").status_code == 404
