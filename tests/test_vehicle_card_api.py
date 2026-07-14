# -*- coding: utf-8 -*-
from tests.test_repair_operations_api import create_order
from tests.test_repair_requests_api import make_client


def test_vehicle_card_exposes_summary_and_detailed_tabs(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)

    import app.db as db

    con = db.connect()
    try:
        master_id = con.execute(
            "SELECT id FROM users WHERE username='admin'"
        ).fetchone()[0]
        part_id = con.execute(
            "INSERT INTO parts(code,name,stock_qty,unit_price) VALUES('P-1','Фара',10,2500)"
        ).lastrowid
        con.execute(
            "UPDATE repair_orders SET responsible_master_id=?,labor_cost=1000,"
            "parts_cost=2500,external_cost=300,other_cost=200,total_cost=4000 WHERE id=?",
            (master_id, order["id"]),
        )
        con.execute(
            "INSERT INTO repair_operations(order_id,name,status,actual_hours,price,result) "
            "VALUES(?,?,?,?,?,?)",
            (order["id"], "Замена фары", "выполнена", 2, 1000, "Фара заменена"),
        )
        con.execute(
            "INSERT INTO repair_order_workers(order_id,worker_id,role,status,actual_hours,hourly_rate) "
            "VALUES(?,?,?,?,?,?)",
            (order["id"], master_id, "мастер", "завершен", 1, 1000),
        )
        con.execute(
            "INSERT INTO repair_parts(order_id,part_id,requested_qty,issued_qty,"
            "installed_qty,unit_price,status) VALUES(?,?,?,?,?,?,?)",
            (order["id"], part_id, 1, 1, 1, 2500, "установлено"),
        )
        con.commit()
    finally:
        con.close()

    card = client.get(f"/api/repairs/vehicles/{bus_id}/card")
    assert card.status_code == 200, card.text
    assert card.json()["vehicle"]["garage_number"] == "Р-101"
    assert card.json()["totals"]["cost"] == 4000
    assert card.json()["totals"]["open_damages"] == 0
    assert "cover" in card.json()

    repairs = client.get(
        f"/api/repairs/vehicles/{bus_id}/card/repairs"
    ).json()["items"]
    assert repairs[0]["responsible_master_name"] == "Администратор системы"

    operations = client.get(
        f"/api/repairs/vehicles/{bus_id}/card/operations"
    ).json()["items"]
    assert operations[0]["name"] == "Замена фары"
    assert operations[0]["result"] == "Фара заменена"

    costs = client.get(f"/api/repairs/vehicles/{bus_id}/card/costs").json()
    assert costs["totals"] == {
        "labor": 1000,
        "parts": 2500,
        "external": 300,
        "other": 200,
        "total": 4000,
    }

    parts = client.get(f"/api/repairs/vehicles/{bus_id}/card/parts").json()["items"]
    assert parts[0]["name"] == "Фара"
    assert parts[0]["line_cost"] == 2500

    workers = client.get(
        f"/api/repairs/vehicles/{bus_id}/card/workers"
    ).json()["items"]
    assert workers[0]["role"] == "мастер"
    assert workers[0]["labor_cost"] == 1000

    maintenance = client.get(
        f"/api/repairs/vehicles/{bus_id}/card/maintenance"
    )
    assert maintenance.status_code == 200, maintenance.text
    assert "plans" in maintenance.json()
    assert "events" in maintenance.json()

    timeline = client.get(f"/api/repairs/vehicles/{bus_id}/card/timeline")
    assert timeline.status_code == 200, timeline.text
    assert timeline.json()["items"][0]["event_type"] == "ремонт"


def test_vehicle_card_returns_404_for_unknown_bus(tmp_path):
    client, _ = make_client(tmp_path)
    assert client.get("/api/repairs/vehicles/999999/card").status_code == 404
