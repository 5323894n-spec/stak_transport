# -*- coding: utf-8 -*-
from tests.test_repair_requests_api import make_client


def test_repeated_fault_is_linked_to_previous_request(tmp_path):
    client, bus_id = make_client(tmp_path)
    payload = {"vehicle_id": bus_id, "odometer": 12000, "fault_description": "Течь радиатора"}
    first = client.post("/api/repairs/requests", json=payload).json()
    second = client.post("/api/repairs/requests", json={**payload, "odometer": 12100})
    assert second.status_code == 201, second.text
    assert second.json()["repeated"] == 1
    assert second.json()["repeated_from_id"] == first["id"]
    repeats = client.get("/api/repairs/repeats")
    assert repeats.status_code == 200
    assert repeats.json()["items"][0]["previous_number"] == first["request_number"]

def _move_request_days_back(request_id, days):
    import app.db as db
    con = db.connect()
    try:
        con.execute(
            "UPDATE repair_requests SET created_at=datetime('now', ?) WHERE id=?",
            (f"-{days} days", request_id),
        )
        con.commit()
    finally:
        con.close()


def test_custom_repeat_window_excludes_older_fault(tmp_path):
    client, bus_id = make_client(tmp_path)
    payload = {"vehicle_id": bus_id, "odometer": 12000, "fault_description": "Шум генератора"}
    first = client.post("/api/repairs/requests", json=payload).json()
    _move_request_days_back(first["id"], 10)
    assert client.post("/api/settings", json={"repair_repeat_days": 5}).status_code == 200
    second = client.post("/api/repairs/requests", json={**payload, "odometer": 12100})
    assert second.status_code == 201, second.text
    assert second.json()["repeated"] == 0
    assert second.json()["repeated_from_id"] is None


def test_custom_repeat_window_links_fault_inside_period(tmp_path):
    client, bus_id = make_client(tmp_path)
    payload = {"vehicle_id": bus_id, "odometer": 12000, "fault_description": "Шум генератора"}
    first = client.post("/api/repairs/requests", json=payload).json()
    _move_request_days_back(first["id"], 10)
    assert client.post("/api/settings", json={"repair_repeat_days": 15}).status_code == 200
    second = client.post("/api/repairs/requests", json={**payload, "odometer": 12100})
    assert second.status_code == 201, second.text
    assert second.json()["repeated"] == 1
    assert second.json()["repeated_from_id"] == first["id"]


def test_repeat_window_setting_rejects_out_of_range_value(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post("/api/settings", json={"repair_repeat_days": 0})
    assert response.status_code == 400
    assert "период" in response.json()["detail"].lower()