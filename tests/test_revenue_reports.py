# -*- coding: utf-8 -*-


def _open_db(tmp_path):
    from app import db
    db.DB_PATH = str(tmp_path / "revenue-report.db")
    db.init_db()
    return db.connect()


def _seed_sheet(con, route_number, amount, date="2026-08-07"):
    from app import revenue_service as rs
    driver_id = con.execute(
        "INSERT INTO drivers(tab_number, fio) VALUES(?,?)", ("Т", "И")
    ).lastrowid
    bus_id = con.execute(
        "INSERT INTO buses(garage_number, plate) VALUES(?,?)", ("Г", "P")
    ).lastrowid
    route_id = con.execute(
        "INSERT INTO routes(number, name) VALUES(?,?)", (route_number, "R")
    ).lastrowid
    num = con.execute("SELECT COALESCE(MAX(number),0)+1 n FROM waybills").fetchone()["n"]
    con.execute(
        "INSERT INTO waybills(number, date, driver_id, bus_id, route_id, status) "
        "VALUES(?,?,?,?,?,?)",
        (num, date, driver_id, bus_id, route_id, "оформлен"),
    )
    wid = con.execute("SELECT id FROM waybills WHERE number=?", (num,)).fetchone()["id"]
    ft = rs.upsert_fare_type(con, code=f"c{route_number}", name="Разовый", unit="поездка")
    rs.add_tariff(con, fare_type_id=ft, valid_from="2026-01-01", price=amount)
    sid = rs.create_sheet_from_waybill(con, wid, created_by="admin")
    rs.set_sheet_lines(con, sid, [(ft, 1)])
    rs.submit_sheet(con, sid, amount, user="admin")
    con.commit()
    return route_id


def test_report_groups_by_route(tmp_path):
    from app.revenue_reports import build_revenue_report
    con = _open_db(tmp_path)
    try:
        _seed_sheet(con, "10", 100.0)
        _seed_sheet(con, "20", 250.0)
        wb = build_revenue_report(
            con, date_from="2026-08-01", date_to="2026-08-31", group_by="route"
        )
    finally:
        con.close()
    sheet = wb.active
    values = [c.value for row in sheet.iter_rows() for c in row]
    assert any(v == "ВЫРУЧКА ПО МАРШРУТАМ" for v in values)
    assert 100.0 in values and 250.0 in values


def test_report_excludes_cancelled(tmp_path):
    from app import revenue_service as rs
    from app.revenue_reports import build_revenue_report
    con = _open_db(tmp_path)
    try:
        route_id = _seed_sheet(con, "30", 500.0)
        sheet_id = con.execute(
            "SELECT id FROM revenue_sheets WHERE route_id=?", (route_id,)
        ).fetchone()["id"]
        rs.cancel_sheet(con, sheet_id, "ошибка", user="admin")
        con.commit()
        wb = build_revenue_report(
            con, date_from="2026-08-01", date_to="2026-08-31", group_by="route"
        )
    finally:
        con.close()
    values = [c.value for row in wb.active.iter_rows() for c in row]
    assert 500.0 not in values
