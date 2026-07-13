# -*- coding: utf-8 -*-
from tests.test_repair_operations_api import create_order
from tests.test_repair_requests_api import make_client


def test_alert_evaluation_creates_overdue_and_low_stock_notifications_once(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)

    import app.db as db
    con = db.connect()
    try:
        con.execute(
            "UPDATE repair_orders SET planned_end=? WHERE id=?",
            ("2026-07-01T12:00:00", order["id"]),
        )
        warehouse_id = con.execute("SELECT id FROM warehouses WHERE code='MAIN'").fetchone()[0]
        con.execute(
            "INSERT INTO parts(code,name,warehouse_id,stock_qty,min_qty,unit_price) VALUES(?,?,?,?,?,?)",
            ("LOW-001", "Фильтр масляный", warehouse_id, 2, 5, 500),
        )
        con.commit()
    finally:
        con.close()

    first = client.post("/api/repairs/alerts/evaluate", json={"date": "2026-07-12"})
    second = client.post("/api/repairs/alerts/evaluate", json={"date": "2026-07-12"})
    assert first.status_code == 200, first.text
    assert first.json()["overdue_created"] == 1
    assert first.json()["low_stock_created"] == 1
    assert first.json()["notifications_created"] == 2
    assert second.status_code == 200, second.text
    assert second.json()["notifications_created"] == 0

    notifications = client.get("/api/notifications").json()["items"]
    repair_messages = [item["message"] for item in notifications if item["category"] in {"просрочка ремонта", "дефицит запчастей"}]
    assert len(repair_messages) == 2
    assert any(order["order_number"] in message for message in repair_messages)
    assert any("Фильтр масляный" in message for message in repair_messages)


def test_alert_evaluation_ignores_current_orders_and_sufficient_stock(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)

    import app.db as db
    con = db.connect()
    try:
        con.execute("UPDATE repair_orders SET planned_end=? WHERE id=?", ("2026-08-01T12:00:00", order["id"]))
        warehouse_id = con.execute("SELECT id FROM warehouses WHERE code='MAIN'").fetchone()[0]
        con.execute(
            "INSERT INTO parts(code,name,warehouse_id,stock_qty,min_qty,unit_price) VALUES(?,?,?,?,?,?)",
            ("OK-001", "Фильтр воздушный", warehouse_id, 10, 5, 700),
        )
        con.commit()
    finally:
        con.close()

    result = client.post("/api/repairs/alerts/evaluate", json={"date": "2026-07-12"})
    assert result.status_code == 200, result.text
    assert result.json()["notifications_created"] == 0