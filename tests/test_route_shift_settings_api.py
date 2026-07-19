# -*- coding: utf-8 -*-

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "shift-settings-api.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
    finally:
        con.close()
    result = TestClient(app)
    token = result.post(
        "/api/login", json={"username": "admin", "password": "admin"}
    ).json()["token"]
    result.headers.update({"Authorization": "Bearer " + token})
    return result


@pytest.fixture
def route_id(client):
    import app.db as db

    con = db.connect()
    try:
        value = con.execute(
            "INSERT INTO routes(number,name) VALUES(?,?)",
            ("S4", "Shift settings route"),
        ).lastrowid
        con.commit()
        return value
    finally:
        con.close()


def test_shift_type_list_and_save_are_validated_and_audited(client):
    import app.db as db

    initial = client.get("/api/shift-types")
    assert initial.status_code == 200, initial.text
    assert {item["code"] for item in initial.json()["items"]} == {
        "single_8h",
        "single_12h",
        "split",
        "two_driver_long",
    }

    response = client.post(
        "/api/shift-types",
        json={
            "code": "evening_6h",
            "name": "Вечерняя 6 ч",
            "work_pattern": "single",
            "planned_duration_min": 360,
            "max_duration_min": 420,
            "driver_slots": 1,
            "allow_split": False,
            "color": "#334155",
            "active": True,
        },
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["code"] == "evening_6h"
    assert saved["planned_duration_min"] == 360

    duplicate = client.post(
        "/api/shift-types",
        json={**saved, "id": None, "name": "Дубликат"},
    )
    assert duplicate.status_code == 400
    assert "код" in duplicate.json()["detail"].lower()

    invalid = client.post(
        "/api/shift-types",
        json={
            "code": "invalid",
            "name": "Некорректная",
            "planned_duration_min": 500,
            "max_duration_min": 400,
            "driver_slots": 3,
        },
    )
    assert invalid.status_code == 400

    con = db.connect()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM audit_log WHERE object_type='shift_types'"
        ).fetchone()[0] == 1
    finally:
        con.close()


def test_route_shift_settings_have_defaults_and_can_be_saved(client, route_id):
    import app.db as db

    response = client.get(f"/api/routes/{route_id}/shift-settings/будни")
    assert response.status_code == 200, response.text
    defaults = response.json()
    assert defaults["default_shift_type"]["code"] == "single_8h"
    assert defaults["long_shift_type"]["code"] == "two_driver_long"
    assert defaults["handover_min"] == 10
    assert defaults["persisted"] is False

    types = {
        item["code"]: item for item in client.get("/api/shift-types").json()["items"]
    }
    saved = client.put(
        f"/api/routes/{route_id}/shift-settings/будни",
        json={
            "default_shift_type_id": types["single_12h"]["id"],
            "long_shift_type_id": types["two_driver_long"]["id"],
            "handover_min": 15,
            "long_run_threshold_min": 780,
            "auto_split": False,
        },
    )
    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload["default_shift_type"]["code"] == "single_12h"
    assert payload["handover_min"] == 15
    assert payload["auto_split"] is False
    assert payload["persisted"] is True

    con = db.connect()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM route_shift_settings WHERE route_id=?",
            (route_id,),
        ).fetchone()[0] == 1
        assert con.execute(
            "SELECT COUNT(*) FROM audit_log WHERE object_type='route_shift_settings'"
        ).fetchone()[0] == 1
    finally:
        con.close()


def test_settings_reject_missing_route_and_inactive_type(client, route_id):
    import app.db as db

    assert client.get("/api/routes/999999/shift-settings/будни").status_code == 404
    con = db.connect()
    try:
        inactive_id = con.execute(
            """
            INSERT INTO shift_types(
              code,name,planned_duration_min,max_duration_min,driver_slots,
              active,created_at,updated_at
            ) VALUES(?,?,?,?,?,0,datetime('now'),datetime('now'))
            """,
            ("inactive", "Неактивная", 480, 600, 1),
        ).lastrowid
        con.commit()
    finally:
        con.close()

    response = client.put(
        f"/api/routes/{route_id}/shift-settings/будни",
        json={
            "default_shift_type_id": inactive_id,
            "handover_min": 10,
            "long_run_threshold_min": 720,
            "auto_split": True,
        },
    )
    assert response.status_code == 400
    assert "актив" in response.json()["detail"].lower()
