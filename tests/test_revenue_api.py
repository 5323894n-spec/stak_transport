# -*- coding: utf-8 -*-
from fastapi.testclient import TestClient


def _client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app
    db.DB_PATH = str(tmp_path / "revenue-api.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
    finally:
        con.close()
    client = TestClient(app)
    token = client.post(
        "/api/login", json={"username": "admin", "password": "admin"}
    ).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _seed_waybill(number=7001, date="2026-08-07"):
    import app.db as db
    con = db.connect()
    try:
        driver_id = con.execute(
            "INSERT INTO drivers(tab_number, fio) VALUES(?,?)", ("Т9", "Петров")
        ).lastrowid
        bus_id = con.execute(
            "INSERT INTO buses(garage_number, plate) VALUES(?,?)", ("Г9", "B009")
        ).lastrowid
        route_id = con.execute(
            "INSERT INTO routes(number, name) VALUES(?,?)", ("9", "Депо")
        ).lastrowid
        con.execute(
            "INSERT INTO waybills(number, date, driver_id, bus_id, route_id, status) "
            "VALUES(?,?,?,?,?,?)",
            (number, date, driver_id, bus_id, route_id, "оформлен"),
        )
        wid = con.execute(
            "SELECT id FROM waybills WHERE number=?", (number,)
        ).fetchone()["id"]
        con.commit()
        return wid, route_id
    finally:
        con.close()


def test_revenue_flow_end_to_end(tmp_path):
    client = _client(tmp_path)
    wid, _ = _seed_waybill()
    ft = client.post(
        "/api/revenue/fare-types",
        json={"code": "single", "name": "Разовый", "unit": "поездка"},
    ).json()
    client.post(
        "/api/revenue/tariffs",
        json={"fare_type_id": ft["id"], "valid_from": "2026-01-01", "price": 30.0},
    )
    sheet = client.post("/api/revenue/sheets", json={"waybill_id": wid}).json()
    lined = client.put(
        f"/api/revenue/sheets/{sheet['id']}/lines",
        json={"lines": [{"fare_type_id": ft["id"], "tickets_count": 100}]},
    ).json()
    assert lined["expected_amount"] == 3000.0
    submitted = client.post(
        f"/api/revenue/sheets/{sheet['id']}/submit",
        json={"submitted_amount": 2980.0},
    ).json()
    assert submitted["difference"] == -20.0
    reconciled = client.post(
        f"/api/revenue/sheets/{sheet['id']}/reconcile", json={}
    ).json()
    assert reconciled["status"] == "сверен"


def test_sheet_unknown_waybill_is_404(tmp_path):
    client = _client(tmp_path)
    response = client.post("/api/revenue/sheets", json={"waybill_id": 999999})
    assert response.status_code == 404
    assert response.json()["detail"] == "Путевой лист не найден"


def test_revenue_requires_authentication(tmp_path):
    client = _client(tmp_path)
    client.headers.pop("Authorization", None)
    assert client.get("/api/revenue/fare-types").status_code in (401, 403)


def test_negative_tickets_rejected(tmp_path):
    client = _client(tmp_path)
    wid, _ = _seed_waybill(number=7002)
    ft = client.post(
        "/api/revenue/fare-types",
        json={"code": "single", "name": "Разовый", "unit": "поездка"},
    ).json()
    client.post(
        "/api/revenue/tariffs",
        json={"fare_type_id": ft["id"], "valid_from": "2026-01-01", "price": 30.0},
    )
    sheet = client.post("/api/revenue/sheets", json={"waybill_id": wid}).json()
    response = client.put(
        f"/api/revenue/sheets/{sheet['id']}/lines",
        json={"lines": [{"fare_type_id": ft["id"], "tickets_count": -5}]},
    )
    assert response.status_code == 400
