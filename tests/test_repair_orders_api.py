# -*- coding: utf-8 -*-
from tests.test_repair_requests_api import make_client


def test_create_order_from_request_and_change_status(tmp_path):
    client, bus_id = make_client(tmp_path)
    request = client.post("/api/repairs/requests", json={
        "vehicle_id": bus_id, "odometer": 12000,
        "fault_description": "Неисправность тормозов", "criticality": "высокая",
    }).json()
    refs = client.get("/api/repairs/references")
    assert refs.status_code == 200, refs.text
    repair_type_id = refs.json()["repair_types"][0]["id"]
    created = client.post("/api/repairs/orders", json={
        "request_id": request["id"], "vehicle_id": bus_id,
        "repair_type_id": repair_type_id,
        "diagnosis": "Требуется диагностика тормозной системы",
    })
    assert created.status_code == 201, created.text
    assert created.json()["order_number"].endswith("000001")
    assert created.json()["status"] == "черновик"
    duplicate = client.post("/api/repairs/orders", json={
        "request_id": request["id"], "vehicle_id": bus_id, "repair_type_id": repair_type_id,
    })
    assert duplicate.status_code == 409
    order_id = created.json()["id"]
    changed = client.post(f"/api/repairs/orders/{order_id}/status", json={"status": "диагностика"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["status"] == "диагностика"
    assert client.get("/api/repairs/orders?active_only=true").json()["items"][0]["request_number"] == request["request_number"]
