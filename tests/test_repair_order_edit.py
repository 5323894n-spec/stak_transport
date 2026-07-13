# -*- coding: utf-8 -*-
from tests.test_repair_conflicts import _second_bus, _scheduled_order
from tests.test_repair_operations_api import create_order
from tests.test_repair_requests_api import make_client


def test_edit_order_recalculates_external_and_other_costs(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)

    import app.db as db
    con = db.connect()
    try:
        con.execute(
            "UPDATE repair_orders SET labor_cost=100,parts_cost=200,total_cost=300 WHERE id=?",
            (order["id"],),
        )
        con.commit()
    finally:
        con.close()

    updated = client.patch(f"/api/repairs/orders/{order['id']}", json={
        "diagnosis": "Ремонт у подрядчика",
        "external_cost": 300,
        "other_cost": 50,
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["diagnosis"] == "Ремонт у подрядчика"
    assert updated.json()["external_cost"] == 300
    assert updated.json()["other_cost"] == 50
    assert updated.json()["total_cost"] == 650

    con = db.connect()
    try:
        audit = con.execute(
            "SELECT action,new_value FROM audit_log WHERE object_type='repair_order' AND object_id=? ORDER BY id DESC LIMIT 1",
            (str(order["id"]),),
        ).fetchone()
    finally:
        con.close()
    assert audit and "редактирование" in audit["action"]
    assert "Ремонт у подрядчика" in audit["new_value"]


def test_edit_order_rejects_negative_costs(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    response = client.patch(f"/api/repairs/orders/{order['id']}", json={"external_cost": -1})
    assert response.status_code == 400
    assert "отрицатель" in response.json()["detail"].lower()


def test_edit_order_rejects_overlapping_repair_post(tmp_path):
    client, first_bus = make_client(tmp_path)
    second_bus = _second_bus()
    refs = client.get("/api/repairs/references").json()
    post_id = refs["repair_posts"][0]["id"]
    first = _scheduled_order(client, first_bus, "2026-07-15T09:00:00", "2026-07-15T12:00:00", post_id=post_id)
    second = _scheduled_order(client, second_bus, "2026-07-15T10:00:00", "2026-07-15T13:00:00")
    assert first.status_code == 201 and second.status_code == 201
    conflict = client.patch(f"/api/repairs/orders/{second.json()['id']}", json={"repair_post_id": post_id})
    assert conflict.status_code == 409
    assert "пост" in conflict.json()["detail"].lower()