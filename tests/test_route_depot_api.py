# -*- coding: utf-8 -*-
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "route-depot-api.db")
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
        "number": "44",
        "name": "Вокзал — Автопарк",
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _create_stop(client, name, code, latitude=None, longitude=None):
    response = client.post("/api/stops", json={
        "name": name,
        "external_code": code,
        "latitude": latitude,
        "longitude": longitude,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _items(first_stop, second_stop):
    return [
        {
            "stop_id": first_stop,
            "sequence": 1,
            "distance_from_prev_km": 0,
            "run_time_day_sec": 60,
            "run_time_night_sec": 50,
        },
        {
            "stop_id": second_stop,
            "sequence": 2,
            "distance_from_prev_km": 1.25,
            "run_time_day_sec": 120,
            "run_time_night_sec": 100,
        },
    ]


def test_replace_and_get_depot_stops_with_cumulative_values(client, route_id):
    first = _create_stop(client, "Автопарк", "300", 56.801, 35.901)
    second = _create_stop(client, "Вокзал", "301", 56.802, 35.902)

    saved = client.put(
        f"/api/routes/{route_id}/depot-stops/depot_out",
        json={"items": _items(first, second)},
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["direction"] == "depot_out"
    assert saved.json()["items"][-1]["cumulative_km"] == 1.25
    assert saved.json()["items"][-1]["cumulative_day_sec"] == 180
    assert saved.json()["items"][-1]["cumulative_night_sec"] == 150

    response = client.get(
        f"/api/routes/{route_id}/depot-stops?direction=depot_out"
    )

    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert [row["stop"]["name"] for row in rows] == ["Автопарк", "Вокзал"]
    assert rows[0]["stop"] == {
        "id": first,
        "external_code": "300",
        "name": "Автопарк",
        "latitude": 56.801,
        "longitude": 35.901,
        "address": None,
        "stop_kind": "обычная",
        "is_terminal": 0,
        "has_dispatcher": 0,
        "municipality": None,
        "registry_flags": "{}",
        "source": "manual",
        "active": 1,
        "notes": None,
    }
    assert rows[-1]["cumulative_km"] == 1.25
    assert rows[-1]["cumulative_day_sec"] == 180
    assert rows[-1]["cumulative_night_sec"] == 150


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("distance_from_prev_km", -0.1, "Расстояние"),
        ("run_time_day_sec", -1, "Дневное время"),
        ("run_time_night_sec", -1, "Ночное время"),
    ],
)
def test_replace_rejects_negative_values(client, route_id, field, value, message):
    stop_id = _create_stop(client, "Автопарк", "300")
    item = {
        "stop_id": stop_id,
        "sequence": 1,
        "distance_from_prev_km": 0,
        "run_time_day_sec": 0,
        "run_time_night_sec": 0,
    }
    item[field] = value

    response = client.put(
        f"/api/routes/{route_id}/depot-stops/depot_out",
        json={"items": [item]},
    )

    assert response.status_code == 400
    assert message in response.json()["detail"]
    assert "отрицатель" in response.json()["detail"]


@pytest.mark.parametrize("direction", ["forward", "wrong"])
def test_depot_api_rejects_invalid_direction(client, route_id, direction):
    response = client.put(
        f"/api/routes/{route_id}/depot-stops/{direction}", json={"items": []}
    )

    assert response.status_code == 400
    assert "depot_out" in response.json()["detail"]
    assert "depot_in" in response.json()["detail"]


@pytest.mark.parametrize("sequences", [(1, 1), (1, 3)])
def test_replace_rejects_duplicate_or_gapped_sequence(client, route_id, sequences):
    first = _create_stop(client, "Автопарк", "300")
    second = _create_stop(client, "Вокзал", "301")
    items = _items(first, second)
    items[0]["sequence"], items[1]["sequence"] = sequences

    response = client.put(
        f"/api/routes/{route_id}/depot-stops/depot_out", json={"items": items}
    )

    assert response.status_code == 400
    assert "начинаться с 1" in response.json()["detail"]
    assert "не иметь пропусков" in response.json()["detail"]


@pytest.mark.parametrize(
    "value", ["1e999", float("nan"), float("inf"), float("-inf")]
)
def test_normalize_items_rejects_non_finite_distance(value):
    from app.route_depot import normalize_items

    with pytest.raises(ValueError, match="конечным числом"):
        normalize_items([{
            "stop_id": 1,
            "sequence": 1,
            "distance_from_prev_km": value,
            "run_time_day_sec": 0,
            "run_time_night_sec": 0,
        }])


def test_normalize_items_rejects_non_finite_cumulative_distance():
    from app.route_depot import normalize_items

    with pytest.raises(ValueError, match="Накопленное расстояние"):
        normalize_items([
            {
                "stop_id": 1,
                "sequence": 1,
                "distance_from_prev_km": "9e307",
                "run_time_day_sec": 0,
                "run_time_night_sec": 0,
            },
            {
                "stop_id": 2,
                "sequence": 2,
                "distance_from_prev_km": "9e307",
                "run_time_day_sec": 0,
                "run_time_night_sec": 0,
            },
        ])


@pytest.mark.parametrize("field", ["run_time_day_sec", "run_time_night_sec"])
def test_replace_rejects_runtime_overflow_with_http_400(client, route_id, field):
    stop_id = _create_stop(client, "Автопарк", "300")
    item = {
        "stop_id": stop_id,
        "sequence": 1,
        "distance_from_prev_km": 0,
        "run_time_day_sec": 0,
        "run_time_night_sec": 0,
    }
    item[field] = "1e999"

    response = client.put(
        f"/api/routes/{route_id}/depot-stops/depot_out", json={"items": [item]}
    )

    assert response.status_code == 400
    assert "целым числом" in response.json()["detail"]


def test_read_only_user_cannot_replace_depot_stops(client, route_id):
    import app.db as db
    from app.auth import hash_password

    con = db.connect()
    try:
        con.execute(
            "INSERT INTO users(username,password_hash,full_name,role,active) "
            "VALUES(?,?,?,?,1)",
            ("viewer", hash_password("secret"), "Наблюдатель", "руководитель"),
        )
        con.commit()
    finally:
        con.close()
    viewer = TestClient(client.app)
    token = viewer.post(
        "/api/login", json={"username": "viewer", "password": "secret"}
    ).json()["token"]
    viewer.headers.update({"Authorization": "Bearer " + token})

    response = viewer.put(
        f"/api/routes/{route_id}/depot-stops/depot_out", json={"items": []}
    )

    assert response.status_code == 403


def test_delete_stop_used_by_depot_route_returns_conflict(client, route_id):
    stop_id = _create_stop(client, "Автопарк", "300")
    saved = client.put(
        f"/api/routes/{route_id}/depot-stops/depot_out",
        json={"items": [{
            "stop_id": stop_id,
            "sequence": 1,
            "distance_from_prev_km": 0,
            "run_time_day_sec": 0,
            "run_time_night_sec": 0,
        }]},
    )
    assert saved.status_code == 200, saved.text

    response = client.delete(f"/api/stops/{stop_id}")

    assert response.status_code == 409
    assert "маршрут" in response.json()["detail"].lower()


def test_replace_checks_references_before_deleting_old_rows(client, route_id):
    first = _create_stop(client, "Автопарк", "300")
    second = _create_stop(client, "Вокзал", "301")
    saved = client.put(
        f"/api/routes/{route_id}/depot-stops/depot_out",
        json={"items": _items(first, second)},
    )
    assert saved.status_code == 200, saved.text

    response = client.put(
        f"/api/routes/{route_id}/depot-stops/depot_out",
        json={"items": [{
            "stop_id": 999999,
            "sequence": 1,
            "distance_from_prev_km": 0,
            "run_time_day_sec": 0,
            "run_time_night_sec": 0,
        }]},
    )

    assert response.status_code == 404
    rows = client.get(
        f"/api/routes/{route_id}/depot-stops?direction=depot_out"
    ).json()["items"]
    assert [row["stop_id"] for row in rows] == [first, second]


def test_depot_api_returns_not_found_for_unknown_route_or_stop(client, route_id):
    stop_id = _create_stop(client, "Автопарк", "300")

    missing_route = client.get("/api/routes/999999/depot-stops?direction=depot_out")
    missing_stop = client.put(
        f"/api/routes/{route_id}/depot-stops/depot_out",
        json={"items": [{
            "stop_id": stop_id + 999999,
            "sequence": 1,
            "distance_from_prev_km": 0,
            "run_time_day_sec": 0,
            "run_time_night_sec": 0,
        }]},
    )

    assert missing_route.status_code == 404
    assert missing_route.json()["detail"] == "Маршрут не найден"
    assert missing_stop.status_code == 404
    assert "Остановка" in missing_stop.json()["detail"]


def test_get_uses_compatible_legacy_erm_fallback_when_table_is_empty(client, route_id):
    import app.db as db

    first = _create_stop(client, "Автопарк", "300", 56.801, 35.901)
    second = _create_stop(client, "Вокзал", "301", 56.802, 35.902)
    notes = {
        "source": "ЭРМ",
        "details": {
            "sheets": {
                "из парка": {
                    "sections": [{
                        "sheet": "из парка",
                        "kind": "из парка",
                        "direction": "из парка",
                        "stops": [
                            {
                                "seq": 1,
                                "stop_id": 300,
                                "stop_name": "Автопарк",
                                "distance_km": 0,
                                "travel_time": "00:00:00",
                            },
                            {
                                "seq": 2,
                                "stop_id": 301,
                                "stop_name": "Вокзал",
                                "distance_km": 1.25,
                                "travel_time": "00:03:00",
                            },
                        ],
                    }],
                }
            }
        },
    }
    con = db.connect()
    try:
        con.execute(
            "UPDATE routes SET notes=? WHERE id=?",
            (json.dumps(notes, ensure_ascii=False), route_id),
        )
        con.commit()
    finally:
        con.close()

    response = client.get(
        f"/api/routes/{route_id}/depot-stops?direction=depot_out"
    )

    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert [row["stop_id"] for row in rows] == [first, second]
    assert rows[-1]["cumulative_km"] == 1.25
    assert rows[-1]["cumulative_day_sec"] == 180
    assert rows[-1]["cumulative_night_sec"] == 180
    assert rows[-1]["source"] == "legacy_erm"


def test_legacy_erm_fallback_combines_all_page_sections(client, route_id):
    import app.db as db

    first = _create_stop(client, "Автопарк", "300")
    second = _create_stop(client, "Вокзал", "301")
    third = _create_stop(client, "Площадь", "302")
    sections = [
        {
            "sheet": "из парка",
            "kind": "из парка",
            "stops": [
                {"seq": 1, "stop_id": 300, "stop_name": "Автопарк",
                 "distance_km": 0, "travel_time": "00:00:00"},
                {"seq": 2, "stop_id": 301, "stop_name": "Вокзал",
                 "distance_km": 1.25, "travel_time": "00:03:00"},
            ],
        },
        {
            "sheet": "из парка",
            "kind": "из парка",
            "stops": [
                {"seq": 1, "stop_id": 302, "stop_name": "Площадь",
                 "distance_km": 0.75, "travel_time": "00:02:00"},
            ],
        },
    ]
    con = db.connect()
    try:
        con.execute(
            "UPDATE routes SET notes=? WHERE id=?",
            (json.dumps({"details": {"sheets": {
                "из парка": {"sections": sections}
            }}}, ensure_ascii=False), route_id),
        )
        con.commit()
    finally:
        con.close()

    response = client.get(
        f"/api/routes/{route_id}/depot-stops?direction=depot_out"
    )

    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert [row["stop_id"] for row in rows] == [first, second, third]
    assert [row["sequence"] for row in rows] == [1, 2, 3]
    assert rows[-1]["cumulative_km"] == 2.0
    assert rows[-1]["cumulative_day_sec"] == 300
    assert json.loads(rows[-1]["source_detail"])["section"] == 2


@pytest.mark.parametrize(
    "legacy_distance", ["1e999", float("nan"), float("inf"), -0.5]
)
def test_legacy_erm_fallback_sanitizes_invalid_distance(
    client, route_id, legacy_distance
):
    import math

    import app.db as db

    stop_id = _create_stop(client, "Автопарк", "300")
    notes = {
        "source": "ЭРМ",
        "details": {
            "sheets": {
                "из парка": {
                    "sections": [{
                        "sheet": "из парка",
                        "kind": "из парка",
                        "direction": "из парка",
                        "stops": [{
                            "seq": 1,
                            "stop_id": 300,
                            "stop_name": "Автопарк",
                            "distance_km": legacy_distance,
                            "travel_time": "00:01:00",
                        }],
                    }],
                }
            }
        },
    }
    con = db.connect()
    try:
        con.execute(
            "UPDATE routes SET notes=? WHERE id=?",
            (json.dumps(notes, ensure_ascii=False), route_id),
        )
        con.commit()
    finally:
        con.close()

    response = client.get(
        f"/api/routes/{route_id}/depot-stops?direction=depot_out"
    )

    assert response.status_code == 200, response.text
    assert "NaN" not in response.text
    assert "Infinity" not in response.text
    row = response.json()["items"][0]
    assert row["stop_id"] == stop_id
    assert row["distance_from_prev_km"] == 0
    assert math.isfinite(row["cumulative_km"])
    assert row["cumulative_km"] >= 0


def test_normalized_depot_rows_take_priority_over_legacy_notes(client, route_id):
    import app.db as db

    stop_id = _create_stop(client, "Ручная остановка", "900")
    con = db.connect()
    try:
        con.execute(
            "UPDATE routes SET notes=? WHERE id=?",
            (json.dumps({
                "details": {"sheets": {"из парка": {"sections": [{"stops": [{
                    "seq": 1,
                    "stop_id": 300,
                    "stop_name": "Legacy",
                    "distance_km": 0,
                    "travel_time": "00:00:00",
                }]}]}}}
            }), route_id),
        )
        con.commit()
    finally:
        con.close()
    saved = client.put(
        f"/api/routes/{route_id}/depot-stops/depot_out",
        json={"items": [{
            "stop_id": stop_id,
            "sequence": 1,
            "distance_from_prev_km": 0,
            "run_time_day_sec": 0,
            "run_time_night_sec": 0,
        }]},
    )
    assert saved.status_code == 200, saved.text

    rows = client.get(
        f"/api/routes/{route_id}/depot-stops?direction=depot_out"
    ).json()["items"]

    assert [row["stop"]["name"] for row in rows] == ["Ручная остановка"]
