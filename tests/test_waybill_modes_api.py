# -*- coding: utf-8 -*-
from fastapi.testclient import TestClient


DATE = "2026-07-07"


def make_client_with_line(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "atp-waybill-modes.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
        driver = con.execute(
            "INSERT INTO drivers(tab_number,fio,license_number,license_issued,license_expires,snils,status) "
            "VALUES(?,?,?,?,?,?,?)",
            ("7001", "Waybill Mode Driver", "7700001", "2020-01-01", "2035-01-01", "123-456-789 00", "работает"),
        )
        driver_id = driver.lastrowid
        bus = con.execute(
            "INSERT INTO buses(garage_number,plate,brand,model,status,odometer,fuel_balance,assigned_driver_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("WB-1", "А001АА77", "Test", "Bus", "исправен", 1000, 50, driver_id),
        )
        bus_id = bus.lastrowid
        route = con.execute(
            "INSERT INTO routes(number,name,comm_type,transport_type,start_point,end_point,length_km,length_back_km) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("77", "Depot - Center", "городское", "Регулярные перевозки", "Depot", "Center", 10, 10),
        )
        route_id = route.lastrowid
        order = con.execute("INSERT INTO orders(date,status,approved_by) VALUES(?,?,?)", (DATE, "утвержден", "admin"))
        order_id = order.lastrowid
        line = con.execute(
            "INSERT INTO order_lines(order_id,route_id,output_number,shift_number,driver_id,bus_id,"
            "report_time,depart_depot,start_line,end_line,return_depot,shift_hours,trips_count,distance_km,planned_fuel,status) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                order_id,
                route_id,
                1,
                1,
                driver_id,
                bus_id,
                "05:30",
                "05:45",
                "06:00",
                "14:00",
                "14:15",
                8.5,
                10,
                100,
                35,
                "план",
            ),
        )
        line_id = line.lastrowid
        con.commit()
    finally:
        con.close()

    client = TestClient(app)
    token = client.post("/api/login", json={"username": "admin", "password": "admin"}).json()["token"]
    client.headers.update({"Authorization": "Bearer " + token})
    return client, {"driver_id": driver_id, "bus_id": bus_id, "route_id": route_id, "order_id": order_id, "line_id": line_id}


def set_mode(client, mode):
    response = client.post("/api/settings", json={"waybill_issue_mode": mode})
    assert response.status_code == 200, response.text


def add_medical(client, driver_id, result="допущен"):
    response = client.post(
        "/api/medical",
        json={
            "driver_id": driver_id,
            "date": DATE,
            "time": "05:15",
            "type": "предрейсовый",
            "result": result,
            "medic_name": "Test Medic",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def add_tech(client, bus_id, result="выпуск разрешен"):
    response = client.post(
        "/api/tech",
        json={
            "bus_id": bus_id,
            "date": DATE,
            "time": "05:20",
            "result": result,
            "odometer": 1000,
            "mechanic_name": "Test Mechanic",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_strict_mode_blocks_without_medical_and_tech(tmp_path):
    client, ctx = make_client_with_line(tmp_path)

    response = client.post(f"/api/waybills/from-line/{ctx['line_id']}")

    assert response.status_code == 409
    assert "Нет предрейсового медицинского осмотра" in response.text
    assert "Нет предрейсового технического контроля" in response.text


def test_medical_only_blocks_without_medical_but_warns_without_tech(tmp_path):
    client, ctx = make_client_with_line(tmp_path)
    set_mode(client, "medical_only")

    blocked = client.post(f"/api/waybills/from-line/{ctx['line_id']}")

    assert blocked.status_code == 409
    assert "Нет предрейсового медицинского осмотра" in blocked.text

    add_medical(client, ctx["driver_id"])
    precheck = client.get(f"/api/waybills/precheck/{ctx['line_id']}")
    assert precheck.status_code == 200, precheck.text
    assert precheck.json()["mode"] == "medical_only"
    assert precheck.json()["problems"] == []
    assert precheck.json()["warnings"] == ["Нет предрейсового технического контроля"]

    created = client.post(f"/api/waybills/from-line/{ctx['line_id']}")

    assert created.status_code == 200, created.text
    assert created.json()["mode"] == "medical_only"
    assert created.json()["warnings"] == ["Нет предрейсового технического контроля"]


def test_medical_only_blocks_explicit_tech_ban(tmp_path):
    client, ctx = make_client_with_line(tmp_path)
    set_mode(client, "medical_only")
    add_medical(client, ctx["driver_id"])
    add_tech(client, ctx["bus_id"], result="выпуск запрещен")

    response = client.post(f"/api/waybills/from-line/{ctx['line_id']}")

    assert response.status_code == 409
    assert "Техконтроль: выпуск запрещён" in response.text


def test_advisory_mode_creates_without_medical_and_tech_with_warnings(tmp_path):
    client, ctx = make_client_with_line(tmp_path)
    set_mode(client, "advisory")

    response = client.post(f"/api/waybills/from-line/{ctx['line_id']}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "advisory"
    assert "Нет предрейсового медицинского осмотра" in body["warnings"]
    assert "Нет предрейсового технического контроля" in body["warnings"]


def test_bulk_order_creation_returns_warnings_and_prints_all_waybills(tmp_path):
    client, _ctx = make_client_with_line(tmp_path)
    set_mode(client, "advisory")

    response = client.post(f"/api/waybills/from-order/{DATE}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["created"]) == 1
    assert body["blocked"] == []
    assert len(body["warnings"]) == 1
    assert "Нет предрейсового медицинского осмотра" in body["warnings"][0]["warnings"]
    assert "Нет предрейсового технического контроля" in body["warnings"][0]["warnings"]

    printed = client.get(f"/api/orders/waybills/print?date={DATE}")

    assert printed.status_code == 200, printed.text
    assert "Печать всех ПЛ" in printed.text
    assert "ПУТЕВОЙ ЛИСТ АВТОБУСА" in printed.text
