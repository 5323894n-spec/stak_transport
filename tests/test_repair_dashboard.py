# -*- coding: utf-8 -*-
from tests.test_repair_operations_api import create_order
from tests.test_repair_requests_api import make_client


def test_repair_dashboard_and_vehicle_card(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    dashboard = client.get("/api/repairs/dashboard")
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["active_orders"] == 1
    assert dashboard.json()["open_requests"] == 1
    assert dashboard.json()["kanban"]["черновик"][0]["order_number"] == order["order_number"]
    card = client.get(f"/api/repairs/vehicles/{bus_id}/card")
    assert card.status_code == 200, card.text
    assert card.json()["vehicle"]["garage_number"] == "Р-101"
    assert card.json()["active_orders"][0]["order_number"] == order["order_number"]


def test_repair_dashboard_marks_overdue_order(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    import app.db as db
    con = db.connect()
    try:
        con.execute("UPDATE repair_orders SET planned_end='2026-01-01T10:00:00' WHERE id=?", (order["id"],))
        con.commit()
    finally:
        con.close()
    assert client.get("/api/repairs/dashboard").json()["overdue_orders"] == 1
