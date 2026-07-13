# -*- coding: utf-8 -*-
from tests.test_repair_requests_api import make_client


def _scheduled_order(client, bus_id, start, end, *, post_id=None):
    request = client.post("/api/repairs/requests", json={
        "vehicle_id": bus_id,
        "odometer": 12000,
        "fault_description": f"Проверка конфликта {bus_id} {start}",
    })
    assert request.status_code == 201, request.text
    refs = client.get("/api/repairs/references").json()
    payload = {
        "request_id": request.json()["id"],
        "vehicle_id": bus_id,
        "repair_type_id": refs["repair_types"][0]["id"],
        "planned_start": start,
        "planned_end": end,
    }
    if post_id is not None:
        payload["repair_post_id"] = post_id
    return client.post("/api/repairs/orders", json=payload)


def _second_bus():
    import app.db as db
    con = db.connect()
    try:
        bus_id = con.execute(
            "INSERT INTO buses(garage_number,plate,odometer) VALUES(?,?,?)",
            ("Р-202", "А202АА69", 22000),
        ).lastrowid
        con.commit()
        return bus_id
    finally:
        con.close()


def test_worker_cannot_be_assigned_to_overlapping_orders(tmp_path):
    client, first_bus = make_client(tmp_path)
    second_bus = _second_bus()
    first = _scheduled_order(client, first_bus, "2026-07-14T09:00:00", "2026-07-14T12:00:00")
    second = _scheduled_order(client, second_bus, "2026-07-14T11:00:00", "2026-07-14T14:00:00")
    assert first.status_code == 201 and second.status_code == 201
    worker_id = client.get("/api/me").json()["id"]
    payload = {"worker_id": worker_id, "role": "слесарь"}
    assert client.post(f"/api/repairs/orders/{first.json()['id']}/workers", json=payload).status_code == 201
    conflict = client.post(f"/api/repairs/orders/{second.json()['id']}/workers", json=payload)
    assert conflict.status_code == 409
    assert "пересека" in conflict.json()["detail"].lower()


def test_repair_post_cannot_be_used_by_overlapping_orders(tmp_path):
    client, first_bus = make_client(tmp_path)
    second_bus = _second_bus()
    post_id = client.get("/api/repairs/references").json()["repair_posts"][0]["id"]
    first = _scheduled_order(client, first_bus, "2026-07-14T09:00:00", "2026-07-14T12:00:00", post_id=post_id)
    assert first.status_code == 201, first.text
    conflict = _scheduled_order(client, second_bus, "2026-07-14T11:59:00", "2026-07-14T15:00:00", post_id=post_id)
    assert conflict.status_code == 409
    assert "пост" in conflict.json()["detail"].lower()