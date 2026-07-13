# -*- coding: utf-8 -*-
from tests.test_repair_operations_api import create_order
from tests.test_repair_requests_api import make_client


def test_assign_start_and_finish_worker(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    me = client.get("/api/me").json()
    assigned = client.post(f"/api/repairs/orders/{order['id']}/workers", json={
        "worker_id": me["id"], "role": "слесарь",
        "planned_hours": 4, "hourly_rate": 800,
    })
    assert assigned.status_code == 201, assigned.text
    assignment_id = assigned.json()["id"]
    started = client.post(f"/api/repairs/workers/{assignment_id}/start")
    assert started.status_code == 200
    assert started.json()["status"] == "в работе"
    finished = client.post(f"/api/repairs/workers/{assignment_id}/finish", json={"actual_hours": 5})
    assert finished.status_code == 200, finished.text
    detail = client.get(f"/api/repairs/orders/{order['id']}/work").json()
    assert detail["workers"][0]["actual_hours"] == 5
    assert detail["order"]["labor_cost"] == 4000
    assert detail["order"]["actual_hours"] == 5


def test_duplicate_worker_assignment_is_rejected(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    worker_id = client.get("/api/me").json()["id"]
    payload = {"worker_id": worker_id, "role": "слесарь"}
    assert client.post(f"/api/repairs/orders/{order['id']}/workers", json=payload).status_code == 201
    assert client.post(f"/api/repairs/orders/{order['id']}/workers", json=payload).status_code == 409
