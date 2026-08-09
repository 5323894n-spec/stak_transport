# -*- coding: utf-8 -*-
from tests.test_route_schedule_document import _client


def _prepared(tmp_path, date="2026-08-09"):
    import app.db as db
    from app.api_planning import sched_day_type
    client = _client(tmp_path)
    con = db.connect()
    try:
        d = con.execute("INSERT INTO drivers(tab_number,fio) VALUES(?,?)", ("Т1", "Иванов")).lastrowid
        b = con.execute("INSERT INTO buses(garage_number,plate) VALUES(?,?)", ("Г1", "A1")).lastrowid
        r = con.execute("INSERT INTO routes(number,name) VALUES(?,?)", ("7", "Центр")).lastrowid
        oid = con.execute("INSERT INTO orders(date,status) VALUES(?, 'утверждён')", (date,)).lastrowid
        line = con.execute(
            "INSERT INTO order_lines(order_id,route_id,output_number,shift_number,driver_id,bus_id,depart_depot,start_line) "
            "VALUES(?,?,?,?,?,?,?,?)", (oid, r, 1, 1, d, b, "05:50", "06:00")).lastrowid
        day_type = sched_day_type(con, date)
        con.executemany(
            "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,trip_number,dep_time,arr_time) "
            "VALUES(?,?,?,?,?,?,?)",
            [(r, day_type, 1, 1, 1, "06:00", "06:30"), (r, day_type, 1, 1, 2, "07:00", "07:30")])
        con.commit()
    finally:
        con.close()
    return client, date, line


def test_board_status_and_adherence_flow(tmp_path):
    client, date, line = _prepared(tmp_path)
    board = client.get("/api/dispatch/board", params={"date": date}).json()
    assert board["has_order"] and board["rows"]
    output_id = board["rows"][0]["output_id"]
    r = client.post(f"/api/dispatch/outputs/{output_id}/status", json={"status": "выпущен", "at": "05:55"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "выпущен"
    r = client.put(f"/api/dispatch/trips/{line}/1", json={"actual_dep": "06:00"})
    assert r.status_code == 200 and r.json()["on_time"] == 1
    summary = client.get("/api/dispatch/summary", params={"date": date}).json()
    assert summary["released"] == 1


def test_disruption_without_reason_is_400(tmp_path):
    client, date, line = _prepared(tmp_path)
    board = client.get("/api/dispatch/board", params={"date": date}).json()
    output_id = board["rows"][0]["output_id"]
    r = client.post(f"/api/dispatch/outputs/{output_id}/status", json={"status": "срыв"})
    assert r.status_code == 400


def test_source_mode_and_telemetry(tmp_path):
    client, date, line = _prepared(tmp_path)
    assert client.put("/api/dispatch/source-mode", json={"date": date, "mode": "gps"}).status_code == 200
    r = client.post("/api/dispatch/telemetry", json={"date": date, "garage_number": "Г1", "event": "release", "time": "06:00"})
    assert r.status_code == 200, r.text
    client.put("/api/dispatch/source-mode", json={"date": date, "mode": "manual"})
    r = client.post("/api/dispatch/telemetry", json={"date": date, "garage_number": "Г1", "event": "release"})
    assert r.status_code == 409


def test_dispatch_requires_authentication(tmp_path):
    client, date, line = _prepared(tmp_path)
    client.headers.pop("Authorization", None)
    assert client.get("/api/dispatch/board", params={"date": date}).status_code in (401, 403)
