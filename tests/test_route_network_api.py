# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "route-network-api.db")
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
    response = client.post("/api/refs/routes", json={
        "number": "12",
        "name": "Вокзал — Автовокзал",
        "stops": "Старое значение",
        "length_km": 99,
    })
    assert response.status_code == 200
    return response.json()["id"]


def create_stop(client, name, code):
    response = client.post("/api/stops", json={
        "name": name,
        "external_code": code,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_create_and_list_stops(client):
    stop_id = create_stop(client, "Вокзал", "100")

    response = client.get("/api/stops?q=вок")

    assert response.status_code == 200
    assert response.json()["items"] == [{
        "id": stop_id,
        "external_code": "100",
        "name": "Вокзал",
        "latitude": None,
        "longitude": None,
        "address": None,
        "stop_kind": "обычная",
        "is_terminal": 0,
        "has_dispatcher": 0,
        "municipality": None,
        "registry_flags": "{}",
        "source": "manual",
        "active": 1,
        "notes": None,
        "created_at": response.json()["items"][0]["created_at"],
        "updated_at": response.json()["items"][0]["updated_at"],
    }]


def test_replace_trace_syncs_legacy_route_fields(client, route_id):
    first = create_stop(client, "Вокзал", "100")
    second = create_stop(client, "Автовокзал", "101")

    response = client.put(f"/api/routes/{route_id}/stops/forward", json={"items": [
        {"stop_id": first, "sequence": 1, "distance_from_prev_km": 0},
        {"stop_id": second, "sequence": 2, "distance_from_prev_km": 1.5,
         "run_time_sec": 240, "dwell_time_sec": 30},
    ]})

    assert response.status_code == 200, response.text
    assert response.json()["total_km"] == 1.5
    network = client.get(f"/api/routes/{route_id}/network").json()
    assert [row["stop"]["name"] for row in network["forward"]] == [
        "Вокзал", "Автовокзал"
    ]
    assert network["forward"][1]["cumulative_km"] == 1.5
    route = next(
        row for row in client.get("/api/refs/routes").json()["items"]
        if row["id"] == route_id
    )
    assert route["stops"] == "Вокзал, Автовокзал"
    assert route["length_km"] == 1.5


def test_replace_trace_saves_day_and_night_runtime_variants(client, route_id):
    first = create_stop(client, "Вокзал", "100")
    second = create_stop(client, "Автовокзал", "101")

    response = client.put(f"/api/routes/{route_id}/stops/forward", json={"items": [
        {"stop_id": first, "sequence": 1, "distance_from_prev_km": 0},
        {"stop_id": second, "sequence": 2, "distance_from_prev_km": 1.5,
         "run_time_sec": 80, "run_time_day_sec": 70,
         "run_time_night_sec": 55, "dwell_time_sec": 30},
    ]})

    assert response.status_code == 200, response.text
    saved = response.json()["items"][1]
    assert saved["run_time_sec"] == 80
    assert saved["run_time_day_sec"] == 70
    assert saved["run_time_night_sec"] == 55
    loaded = client.get(f"/api/routes/{route_id}/network").json()["forward"][1]
    assert loaded["run_time_sec"] == 80
    assert loaded["run_time_day_sec"] == 70
    assert loaded["run_time_night_sec"] == 55


def test_replace_trace_defaults_missing_runtime_variants_to_main_runtime(
    client, route_id
):
    stop_id = create_stop(client, "Вокзал", "100")

    response = client.put(f"/api/routes/{route_id}/stops/backward", json={"items": [
        {"stop_id": stop_id, "sequence": 1, "distance_from_prev_km": 0,
         "run_time_sec": 125},
    ]})

    assert response.status_code == 200, response.text
    row = response.json()["items"][0]
    assert row["run_time_sec"] == 125
    assert row["run_time_day_sec"] == 125
    assert row["run_time_night_sec"] == 125


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_time_day_sec", -1),
        ("run_time_night_sec", True),
        ("run_time_day_sec", 1.5),
        ("run_time_night_sec", "1e999"),
        ("run_time_sec", 2**63),
    ],
)
def test_replace_trace_rejects_invalid_runtime_values(
    client, route_id, field, value
):
    stop_id = create_stop(client, "Вокзал", "100")
    item = {
        "stop_id": stop_id,
        "sequence": 1,
        "distance_from_prev_km": 0,
        "run_time_sec": 60,
        "run_time_day_sec": 60,
        "run_time_night_sec": 60,
    }
    item[field] = value

    response = client.put(
        f"/api/routes/{route_id}/stops/forward", json={"items": [item]}
    )

    assert response.status_code == 400
    assert (
        "целым числом" in response.json()["detail"]
        or "отрицатель" in response.json()["detail"]
    )


def test_replace_trace_rejects_duplicate_sequence(client, route_id):
    first = create_stop(client, "Вокзал", "100")
    second = create_stop(client, "Автовокзал", "101")

    response = client.put(f"/api/routes/{route_id}/stops/forward", json={"items": [
        {"stop_id": first, "sequence": 1, "distance_from_prev_km": 0},
        {"stop_id": second, "sequence": 1, "distance_from_prev_km": 1},
    ]})

    assert response.status_code == 400
    assert "не иметь пропусков" in response.json()["detail"]


def test_delete_referenced_stop_returns_conflict(client, route_id):
    stop_id = create_stop(client, "Вокзал", "100")
    saved = client.put(f"/api/routes/{route_id}/stops/forward", json={"items": [
        {"stop_id": stop_id, "sequence": 1, "distance_from_prev_km": 0},
    ]})
    assert saved.status_code == 200

    response = client.delete(f"/api/stops/{stop_id}")

    assert response.status_code == 409


def test_stop_external_code_must_be_unique(client):
    create_stop(client, "Вокзал", "100")

    response = client.post("/api/stops", json={
        "name": "Другой вокзал",
        "external_code": "100",
    })

    assert response.status_code == 400
