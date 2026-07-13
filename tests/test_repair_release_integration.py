# -*- coding: utf-8 -*-
from tests.test_repair_requests_api import make_client


def seed_active_repair_and_order_line(bus_id):
    import app.db as db
    con = db.connect()
    try:
        driver_id = con.execute(
            "INSERT INTO drivers(tab_number,fio,status,license_number,snils) VALUES(?,?,?,?,?)",
            ("D-1", "Водитель", "работает", "6900 123456", "123-456-789 00"),
        ).lastrowid
        route_id = con.execute("INSERT INTO routes(number,name) VALUES(?,?)", ("1", "Маршрут 1")).lastrowid
        order_id = con.execute("INSERT INTO orders(date,status) VALUES(?,?)", ("2026-07-12", "утвержден")).lastrowid
        line_id = con.execute(
            "INSERT INTO order_lines(order_id,route_id,output_number,shift_number,driver_id,bus_id,status) VALUES(?,?,?,?,?,?,?)",
            (order_id, route_id, 1, 1, driver_id, bus_id, "план"),
        ).lastrowid
        repair_id = con.execute(
            "INSERT INTO repair_orders(number,bus_id,status) VALUES(?,?,?)",
            ("РМ-2026-000777", bus_id, "в работе"),
        ).lastrowid
        con.commit()
        return line_id, repair_id
    finally:
        con.close()


def test_active_repair_blocks_assignment_tech_release_and_waybill(tmp_path):
    client, bus_id = make_client(tmp_path)
    line_id, _ = seed_active_repair_and_order_line(bus_id)
    assignment = client.put(f"/api/orders/line/{line_id}", json={"bus_id": bus_id})
    assert assignment.status_code == 409
    assert "РМ-2026-000777" in assignment.text
    tech = client.post("/api/tech", json={
        "bus_id": bus_id, "date": "2026-07-12", "result": "выпуск разрешен",
    })
    assert tech.status_code == 409
    assert "РМ-2026-000777" in tech.text
    precheck = client.get(f"/api/waybills/precheck/{line_id}")
    assert precheck.status_code == 200
    assert any("РМ-2026-000777" in problem for problem in precheck.json()["problems"])
