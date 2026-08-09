# -*- coding: utf-8 -*-


def _open_db(tmp_path):
    from app import db
    db.DB_PATH = str(tmp_path / "dispatch-report.db")
    db.init_db()
    return db.connect()


def _seed(con, date="2026-08-09"):
    from app import dispatch_service as ds
    from app.api_planning import sched_day_type
    d = con.execute("INSERT INTO drivers(tab_number,fio) VALUES(?,?)", ("Т1", "Иванов")).lastrowid
    b = con.execute("INSERT INTO buses(garage_number,plate) VALUES(?,?)", ("Г1", "A1")).lastrowid
    r = con.execute("INSERT INTO routes(number,name) VALUES(?,?)", ("7", "Центр")).lastrowid
    oid = con.execute("INSERT INTO orders(date,status) VALUES(?, 'утвержден')", (date,)).lastrowid
    line = con.execute(
        "INSERT INTO order_lines(order_id,route_id,output_number,shift_number,driver_id,bus_id,depart_depot,start_line) "
        "VALUES(?,?,?,?,?,?,?,?)", (oid, r, 1, 1, d, b, "05:50", "06:00")).lastrowid
    con.executemany(
        "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,trip_number,dep_time,arr_time) VALUES(?,?,?,?,?,?,?)",
        [(r, sched_day_type(con, date), 1, 1, 1, "06:00", "06:30")])
    con.commit()
    board = ds.build_board(con, date)
    output_id = board["rows"][0]["output_id"]
    ds.set_output_status(con, output_id, "выпущен", at="05:55", user="disp")
    ds.set_trip_fact(con, line, 1, "06:02", date=date, user="disp")
    con.commit()
    return date


def test_dispatch_report_has_two_sheets_and_content(tmp_path):
    from app.dispatch_reports import build_dispatch_report, dispatch_report_filename
    con = _open_db(tmp_path)
    try:
        date = _seed(con)
        wb = build_dispatch_report(con, date)
    finally:
        con.close()
    assert wb.sheetnames == ["Выпуск", "Регулярность"]
    release_values = [c.value for row in wb["Выпуск"].iter_rows() for c in row]
    assert "Иванов" in release_values and "выпущен" in release_values
    adherence_values = [c.value for row in wb["Регулярность"].iter_rows() for c in row]
    assert "06:00" in adherence_values and 2 in adherence_values  # plan dep + deviation
    assert dispatch_report_filename(date) == f"Диспетчер_{date}.xlsx"
