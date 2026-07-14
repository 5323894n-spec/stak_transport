# -*- coding: utf-8 -*-


def make_db(tmp_path):
    import app.db as db

    db.DB_PATH = str(tmp_path / "route-migration.db")
    db.init_db()
    return db, db.connect()


def create_route(con, number, stops, stops_back=""):
    cur = con.execute(
        "INSERT INTO routes(number,name,stops,stops_back,length_km,length_back_km) "
        "VALUES(?,?,?,?,?,?)",
        (number, f"Маршрут {number}", stops, stops_back, 12.5, 11.8),
    )
    con.commit()
    return cur.lastrowid


def test_migrate_legacy_route_is_repeat_safe(tmp_path):
    db, con = make_db(tmp_path)
    try:
        route_id = create_route(
            con,
            "12",
            "Вокзал, Автовокзал, Площадь",
            "Площадь, Вокзал",
        )
        from app.route_migration import migrate_route

        first = migrate_route(con, route_id)
        second = migrate_route(con, route_id)

        assert first["status"] == "migrated"
        assert first["created_stops"] == 3
        assert second["status"] == "unchanged"
        count = con.execute(
            "SELECT COUNT(*) FROM route_stops WHERE route_id=?", (route_id,)
        ).fetchone()[0]
        assert count == 5
        assert con.execute("SELECT COUNT(*) FROM stops").fetchone()[0] == 3
        route = db.one(con.execute("SELECT * FROM routes WHERE id=?", (route_id,)))
        assert route["length_km"] == 12.5
        assert route["length_back_km"] == 11.8
    finally:
        con.close()


def test_migration_does_not_merge_ambiguous_same_name_stops(tmp_path):
    _db, con = make_db(tmp_path)
    try:
        con.execute(
            "INSERT INTO stops(name,latitude,longitude,source) VALUES(?,?,?,?)",
            ("Центр", 56.850, 35.900, "manual"),
        )
        con.execute(
            "INSERT INTO stops(name,latitude,longitude,source) VALUES(?,?,?,?)",
            ("Центр", 56.880, 35.950, "manual"),
        )
        route_id = create_route(con, "13", "Центр")
        from app.route_migration import migrate_route

        result = migrate_route(con, route_id)

        assert result["status"] == "needs_review"
        assert result["ambiguous"][0]["name"] == "Центр"
        assert len(result["ambiguous"][0]["candidate_ids"]) == 2
        assert con.execute(
            "SELECT COUNT(*) FROM route_stops WHERE route_id=?", (route_id,)
        ).fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM stops").fetchone()[0] == 2
    finally:
        con.close()


def test_migration_reports_missing_route(tmp_path):
    _db, con = make_db(tmp_path)
    try:
        from app.route_migration import migrate_route

        try:
            migrate_route(con, 999)
        except ValueError as exc:
            assert str(exc) == "Маршрут не найден"
        else:
            raise AssertionError("Ожидалась ошибка отсутствующего маршрута")
    finally:
        con.close()
