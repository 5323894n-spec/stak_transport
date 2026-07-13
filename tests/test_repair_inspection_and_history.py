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
    import app.db as db

    con = db.connect()
    try:
        master_id = con.execute(
            "SELECT id FROM users WHERE username='admin'"
        ).fetchone()[0]
        con.execute(
            "UPDATE repair_orders SET responsible_master_id=?,labor_cost=1200,"
            "parts_cost=2300,external_cost=400,other_cost=100,total_cost=4000 "
            "WHERE id=?",
            (master_id, order["id"]),
        )
        con.commit()
    finally:
        con.close()
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
    snapshot = history.json()["items"][0]
    assert snapshot["order_number"] == order["order_number"]
    assert snapshot["labor_cost"] == 1200
    assert snapshot["parts_cost"] == 2300
    assert snapshot["external_cost"] == 400
    assert snapshot["other_cost"] == 100
    assert snapshot["total_cost"] == 4000
    assert snapshot["master_name"] == "Администратор системы"

    con = db.connect()
    try:
        con.execute(
            "UPDATE repair_orders SET labor_cost=0,total_cost=0,responsible_master_id=NULL "
            "WHERE id=?",
            (order["id"],),
        )
        con.commit()
    finally:
        con.close()
    unchanged = client.get(f"/api/vehicles/{bus_id}/repair-history").json()["items"][0]
    assert unchanged["labor_cost"] == 1200
    assert unchanged["total_cost"] == 4000
    assert unchanged["master_name"] == "Администратор системы"


def test_negative_inspection_returns_order_to_work(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    move_to_control(client, order["id"])
    response = client.post(f"/api/repairs/orders/{order['id']}/inspection", json={
        "result": "не годен", "release_allowed": False, "defects": "Остался люфт",
    })
    assert response.status_code == 201, response.text
    assert response.json()["order_status"] == "в работе"
