# -*- coding: utf-8 -*-
import io
import json

from fastapi.testclient import TestClient
from openpyxl import Workbook


ROUTE_TITLE = 'Маршрут № 1 "Железнодорожный вокзал - Ореховая улица"'


def make_client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "atp-erm-test.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
    finally:
        con.close()

    client = TestClient(app)
    token = client.post("/api/login", json={"username": "admin", "password": "admin"}).json()["token"]
    client.headers.update({"Authorization": "Bearer " + token})
    return client


def _write_header(ws, row):
    headers = {
        2: "п.п.",
        3: "ID",
        6: "остановочный пункт",
        7: "наименования улиц",
        8: "широта",
        9: "долгота",
        10: "расст.      программн",
        11: "общ       программн",
        12: "время движения между ОП",
        13: "время движения нарастающ",
        14: "несоот",
    }
    for col, value in headers.items():
        ws.cell(row=row, column=col, value=value)


def _write_stop(ws, row, seq, stop_id, line_name, direction, stop, street, distance_m, cumulative_m, travel, cumulative):
    values = {
        2: seq,
        3: stop_id,
        4: line_name,
        5: direction,
        6: stop,
        7: street,
        8: 56.8 + seq / 1000,
        9: 35.9 + seq / 1000,
        10: distance_m,
        11: cumulative_m,
        12: travel,
        13: cumulative,
        14: 0,
        15: distance_m / 1000 if distance_m is not None else None,
        16: cumulative_m / 1000 if cumulative_m is not None else None,
    }
    for col, value in values.items():
        ws.cell(row=row, column=col, value=value)


def build_erm_workbook():
    wb = Workbook()
    wb.remove(wb.active)

    ws = wb.create_sheet("параметры")
    ws.cell(row=2, column=6, value=ROUTE_TITLE)
    ws.cell(row=2, column=8, value="прямое направление")
    _write_header(ws, 3)
    _write_stop(ws, 4, 1, 100, "А001", "на Ореховую", "Железнодорожный вокзал", "Привокзальная площадь", 0, 0, "00:00:00", "00:00:00")
    _write_stop(ws, 5, 2, 101, "А001", "на Ореховую", "Автовокзал", "Улица Коминтерна", 500, 500, "00:03:00", "00:03:00")
    _write_stop(ws, 6, 3, 102, "А001", "на Ореховую", "Ореховая улица", "Ореховая улица", 1000, 1500, "00:07:00", "00:10:00")
    ws.cell(row=8, column=8, value="обратное направление")
    _write_header(ws, 9)
    _write_stop(ws, 10, 1, 200, "А001", "Ореховая-Вокзал", "Ореховая улица", "Ореховая улица", 0, 0, "00:00:00", "00:00:00")
    _write_stop(ws, 11, 2, 201, "А001", "Ореховая-Вокзал", "Железнодорожный вокзал", "Привокзальная площадь", 1300, 1300, "00:09:00", "00:09:00")

    ws = wb.create_sheet("из парка")
    ws.cell(row=2, column=6, value=ROUTE_TITLE)
    ws.cell(row=2, column=7, value="рейсы из парка")
    _write_header(ws, 3)
    _write_stop(ws, 4, 1, 300, "А001", "из парка", "Автопарк", "Гаражная улица", 0, 0, "00:00:00", "00:00:00")
    _write_stop(ws, 5, 2, 301, "А001", "из парка", "Железнодорожный вокзал", "Привокзальная площадь", 700, 700, "00:05:00", "00:05:00")

    ws = wb.create_sheet("в парк")
    ws.cell(row=2, column=6, value=ROUTE_TITLE)
    ws.cell(row=2, column=7, value="рейсы в парк")
    _write_header(ws, 3)
    _write_stop(ws, 4, 1, 400, "А001", "в парк", "Ореховая улица", "Ореховая улица", 0, 0, "00:00:00", "00:00:00")
    _write_stop(ws, 5, 2, 401, "А001", "в парк", "Автопарк", "Гаражная улица", 800, 800, "00:06:00", "00:06:00")

    data = io.BytesIO()
    wb.save(data)
    return data.getvalue()


def test_parse_erm_route_workbook_extracts_route_and_sections():
    from app.erm_import import parse_erm_route_workbook

    parsed = parse_erm_route_workbook(build_erm_workbook())

    assert parsed["number"] == "1"
    assert parsed["name"] == "Железнодорожный вокзал - Ореховая улица"
    assert parsed["start_point"] == "Железнодорожный вокзал"
    assert parsed["end_point"] == "Ореховая улица"
    assert parsed["stops"] == "Железнодорожный вокзал, Автовокзал, Ореховая улица"
    assert parsed["stops_back"] == "Ореховая улица, Железнодорожный вокзал"
    assert parsed["length_km"] == 1.5
    assert parsed["length_back_km"] == 1.3
    assert parsed["trip_time_min"] == 10
    assert parsed["trip_time_back_min"] == 9
    assert parsed["details"]["summary"]["route_stops_forward"] == 3
    assert parsed["details"]["summary"]["route_stops_backward"] == 2
    assert parsed["details"]["summary"]["depot_sections"] == 2


def test_erm_route_import_creates_route_and_preserves_details(tmp_path):
    client = make_client(tmp_path)

    response = client.post(
        "/api/import/routes/erm",
        files={"file": ("erm.xlsx", build_erm_workbook(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["created"] is True
    assert data["updated"] is False
    assert data["route"]["number"] == "1"
    assert data["summary"]["depot_sections"] == 2

    routes = client.get("/api/refs/routes").json()["items"]
    route = next(r for r in routes if r["number"] == "1")
    assert route["length_km"] == 1.5
    assert route["trip_time_min"] == 10
    notes = json.loads(route["notes"])
    assert notes["source"] == "ЭРМ"
    assert notes["details"]["sheets"]["из парка"]["sections"][0]["stops"][1]["stop_name"] == "Железнодорожный вокзал"


def test_erm_route_import_updates_existing_route_by_number(tmp_path):
    client = make_client(tmp_path)
    create = client.post("/api/refs/routes", json={
        "number": "1",
        "name": "Старое имя",
        "version": 3,
        "notes": "ручная заметка",
    })
    assert create.status_code == 200, create.text

    response = client.post(
        "/api/import/routes/erm",
        files={"file": ("erm.xlsx", build_erm_workbook(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["created"] is False
    assert data["updated"] is True

    route = next(r for r in client.get("/api/refs/routes").json()["items"] if r["number"] == "1")
    assert route["name"] == "Железнодорожный вокзал - Ореховая улица"
    assert route["version"] == 4
    notes = json.loads(route["notes"])
    assert notes["previous_notes"] == "ручная заметка"
