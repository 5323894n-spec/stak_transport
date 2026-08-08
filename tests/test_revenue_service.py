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
