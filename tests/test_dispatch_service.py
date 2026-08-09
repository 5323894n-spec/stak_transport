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
