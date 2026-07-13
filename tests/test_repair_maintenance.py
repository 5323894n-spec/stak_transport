# -*- coding: utf-8 -*-
from tests.test_repair_requests_api import make_client


def test_maintenance_evaluation_creates_one_request_and_notification(tmp_path):
    client, bus_id = make_client(tmp_path)
    repair_type_id = client.get("/api/repairs/references").json()["repair_types"][0]["id"]
    plan = client.post("/api/repairs/maintenance/plans", json={
        "vehicle_id": bus_id, "repair_type_id": repair_type_id,
        "name": "ТО по пробегу", "next_date": "2026-07-12",
        "next_odometer": 12000, "warning_days": 7, "warning_km": 500,
    })
    assert plan.status_code == 201, plan.text
    first = client.post("/api/repairs/maintenance/evaluate", json={"date": "2026-07-12"})
    second = client.post("/api/repairs/maintenance/evaluate", json={"date": "2026-07-12"})
    assert first.status_code == 200, first.text
    assert first.json()["requests_created"] == 1
    assert second.json()["requests_created"] == 0
    requests = client.get("/api/repairs/requests").json()["items"]
    assert requests[0]["request_source"] == "плановое ТО"
    plans = client.get("/api/repairs/maintenance/plans").json()["items"]
    assert plans[0]["garage_number"] == "Р-101"


def test_maintenance_not_due_does_not_create_request(tmp_path):
    client, bus_id = make_client(tmp_path)
    repair_type_id = client.get("/api/repairs/references").json()["repair_types"][0]["id"]
    client.post("/api/repairs/maintenance/plans", json={
        "vehicle_id": bus_id, "repair_type_id": repair_type_id,
        "name": "Будущее ТО", "next_date": "2026-08-12", "next_odometer": 20000,
    })
    result = client.post("/api/repairs/maintenance/evaluate", json={"date": "2026-07-12"})
    assert result.json()["requests_created"] == 0
