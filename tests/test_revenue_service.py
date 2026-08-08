# -*- coding: utf-8 -*-
import pytest


def _open_db(tmp_path):
    from app import db
    db.DB_PATH = str(tmp_path / "revenue.db")
    db.init_db()
    return db.connect()


def test_revenue_tables_and_indexes_exist(tmp_path):
    con = _open_db(tmp_path)
    try:
        tables = {
            row["name"]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indexes = {
            row["name"]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    finally:
        con.close()
    assert {"fare_types", "fare_tariffs", "revenue_sheets", "revenue_lines"} <= tables
    assert "idx_revenue_sheets_waybill" in indexes
    assert "idx_revenue_sheet_active" in indexes


def test_write_access_grants_revenue_to_accountant_and_dispatcher():
    from app.auth import WRITE_ACCESS
    assert "revenue" in WRITE_ACCESS["бухгалтер"]
    assert "revenue" in WRITE_ACCESS["диспетчер"]


from app import revenue_service as rs


def test_active_tariff_picks_version_by_date(tmp_path):
    con = _open_db(tmp_path)
    try:
        ft = rs.upsert_fare_type(con, code="single", name="Разовый", unit="поездка")
        rs.add_tariff(con, fare_type_id=ft, valid_from="2026-01-01", price=30.0)
        rs.add_tariff(con, fare_type_id=ft, valid_from="2026-06-01", price=35.0)
        con.commit()
        assert rs.active_tariff(con, ft, "2026-03-01")["price"] == 30.0
        assert rs.active_tariff(con, ft, "2026-06-01")["price"] == 35.0
        assert rs.active_tariff(con, ft, "2025-12-31") is None
    finally:
        con.close()


def test_active_tariff_respects_valid_to(tmp_path):
    con = _open_db(tmp_path)
    try:
        ft = rs.upsert_fare_type(con, code="promo", name="Акция", unit="поездка")
        rs.add_tariff(
            con, fare_type_id=ft, valid_from="2026-01-01",
            valid_to="2026-01-31", price=20.0,
        )
        con.commit()
        assert rs.active_tariff(con, ft, "2026-01-15")["price"] == 20.0
        assert rs.active_tariff(con, ft, "2026-02-01") is None
    finally:
        con.close()


def test_add_tariff_rejects_negative_price(tmp_path):
    con = _open_db(tmp_path)
    try:
        ft = rs.upsert_fare_type(con, code="x", name="X", unit="поездка")
        with pytest.raises(rs.RevenueError):
            rs.add_tariff(con, fare_type_id=ft, valid_from="2026-01-01", price=-1.0)
    finally:
        con.close()


def _seed_waybill(con, *, date="2026-08-07", number=5001):
    driver_id = con.execute(
        "INSERT INTO drivers(tab_number, fio) VALUES(?,?)", ("Т1", "Иванов")
    ).lastrowid
    bus_id = con.execute(
        "INSERT INTO buses(garage_number, plate) VALUES(?,?)", ("Г1", "A001")
    ).lastrowid
    route_id = con.execute(
        "INSERT INTO routes(number, name) VALUES(?,?)", ("42", "Центр")
    ).lastrowid
    con.execute(
        "INSERT INTO waybills(number, date, driver_id, bus_id, route_id, status) "
        "VALUES(?,?,?,?,?,?)",
        (number, date, driver_id, bus_id, route_id, "оформлен"),
    )
    wid = con.execute("SELECT id FROM waybills WHERE number=?", (number,)).fetchone()["id"]
    return wid, route_id


def _fare(con, code, price, valid_from="2026-01-01"):
    ft = rs.upsert_fare_type(con, code=code, name=code, unit="поездка")
    rs.add_tariff(con, fare_type_id=ft, valid_from=valid_from, price=price)
    return ft


def test_create_sheet_copies_waybill_fields(tmp_path):
    con = _open_db(tmp_path)
    try:
        wid, route_id = _seed_waybill(con, date="2026-08-07")
        sheet_id = rs.create_sheet_from_waybill(con, wid, created_by="admin")
        con.commit()
        sheet = rs.get_sheet(con, sheet_id)
        assert sheet["date"] == "2026-08-07"
        assert sheet["route_id"] == route_id
        assert sheet["status"] == "черновик"
        assert sheet["number"] >= 1
    finally:
        con.close()


def test_create_sheet_rejects_second_active_sheet(tmp_path):
    con = _open_db(tmp_path)
    try:
        wid, _ = _seed_waybill(con)
        rs.create_sheet_from_waybill(con, wid, created_by="admin")
        con.commit()
        with pytest.raises(rs.RevenueError):
            rs.create_sheet_from_waybill(con, wid, created_by="admin")
    finally:
        con.close()


def test_create_sheet_unknown_waybill(tmp_path):
    con = _open_db(tmp_path)
    try:
        with pytest.raises(rs.RevenueError):
            rs.create_sheet_from_waybill(con, 999999, created_by="admin")
    finally:
        con.close()


def test_set_lines_computes_amounts_from_tariff_on_date(tmp_path):
    con = _open_db(tmp_path)
    try:
        wid, _ = _seed_waybill(con, date="2026-08-07")
        single = _fare(con, "single", 30.0)
        child = _fare(con, "child", 15.0)
        sheet_id = rs.create_sheet_from_waybill(con, wid, created_by="admin")
        sheet = rs.set_sheet_lines(con, sheet_id, [(single, 100), (child, 20)])
        con.commit()
        assert sheet["expected_amount"] == 30.0 * 100 + 15.0 * 20
        amounts = {ln["fare_type_id"]: ln["amount"] for ln in sheet["lines"]}
        assert amounts[single] == 3000.0
    finally:
        con.close()


def test_set_lines_rejects_negative_and_missing_tariff(tmp_path):
    con = _open_db(tmp_path)
    try:
        wid, _ = _seed_waybill(con, date="2026-08-07")
        single = _fare(con, "single", 30.0)
        no_tariff = rs.upsert_fare_type(con, code="none", name="Нет", unit="поездка")
        sheet_id = rs.create_sheet_from_waybill(con, wid, created_by="admin")
        with pytest.raises(rs.RevenueError):
            rs.set_sheet_lines(con, sheet_id, [(single, -1)])
        with pytest.raises(rs.RevenueError):
            rs.set_sheet_lines(con, sheet_id, [(no_tariff, 5)])
    finally:
        con.close()


def _draft_with_lines(con):
    wid, _ = _seed_waybill(con, date="2026-08-07")
    single = _fare(con, "single", 30.0)
    sheet_id = rs.create_sheet_from_waybill(con, wid, created_by="admin")
    rs.set_sheet_lines(con, sheet_id, [(single, 100)])  # expected 3000
    return sheet_id


def test_submit_computes_difference_and_advances_status(tmp_path):
    con = _open_db(tmp_path)
    try:
        sheet_id = _draft_with_lines(con)
        sheet = rs.submit_sheet(con, sheet_id, 2950.0, user="cashier")
        con.commit()
        assert sheet["status"] == "сдан"
        assert sheet["submitted_amount"] == 2950.0
        assert sheet["difference"] == -50.0
    finally:
        con.close()


def test_reconcile_requires_submitted(tmp_path):
    con = _open_db(tmp_path)
    try:
        sheet_id = _draft_with_lines(con)
        with pytest.raises(rs.RevenueError):
            rs.reconcile_sheet(con, sheet_id, user="buh")
        rs.submit_sheet(con, sheet_id, 3000.0, user="cashier")
        sheet = rs.reconcile_sheet(con, sheet_id, user="buh")
        con.commit()
        assert sheet["status"] == "сверен"
    finally:
        con.close()


def test_cancel_sets_status_and_reason(tmp_path):
    con = _open_db(tmp_path)
    try:
        sheet_id = _draft_with_lines(con)
        sheet = rs.cancel_sheet(con, sheet_id, "ошибка", user="admin")
        con.commit()
        assert sheet["status"] == "аннулирован"
        assert sheet["cancel_reason"] == "ошибка"
    finally:
        con.close()


def test_lines_locked_after_submit(tmp_path):
    con = _open_db(tmp_path)
    try:
        sheet_id = _draft_with_lines(con)
        single = con.execute(
            "SELECT fare_type_id FROM revenue_lines WHERE sheet_id=?", (sheet_id,)
        ).fetchone()["fare_type_id"]
        rs.submit_sheet(con, sheet_id, 3000.0, user="cashier")
        with pytest.raises(rs.RevenueError):
            rs.set_sheet_lines(con, sheet_id, [(single, 50)])
    finally:
        con.close()


def test_cancel_frees_waybill_for_new_sheet(tmp_path):
    con = _open_db(tmp_path)
    try:
        wid, _ = _seed_waybill(con)
        first = rs.create_sheet_from_waybill(con, wid, created_by="admin")
        rs.cancel_sheet(con, first, "ошибка", user="admin")
        con.commit()
        second = rs.create_sheet_from_waybill(con, wid, created_by="admin")
        con.commit()
        assert second != first
    finally:
        con.close()
