# -*- coding: utf-8 -*-
def test_seed_creates_dispatch_state(tmp_path):
    from app import db, seed
    db.DB_PATH = str(tmp_path / "dispatch-seed.db")
    seed.run()
    con = db.connect()
    try:
        released = con.execute(
            "SELECT COUNT(*) n FROM dispatch_outputs WHERE status='выпущен'"
        ).fetchone()["n"]
        outputs = con.execute("SELECT COUNT(*) n FROM dispatch_outputs").fetchone()["n"]
    finally:
        con.close()
    assert outputs >= 1
    assert released >= 1
