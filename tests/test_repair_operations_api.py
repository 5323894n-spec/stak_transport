# -*- coding: utf-8 -*-
from tests.test_repair_requests_api import make_client


def create_order(client, bus_id):
    request = client.post("/api/repairs/requests", json={
        "vehicle_id": bus_id, "odometer": 12000, "fault_description": "Стук в подвеске",
    }).json()
    repair_type_id = client.get("/api/repairs/references").json()["repair_types"][0]["id"]
    return client.post("/api/repairs/orders", json={
        "request_id": request["id"], "vehicle_id": bus_id, "repair_type_id": repair_type_id,
    }).json()


def test_operation_lifecycle_recalculates_order_hours(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    operation = client.post(f"/api/repairs/orders/{order['id']}/operations", json={
        "name": "Замена сайлентблока", "norm_hours": 2.5,
    })
    assert operation.status_code == 201, operation.text
    operation_id = operation.json()["id"]
    started = client.post(f"/api/repairs/operations/{operation_id}/start")
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "в работе"
    completed = client.post(f"/api/repairs/operations/{operation_id}/complete", json={
        "actual_hours": 3.0, "result": "Деталь заменена",
    })
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "выполнена"
    detail = client.get(f"/api/repairs/orders/{order['id']}/work").json()
    assert detail["order"]["actual_hours"] == 3.0
    assert detail["operations"][0]["result"] == "Деталь заменена"
