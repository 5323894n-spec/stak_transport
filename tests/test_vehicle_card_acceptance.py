# -*- coding: utf-8 -*-
import io

from openpyxl import load_workbook

from tests.test_repair_requests_api import make_client
from tests.test_vehicle_incidents_api import incident_payload


def _create_user(username, role):
    import app.db as db
    from app.auth import hash_password

    con = db.connect()
    try:
        con.execute(
            "INSERT INTO users(username,password_hash,full_name,role,active) "
            "VALUES(?,?,?,?,1)",
            (username, hash_password("secret"), username.title(), role),
        )
        con.commit()
    finally:
        con.close()


def _token(client, username):
    response = client.post(
        "/api/login", json={"username": username, "password": "secret"}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_vehicle_card_permissions_and_report_access(tmp_path):
    client, bus_id = make_client(tmp_path)
    _create_user("personnel-card", "кадры")
    _create_user("accountant-card", "бухгалтер")

    denied = client.post(
        f"/api/repairs/vehicles/{bus_id}/incidents",
        headers={"Authorization": "Bearer " + _token(client, "personnel-card")},
        json=incident_payload(),
    )
    assert denied.status_code == 403

    accountant = {"Authorization": "Bearer " + _token(client, "accountant-card")}
    assert client.get(
        f"/api/repairs/vehicles/{bus_id}/print", headers=accountant
    ).status_code == 200
    assert client.get(
        f"/api/repairs/vehicles/{bus_id}/export.xlsx", headers=accountant
    ).status_code == 200


def test_vehicle_dossier_end_to_end_and_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("ATP_REPAIR_UPLOADS", str(tmp_path / "uploads"))
    client, bus_id = make_client(tmp_path)
    payload = incident_payload()
    payload["create_repair_request"] = False
    incident = client.post(
        f"/api/repairs/vehicles/{bus_id}/incidents", json=payload
    )
    assert incident.status_code == 201, incident.text
    incident_id = incident.json()["id"]

    uploaded = client.post(
        f"/api/repairs/vehicles/{bus_id}/media",
        files={"file": ("damage.jpg", b"\xff\xd8\xff damage", "image/jpeg")},
        data={
            "category": "ДТП",
            "caption": "Повреждение до ремонта",
            "incident_id": str(incident_id),
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    media_id = uploaded.json()["id"]
    assert client.post(f"/api/repairs/media/{media_id}/cover").status_code == 200
    assert client.post(
        f"/api/repairs/media/{media_id}/cancel",
        json={"reason": "Загружен повторно"},
    ).status_code == 200
    assert client.post(
        f"/api/repairs/incidents/{incident_id}/cancel",
        json={"reason": "Ошибочная регистрация"},
    ).status_code == 200

    exported = client.get(f"/api/repairs/vehicles/{bus_id}/export.xlsx")
    assert exported.status_code == 200, exported.text
    workbook = load_workbook(io.BytesIO(exported.content), data_only=True)
    assert workbook["Паспорт"]["A1"].value == "ТЕХНИЧЕСКОЕ ДОСЬЕ АВТОБУСА"

    import app.db as db

    con = db.connect()
    try:
        actions = {
            row[0]
            for row in con.execute(
                "SELECT action FROM audit_log WHERE object_id IN (?,?,?)",
                (bus_id, incident_id, media_id),
            )
        }
    finally:
        con.close()
    assert {
        "регистрация события автобуса",
        "отмена события автобуса",
        "добавление файла карточки автобуса",
        "выбор обложки автобуса",
        "отмена файла карточки автобуса",
        "экспорт технического досье автобуса",
    } <= actions
