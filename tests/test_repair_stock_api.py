# -*- coding: utf-8 -*-
from tests.test_repair_operations_api import create_order
from tests.test_repair_requests_api import make_client


def seed_part(stock=5, price=1200):
    import app.db as db
    con = db.connect()
    try:
        warehouse_id = con.execute("SELECT id FROM warehouses WHERE code='MAIN'").fetchone()[0]
        part_id = con.execute(
            "INSERT INTO parts(code,name,warehouse_id,stock_qty,unit_price) VALUES(?,?,?,?,?)",
            ("P-001", "Фильтр масляный", warehouse_id, stock, price),
        ).lastrowid
        con.commit()
        return part_id
    finally:
        con.close()


def test_issue_and_install_part_recalculates_stock_and_cost(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    part_id = seed_part()
    requested = client.post(f"/api/repairs/orders/{order['id']}/parts", json={
        "part_id": part_id, "quantity": 2,
    })
    assert requested.status_code == 201, requested.text
    repair_part_id = requested.json()["id"]
    issued = client.post(f"/api/repairs/parts/{repair_part_id}/issue", json={"quantity": 2})
    assert issued.status_code == 200, issued.text
    assert issued.json()["stock_qty"] == 3
    installed = client.post(f"/api/repairs/parts/{repair_part_id}/install", json={"quantity": 2})
    assert installed.status_code == 200, installed.text
    detail = client.get(f"/api/repairs/orders/{order['id']}/parts").json()
    assert detail["order"]["parts_cost"] == 2400
    assert detail["items"][0]["installed_qty"] == 2


def test_insufficient_stock_does_not_change_balance(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    part_id = seed_part(stock=1)
    repair_part = client.post(f"/api/repairs/orders/{order['id']}/parts", json={
        "part_id": part_id, "quantity": 2,
    }).json()
    response = client.post(f"/api/repairs/parts/{repair_part['id']}/issue", json={"quantity": 2})
    assert response.status_code == 409
    assert client.get(f"/api/repairs/orders/{order['id']}/parts").json()["items"][0]["stock_qty"] == 1
