# -*- coding: utf-8 -*-
from fastapi.testclient import TestClient


def make_client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "route-migration-api.db")
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
    return client


def add_route(client, number, stops):
    response = client.post("/api/refs/routes", json={
        "number": number,
        "name": f"Маршрут {number}",
        "stops": stops,
    })
    assert response.status_code == 200
    return response.json()["id"]


def test_migrate_single_route_api(tmp_path):
    client = make_client(tmp_path)
    route_id = add_route(client, "12", "Вокзал, Площадь")

    first = client.post(f"/api/routes/{route_id}/migrate-network")
    second = client.post(f"/api/routes/{route_id}/migrate-network")

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "migrated"
    assert second.json()["status"] == "unchanged"
    network = client.get(f"/api/routes/{route_id}/network").json()
    assert [row["stop"]["name"] for row in network["forward"]] == [
        "Вокзал", "Площадь"
    ]


def test_bulk_migrate_routes_api(tmp_path):
    client = make_client(tmp_path)
    add_route(client, "12", "Вокзал, Площадь")
    add_route(client, "13", "Парк, Рынок")

    response = client.post("/api/routes/migrate-network")

    assert response.status_code == 200, response.text
    assert response.json()["summary"] == {
        "total": 2,
        "migrated": 2,
        "unchanged": 0,
        "needs_review": 0,
        "failed": 0,
    }
