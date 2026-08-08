# -*- coding: utf-8 -*-
def test_seed_creates_fare_types_and_a_revenue_sheet(tmp_path):
    from app import db, seed
    db.DB_PATH = str(tmp_path / "revenue-seed.db")
    seed.run()  # opens its own connection, seeds a full demo АТП, closes it
    con = db.connect()
    try:
        fare_types = con.execute("SELECT COUNT(*) n FROM fare_types").fetchone()["n"]
        sheets = con.execute("SELECT COUNT(*) n FROM revenue_sheets").fetchone()["n"]
    finally:
        con.close()
    assert fare_types >= 3
    assert sheets >= 1
