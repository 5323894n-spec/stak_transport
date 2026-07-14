# -*- coding: utf-8 -*-
import io

from fastapi.testclient import TestClient
from openpyxl import Workbook


CSV_DATA = """direction,sequence,external_code,name,latitude,longitude,address,distance_km,run_time_sec,dwell_time_sec
forward,1,100,Вокзал,56.801,35.901,Площадь вокзала,0,0,30
forward,2,101,Автовокзал,56.802,35.902,Улица Коминтерна,1.5,240,30
forward,3,102,Площадь,56.803,35.903,Советская площадь,0.5,120,20
backward,1,102,Площадь,56.803,35.903,Советская площадь,0,0,20
backward,2,100,Вокзал,56.801,35.901,Площадь вокзала,1.8,300,30
""".encode("utf-8-sig")


def make_client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "route-import-v2.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
    finally:
        con.close()
    client = TestClient(app)
    token = client.post("/api/login", json={
        "username": "admin", "password": "admin",
    }).json()["token"]
    client.headers.update({"Authorization": "Bearer " + token})
    route = client.post("/api/refs/routes", json={
        "number": "12", "name": "Тестовый маршрут",
    })
    return client, route.json()["id"]


def xlsx_data():
    wb = Workbook()
    ws = wb.active
    ws.append([
        "direction", "sequence", "external_code", "name", "latitude", "longitude",
        "address", "distance_km", "run_time_sec", "dwell_time_sec",
    ])
    ws.append(["forward", 1, "200", "Парк", 56.81, 35.91, "Гаражная", 0, 0, 20])
    ws.append(["forward", 2, "201", "Рынок", 56.82, 35.92, "Рыночная", 2.2, 360, 30])
    data = io.BytesIO()
    wb.save(data)
    return data.getvalue()


def test_csv_preview_does_not_apply_until_confirmed(tmp_path):
    client, route_id = make_client(tmp_path)

    preview = client.post(
        f"/api/routes/{route_id}/network-import/preview",
        files={"file": ("stops.csv", CSV_DATA, "text/csv")},
    )

    assert preview.status_code == 200, preview.text
    assert preview.json()["summary"] == {
        "created_stops": 3,
        "matched_stops": 2,
        "conflicts": 0,
        "forward_stops": 3,
        "backward_stops": 2,
    }
    assert client.get(f"/api/routes/{route_id}/network").json()["forward"] == []

    applied = client.post(
        f"/api/routes/{route_id}/network-import/apply",
        json={"preview_token": preview.json()["preview_token"]},
    )

    assert applied.status_code == 200, applied.text
    network = client.get(f"/api/routes/{route_id}/network").json()
    assert len(network["forward"]) == 3
    assert network["forward"][-1]["cumulative_km"] == 2.0
    assert network["backward"][-1]["cumulative_km"] == 1.8
    route = client.get("/api/refs/routes").json()["items"][0]
    assert route["stops"] == "Вокзал, Автовокзал, Площадь"
    assert route["length_km"] == 2.0


def test_xlsx_preview_uses_same_validated_format(tmp_path):
    client, route_id = make_client(tmp_path)

    response = client.post(
        f"/api/routes/{route_id}/network-import/preview",
        files={"file": (
            "stops.xlsx", xlsx_data(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )},
    )

    assert response.status_code == 200, response.text
    assert response.json()["summary"]["forward_stops"] == 2
    assert response.json()["summary"]["created_stops"] == 2


def test_import_rejects_missing_required_columns(tmp_path):
    client, route_id = make_client(tmp_path)

    response = client.post(
        f"/api/routes/{route_id}/network-import/preview",
        files={"file": ("bad.csv", b"name,address\nStop,Street\n", "text/csv")},
    )

    assert response.status_code == 400
    assert "direction" in response.json()["detail"]
