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
