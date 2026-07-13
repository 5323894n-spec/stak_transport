# -*- coding: utf-8 -*-
from tests.test_vehicle_incidents_api import incident_payload
from tests.test_repair_requests_api import make_client


def test_vehicle_gallery_upload_metadata_cover_and_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv("ATP_REPAIR_UPLOADS", str(tmp_path / "uploads"))
    client, bus_id = make_client(tmp_path)

    uploaded = client.post(
        f"/api/repairs/vehicles/{bus_id}/media",
        files={"file": ("автобус.jpg", b"\xff\xd8\xff test", "image/jpeg")},
        data={
            "category": "общий вид",
            "caption": "Вид спереди",
            "captured_at": "2026-07-13",
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    media_id = uploaded.json()["id"]

    edited = client.patch(
        f"/api/repairs/media/{media_id}",
        json={"caption": "Вид спереди после мойки", "captured_at": "2026-07-14"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["caption"] == "Вид спереди после мойки"
    assert edited.json()["captured_at"] == "2026-07-14"

    cover = client.post(f"/api/repairs/media/{media_id}/cover")
    assert cover.status_code == 200, cover.text
    gallery = client.get(f"/api/repairs/vehicles/{bus_id}/media").json()["items"]
    assert gallery[0]["is_cover"] == 1
    assert gallery[0]["caption"] == "Вид спереди после мойки"
    assert gallery[0]["download_url"].endswith(f"/{media_id}/download")

    downloaded = client.get(f"/api/repairs/attachments/{media_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"\xff\xd8\xff")

    cancelled = client.post(
        f"/api/repairs/media/{media_id}/cancel",
        json={"reason": "Неверный ракурс"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["cancel_reason"] == "Неверный ракурс"
    assert client.get(f"/api/repairs/vehicles/{bus_id}/media").json()["items"] == []
    assert client.get(f"/api/repairs/attachments/{media_id}/download").status_code == 404


def test_incident_photo_must_belong_to_same_bus(tmp_path, monkeypatch):
    monkeypatch.setenv("ATP_REPAIR_UPLOADS", str(tmp_path / "uploads"))
    client, first_bus = make_client(tmp_path)

    import app.db as db

    con = db.connect()
    try:
        second_bus = con.execute(
            "INSERT INTO buses(garage_number,plate,odometer) VALUES(?,?,?)",
            ("Р-202", "А202АА69", 22000),
        ).lastrowid
        con.commit()
    finally:
        con.close()

    payload = incident_payload()
    payload["create_repair_request"] = False
    payload["damages"] = []
    incident = client.post(
        f"/api/repairs/vehicles/{second_bus}/incidents", json=payload
    )
    assert incident.status_code == 201, incident.text

    response = client.post(
        f"/api/repairs/vehicles/{first_bus}/media",
        files={"file": ("damage.png", b"\x89PNG test", "image/png")},
        data={"category": "повреждение", "incident_id": str(incident.json()["id"])},
    )
    assert response.status_code == 409
    assert "другому автобусу" in response.json()["detail"]
    assert not list((tmp_path / "uploads").glob("*"))
