# -*- coding: utf-8 -*-
from fastapi.testclient import TestClient


def make_client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "repair-api.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
        bus_id = con.execute(
            "INSERT INTO buses(garage_number,plate,brand,model,odometer) VALUES(?,?,?,?,?)",
            ("Р-101", "А101АА69", "ПАЗ", "Vector", 12000),
        ).lastrowid
        con.commit()
    finally:
        con.close()
    client = TestClient(app)
    token = client.post("/api/login", json={"username": "admin", "password": "admin"}).json()["token"]
    client.headers.update({"Authorization": "Bearer " + token})
    return client, bus_id


def test_repair_request_lifecycle_and_numbering(tmp_path):
    client, bus_id = make_client(tmp_path)
    payload = {
        "vehicle_id": bus_id,
        "odometer": 12000,
        "fault_description": "Падение давления масла",
        "request_source": "диспетчер",
        "criticality": "высокая",
    }
    first = client.post("/api/repairs/requests", json=payload)
    second = client.post("/api/repairs/requests", json={**payload, "fault_description": "Течь масла"})
    assert first.status_code == 201, first.text
    assert first.json()["request_number"].endswith("000001")
    assert second.json()["request_number"].endswith("000002")

    items = client.get("/api/repairs/requests?status=новая").json()["items"]
    assert len(items) == 2
    assert items[0]["garage_number"] == "Р-101"

    request_id = first.json()["id"]
    updated = client.patch(f"/api/repairs/requests/{request_id}", json={"criticality": "критическая"})
    assert updated.status_code == 200
    assert updated.json()["criticality"] == "критическая"

    cancelled = client.post(f"/api/repairs/requests/{request_id}/cancel", json={"reason": "Создана ошибочно"})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "отменена"


def test_repair_request_validates_required_fields(tmp_path):
    client, bus_id = make_client(tmp_path)
    response = client.post("/api/repairs/requests", json={"vehicle_id": bus_id, "odometer": 1})
    assert response.status_code == 400
