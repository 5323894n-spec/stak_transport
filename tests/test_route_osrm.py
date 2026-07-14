# -*- coding: utf-8 -*-
import json

from fastapi.testclient import TestClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def make_client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "route-osrm.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
    finally:
        con.close()
    client = TestClient(app)
    token = client.post("/api/login", json={
        "username": "admin", "password": "admin",
    }).json()["token"]
    client.headers.update({"Authorization": "Bearer " + token})
    route_id = client.post("/api/refs/routes", json={
        "number": "12", "name": "OSRM маршрут",
    }).json()["id"]
    return client, route_id


def add_trace(client, route_id, with_coordinates=True):
    coordinates = [
        {"latitude": 56.801, "longitude": 35.901},
        {"latitude": 56.802, "longitude": 35.902},
    ] if with_coordinates else [{}, {}]
    stop_ids = []
    for index, name in enumerate(("Вокзал", "Автовокзал")):
        response = client.post("/api/stops", json={
            "name": name,
            "external_code": str(100 + index),
            **coordinates[index],
        })
        stop_ids.append(response.json()["id"])
    response = client.put(f"/api/routes/{route_id}/stops/forward", json={"items": [
        {"stop_id": stop_ids[0], "sequence": 1, "distance_from_prev_km": 0},
        {"stop_id": stop_ids[1], "sequence": 2, "distance_from_prev_km": 1.0,
         "run_time_sec": 300},
    ]})
    assert response.status_code == 200


def test_osrm_client_validates_and_normalizes_response(monkeypatch):
    import app.osrm as osrm

    monkeypatch.setattr(osrm.urllib.request, "urlopen", lambda request, timeout: FakeResponse({
        "code": "Ok",
        "routes": [{
            "geometry": {"type": "LineString", "coordinates": [[35.901, 56.801], [35.902, 56.802]]},
            "legs": [{"distance": 1500.4, "duration": 240.2}],
        }],
    }))

    result = osrm.request_route([(35.901, 56.801), (35.902, 56.802)],
                                base_url="https://router.test")

    assert result["legs"] == [{"distance": 1500.4, "duration": 240.2}]
    assert result["geometry"]["type"] == "LineString"


def test_osrm_preview_does_not_apply_until_confirmed(tmp_path, monkeypatch):
    import app.osrm as osrm

    client, route_id = make_client(tmp_path)
    add_trace(client, route_id)
    monkeypatch.setattr(osrm, "request_route", lambda coordinates, **kwargs: {
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "legs": [{"distance": 1500, "duration": 240}],
    })

    preview = client.post(f"/api/routes/{route_id}/osrm/preview/forward")

    assert preview.status_code == 200, preview.text
    assert preview.json()["diff"][0]["old_distance_km"] == 1.0
    assert preview.json()["diff"][0]["new_distance_km"] == 1.5
    before = client.get(f"/api/routes/{route_id}/network").json()
    assert before["forward"][1]["distance_from_prev_km"] == 1.0

    applied = client.post(f"/api/routes/{route_id}/osrm/apply/forward", json={
        "preview_token": preview.json()["preview_token"],
    })

    assert applied.status_code == 200, applied.text
    after = client.get(f"/api/routes/{route_id}/network").json()
    assert after["forward"][1]["distance_from_prev_km"] == 1.5
    assert after["forward"][1]["run_time_sec"] == 240
    assert after["forward"][1]["distance_source"] == "auto_osrm"


def test_osrm_preview_requires_coordinates(tmp_path):
    client, route_id = make_client(tmp_path)
    add_trace(client, route_id, with_coordinates=False)

    response = client.post(f"/api/routes/{route_id}/osrm/preview/forward")

    assert response.status_code == 400
    assert "координат" in response.json()["detail"]


def test_osrm_timeout_is_reported_as_service_unavailable(tmp_path, monkeypatch):
    import app.osrm as osrm

    client, route_id = make_client(tmp_path)
    add_trace(client, route_id)

    def timeout(*args, **kwargs):
        raise osrm.OSRMTimeout("Сервис OSRM не ответил вовремя")

    monkeypatch.setattr(osrm, "request_route", timeout)
    response = client.post(f"/api/routes/{route_id}/osrm/preview/forward")

    assert response.status_code == 503
    assert "OSRM" in response.json()["detail"]
