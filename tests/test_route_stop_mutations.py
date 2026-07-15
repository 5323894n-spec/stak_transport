# -*- coding: utf-8 -*-
from fastapi.testclient import TestClient


def make_client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin, hash_password
    from app.main import app

    db.DB_PATH = str(tmp_path / "route-stop-mutations.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
        con.execute(
            "INSERT INTO users(username,password_hash,full_name,role) VALUES(?,?,?,?)",
            ("dispatcher", hash_password("12345"), "Диспетчер", "диспетчер"),
        )
        con.commit()
    finally:
        con.close()
    return TestClient(app)


def login(client, username, password):
    token = client.post("/api/login", json={
        "username": username, "password": password,
    }).json()["token"]
    client.headers.update({"Authorization": "Bearer " + token})


def test_update_stop_changes_searchable_fields(tmp_path):
    client = make_client(tmp_path)
    login(client, "admin", "admin")
    stop_id = client.post("/api/stops", json={
        "name": "Старое название", "external_code": "100",
    }).json()["id"]

    response = client.put(f"/api/stops/{stop_id}", json={
        "name": "Новая площадь",
        "address": "ул. Новая, 1",
        "latitude": 56.858,
        "longitude": 35.917,
        "is_terminal": 1,
    })

    assert response.status_code == 200, response.text
    item = client.get("/api/stops?q=новая").json()["items"][0]
    assert item["name"] == "Новая площадь"
    assert item["address"] == "ул. Новая, 1"
    assert item["latitude"] == 56.858
    assert item["is_terminal"] == 1


def test_delete_unreferenced_stop(tmp_path):
    client = make_client(tmp_path)
    login(client, "admin", "admin")
    stop_id = client.post("/api/stops", json={"name": "Временная"}).json()["id"]

    response = client.delete(f"/api/stops/{stop_id}")

    assert response.status_code == 200
    assert client.get("/api/stops?q=временная").json()["items"] == []


def test_dispatcher_cannot_write_route_stops(tmp_path):
    client = make_client(tmp_path)
    login(client, "dispatcher", "12345")

    response = client.post("/api/stops", json={"name": "Запрещённая"})

    assert response.status_code == 403
