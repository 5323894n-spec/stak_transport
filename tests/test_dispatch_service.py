# -*- coding: utf-8 -*-
import pytest


def _open_db(tmp_path):
    from app import db
    db.DB_PATH = str(tmp_path / "dispatch.db")
    db.init_db()
    return db.connect()


def test_dispatch_tables_and_permission_and_setting(tmp_path):
    con = _open_db(tmp_path)
    try:
        tables = {
            r["name"]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        setting = con.execute(
            "SELECT value FROM settings WHERE key='dispatch_tolerance_min'"
        ).fetchone()
    finally:
        con.close()
    assert {"dispatch_days", "dispatch_outputs", "dispatch_trip_facts"} <= tables
    assert setting is not None
    from app.auth import WRITE_ACCESS
    assert "dispatch" in WRITE_ACCESS["диспетчер"]
    assert "dispatch" in WRITE_ACCESS["эксплуатация"]


from app import dispatch_service as ds
from app.api_planning import sched_day_type


def _seed_order(con, date="2026-08-09"):
    d = con.execute("INSERT INTO drivers(tab_number,fio) VALUES(?,?)", ("Т1", "Иванов")).lastrowid
    b = con.execute("INSERT INTO buses(garage_number,plate) VALUES(?,?)", ("Г1", "A1")).lastrowid
    r = con.execute("INSERT INTO routes(number,name) VALUES(?,?)", ("7", "Центр")).lastrowid
    oid = con.execute("INSERT INTO orders(date,status) VALUES(?, 'утверждён')", (date,)).lastrowid
    line = con.execute(
        "INSERT INTO order_lines(order_id,route_id,output_number,shift_number,driver_id,bus_id,depart_depot,start_line) "
        "VALUES(?,?,?,?,?,?,?,?)", (oid, r, 1, 1, d, b, "05:50", "06:00")).lastrowid
    con.commit()
    return date, line, r, b


def test_build_board_from_approved_order(tmp_path):
    con = _open_db(tmp_path)
    try:
        date, line, *_ = _seed_order(con)
        board = ds.build_board(con, date)
        con.commit()
        assert board["has_order"] and board["order_approved"]
        assert board["source_mode"] == "manual"
        row = board["rows"][0]
        assert row["order_line_id"] == line
        assert row["plan_release"] == "05:50"
        assert row["status"] == "план"
    finally:
        con.close()


def test_release_sets_deviation_and_status(tmp_path):
    con = _open_db(tmp_path)
    try:
        date, line, *_ = _seed_order(con)
        board = ds.build_board(con, date)
        output_id = board["rows"][0]["output_id"]
        updated = ds.set_output_status(con, output_id, "выпущен", at="05:54", user="disp")
        con.commit()
        assert updated["status"] == "выпущен"
        assert updated["actual_release"] == "05:54"
        assert updated["deviation_min"] == 4
    finally:
        con.close()


def test_disruption_requires_reason(tmp_path):
    con = _open_db(tmp_path)
    try:
        date, line, *_ = _seed_order(con)
        output_id = ds.build_board(con, date)["rows"][0]["output_id"]
        with pytest.raises(ds.DispatchError):
            ds.set_output_status(con, output_id, "срыв", user="disp")
        ok = ds.set_output_status(con, output_id, "срыв", reason="ДТП", user="disp")
        con.commit()
        assert ok["status"] == "срыв" and ok["reason"] == "ДТП"
    finally:
        con.close()


def test_empty_board_without_approved_order(tmp_path):
    con = _open_db(tmp_path)
    try:
        board = ds.build_board(con, "2026-08-09")
        assert board["has_order"] is False and board["rows"] == []
    finally:
        con.close()


def _seed_trips(con, route_id, day_type):
    con.executemany(
        "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,trip_number,dep_time,arr_time) "
        "VALUES(?,?,?,?,?,?,?)",
        [(route_id, day_type, 1, 1, 1, "06:00", "06:30"),
         (route_id, day_type, 1, 1, 2, "07:00", "07:30")])
    con.commit()


def test_trip_facts_plan_and_on_time(tmp_path):
    con = _open_db(tmp_path)
    try:
        date, line, route_id, _ = _seed_order(con)
        _seed_trips(con, route_id, sched_day_type(con, date))
        ds.build_board(con, date)
        facts = ds.list_trip_facts(con, date, line)
        assert [f["trip_number"] for f in facts] == [1, 2]
        assert facts[0]["plan_dep"] == "06:00"
        saved = ds.set_trip_fact(con, line, 1, "06:01", date=date, user="disp")
        con.commit()
        assert saved["deviation_min"] == 1 and saved["on_time"] == 1
        late = ds.set_trip_fact(con, line, 2, "07:05", date=date, user="disp")
        con.commit()
        assert late["deviation_min"] == 5 and late["on_time"] == 0
        summary = ds.day_summary(con, date)
        assert summary["trip_regularity"] == 50.0
    finally:
        con.close()
