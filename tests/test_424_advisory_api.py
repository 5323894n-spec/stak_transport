# -*- coding: utf-8 -*-
import calendar
import datetime
import json

from fastapi.testclient import TestClient

WARNING = "\u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435"
DATE = "2026-07-06"
DAY_TYPE = "\u0431\u0443\u0434\u043d\u0438"


def make_client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "atp-424-advisory.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
    finally:
        con.close()

    client = TestClient(app)
    token = client.post("/api/login", json={"username": "admin", "password": "admin"}).json()["token"]
    client.headers.update({"Authorization": "Bearer " + token})
    return client


def create_driver(client, tab="4241"):
    response = client.post("/api/refs/drivers", json={
        "tab_number": tab,
        "fio": f"Driver {tab}",
        "status": "\u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442",
        "default_schedule": "2/2",
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def create_bus(client, driver_id):
    response = client.post("/api/refs/buses", json={
        "garage_number": f"BUS{driver_id}",
        "plate": "A424AA",
        "brand": "Test",
        "model": "Bus",
        "status": "\u0438\u0441\u043f\u0440\u0430\u0432\u0435\u043d",
        "assigned_driver_id": driver_id,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def create_route(client):
    response = client.post("/api/refs/routes", json={
        "number": "424",
        "name": "Depot - Center",
        "comm_type": "city",
        "start_point": "Depot",
        "end_point": "Center",
        "length_km": 12,
        "length_back_km": 12,
        "trip_time_min": 45,
        "trip_time_back_min": 45,
        "interval_min": 12,
        "outputs_count": 1,
        "bus_types": "large",
        "work_days": "daily",
        "version": 1,
        "active": 1,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def add_trip(client, route_id, **overrides):
    payload = {
        "route_id": route_id,
        "day_type": DAY_TYPE,
        "output_number": 1,
        "shift_number": 1,
        "trip_number": 1,
        "direction": "\u043f\u0440\u044f\u043c\u043e\u0435",
        "dep_time": "06:00",
        "arr_time": "06:45",
        "distance_km": 12,
        "break_after_min": 0,
        "break_type": "",
    }
    payload.update(overrides)
    response = client.post("/api/trips", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def post_roster_work(client, driver_id, date=DATE, **overrides):
    payload = {
        "driver_id": driver_id,
        "date": date,
        "status": "\u0440\u0430\u0431\u043e\u0442\u0430",
        "start_time": "06:00",
        "end_time": "23:30",
        "hours": 17.5,
        "break_min": 0,
        "comment": "",
    }
    payload.update(overrides)
    response = client.post("/api/roster/entry", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def replace_norms(client, params):
    current = client.get("/api/norms")
    assert current.status_code == 200, current.text
    base = current.json()["defaults"]
    base.update(params)
    response = client.post("/api/norms", json={
        "name": "Test 424 norms",
        "valid_from": "2026-01-01",
        "valid_to": "2026-12-31",
        "active": 1,
        "params": base,
        "doc_ref": "test",
        "comment": "test",
    })
    assert response.status_code == 200, response.text


def test_roster_424_violations_are_warnings_and_do_not_block_approval(tmp_path):
    client = make_client(tmp_path)
    driver_id = create_driver(client)
    post_roster_work(client, driver_id)

    check = client.get(f"/api/roster/check?date_from={DATE}&date_to={DATE}&driver_id={driver_id}")
    assert check.status_code == 200, check.text
    violations = check.json()["violations"]
    assert violations
    assert {v["severity"] for v in violations} == {WARNING}

    approved = client.post("/api/roster/approve", json={"date_from": DATE, "date_to": DATE})
    assert approved.status_code == 200, approved.text
    assert approved.json()["ok"] is True


def test_order_approval_does_not_block_on_424_warnings(tmp_path):
    client = make_client(tmp_path)
    driver_id = create_driver(client)
    bus_id = create_bus(client, driver_id)
    route_id = create_route(client)
    post_roster_work(client, driver_id, route_id=route_id, output_number=1, shift_number=1)

    import app.db as db

    con = db.connect()
    try:
        oid = con.execute("INSERT INTO orders(date,status) VALUES(?, 'черновик')", (DATE,)).lastrowid
        con.execute(
            """
            INSERT INTO order_lines(order_id,route_id,output_number,shift_number,driver_id,bus_id,
              report_time,depart_depot,start_line,end_line,return_depot,shift_hours,trips_count,distance_km,status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (oid, route_id, 1, 1, driver_id, bus_id, "05:30", "05:45", "06:00", "23:30", "23:45", 17.5, 1, 12, "план"),
        )
        con.commit()
    finally:
        con.close()

    approved = client.post(f"/api/orders/{oid}/status", json={"status": "утвержден"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["ok"] is True


def test_accounting_period_months_controls_overtime_warning(tmp_path):
    client = make_client(tmp_path)
    replace_norms(client, {"accounting_period_months": 3, "week_norm_hours": 0.1})
    driver_id = create_driver(client)
    post_roster_work(client, driver_id, date="2026-01-10", start_time="08:00", end_time="16:00", hours=8)

    response = client.get(f"/api/roster/check?date_from=2026-01-01&date_to=2026-03-31&driver_id={driver_id}")
    assert response.status_code == 200, response.text
    violations = response.json()["violations"]
    overtime = [v for v in violations if "\u0443\u0447\u0451\u0442\u043d" in v["type"].lower()]
    assert overtime
    assert overtime[0]["severity"] == WARNING
    assert "3" in overtime[0]["norm_value"]


def test_schedule_424_break_issues_are_warnings(tmp_path):
    client = make_client(tmp_path)
    route_id = create_route(client)
    add_trip(client, route_id, trip_number=1, dep_time="09:15", arr_time="10:00", break_after_min=40, break_type="\u043e\u0431\u0435\u0434")
    add_trip(client, route_id, trip_number=2, dep_time="10:12", arr_time="10:50", break_after_min=0)

    import app.db as db

    con = db.connect()
    try:
        con.execute("UPDATE route_trips SET dep_time='10:12', arr_time='10:50' WHERE route_id=? AND trip_number=2", (route_id,))
        con.commit()
    finally:
        con.close()

    response = client.get(f"/api/routes/{route_id}/check?day_type={DAY_TYPE}")
    assert response.status_code == 200, response.text
    problems = response.json()["problems"]
    break_gap = [p for p in problems if p["kind"] == "break_gap"]
    assert break_gap
    assert break_gap[0]["severity"] == WARNING
