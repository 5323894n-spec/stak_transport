# -*- coding: utf-8 -*-
from tests.test_repair_operations_api import create_order
from tests.test_repair_requests_api import make_client


def move_to_control(client, order_id):
    for status in ("диагностика", "готов к работе", "в работе", "контроль"):
        response = client.post(f"/api/repairs/orders/{order_id}/status", json={"status": status})
        assert response.status_code == 200, response.text


def test_positive_inspection_allows_close_and_creates_history(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    move_to_control(client, order["id"])
    blocked = client.post(f"/api/repairs/orders/{order['id']}/close", json={"result": "Исправен"})
    assert blocked.status_code == 409
    inspection = client.post(f"/api/repairs/orders/{order['id']}/inspection", json={
        "result": "годен", "release_allowed": True, "comment": "Замечаний нет",
    })
    assert inspection.status_code == 201, inspection.text
    closed = client.post(f"/api/repairs/orders/{order['id']}/close", json={"result": "Ремонт завершён"})
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "завершен"
    history = client.get(f"/api/vehicles/{bus_id}/repair-history")
    assert history.status_code == 200, history.text
    assert history.json()["items"][0]["order_number"] == order["order_number"]


def test_negative_inspection_returns_order_to_work(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    move_to_control(client, order["id"])
    response = client.post(f"/api/repairs/orders/{order['id']}/inspection", json={
        "result": "не годен", "release_allowed": False, "defects": "Остался люфт",
    })
    assert response.status_code == 201, response.text
    assert response.json()["order_status"] == "в работе"
