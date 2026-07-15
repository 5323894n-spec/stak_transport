# -*- coding: utf-8 -*-
import datetime

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "period-templates.db")
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


def create_route(client, number):
    response = client.post(
        "/api/refs/routes",
        json={"number": number, "name": "Маршрут " + number,
              "trip_time_min": 40, "trip_time_back_min": 40},
    )
    assert response.status_code == 200
    return response.json()["id"]


def create_template(client):
    response = client.post(
        "/api/period-templates",
        json={
            "name": "Городской будний",
            "description": "Три режима движения",
            "items": [
                {"name": "Пик", "start": "06:00", "end": "09:00", "interval_min": 8},
                {"name": "День", "start": "09:00", "end": "22:00", "interval_min": 18},
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_template_preview_does_not_change_route_until_apply(client):
    route_id = create_route(client, "T1")
    template = create_template(client)
    preview = client.post(
        f"/api/routes/{route_id}/periods/будни/template-preview",
        json={"template_id": template["id"]},
    )
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert len(data["diff"]["new"]) == 2
    assert client.get(f"/api/routes/{route_id}/periods/будни").json()["items"] == []

    applied = client.post(
        f"/api/routes/{route_id}/periods/будни/template-apply",
        json={"preview_token": data["preview_token"]},
    )
    assert applied.status_code == 200, applied.text
    saved = client.get(f"/api/routes/{route_id}/periods/будни").json()["items"]
    assert [row["name"] for row in saved] == ["Пик", "День"]
    assert client.post(
        f"/api/routes/{route_id}/periods/будни/template-apply",
        json={"preview_token": data["preview_token"]},
    ).status_code == 409


def test_template_token_rejects_expired_wrong_route_and_wrong_user(client):
    import app.db as db

    first_route = create_route(client, "T2")
    second_route = create_route(client, "T3")
    template = create_template(client)
    token = client.post(
        f"/api/routes/{first_route}/periods/выходные/template-preview",
        json={"template_id": template["id"]},
    ).json()["preview_token"]
    assert client.post(
        f"/api/routes/{second_route}/periods/выходные/template-apply",
        json={"preview_token": token},
    ).status_code == 404

    con = db.connect()
    try:
        con.execute(
            "UPDATE period_previews SET expires_at=? WHERE token=?",
            ((datetime.datetime.now() - datetime.timedelta(minutes=1)).isoformat(), token),
        )
        con.commit()
    finally:
        con.close()
    assert client.post(
        f"/api/routes/{first_route}/periods/выходные/template-apply",
        json={"preview_token": token},
    ).status_code == 410

    fresh = client.post(
        f"/api/routes/{first_route}/periods/выходные/template-preview",
        json={"template_id": template["id"]},
    ).json()["preview_token"]
    con = db.connect()
    try:
        con.execute("UPDATE period_previews SET username='other' WHERE token=?", (fresh,))
        con.commit()
    finally:
        con.close()
    assert client.post(
        f"/api/routes/{first_route}/periods/выходные/template-apply",
        json={"preview_token": fresh},
    ).status_code == 404


def test_template_crud_updates_list_and_delete(client):
    template = create_template(client)
    listed = client.get("/api/period-templates").json()["items"]
    assert listed[0]["name"] == "Городской будний"
    updated = client.put(
        f"/api/period-templates/{template['id']}",
        json={
            "name": "Городской выходной",
            "description": "Обновлён",
            "items": [{"name": "День", "start": "07:00", "end": "23:00", "interval_min": 20}],
        },
    )
    assert updated.status_code == 200
    assert client.get("/api/period-templates").json()["items"][0]["name"] == "Городской выходной"
    assert client.delete(f"/api/period-templates/{template['id']}").status_code == 200
    assert client.get("/api/period-templates").json()["items"] == []
