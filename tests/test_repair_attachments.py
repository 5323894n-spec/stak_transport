# -*- coding: utf-8 -*-
import os
from tests.test_repair_operations_api import create_order
from tests.test_repair_requests_api import make_client


def test_upload_and_download_repair_attachment(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    monkeypatch.setenv("ATP_REPAIR_UPLOADS", str(uploads))
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    response = client.post(
        f"/api/repairs/orders/{order['id']}/attachments",
        files={"file": ("акт осмотра.pdf", b"%PDF-1.4 test", "application/pdf")},
        data={"category": "акт"},
    )
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["original_name"] == "акт осмотра.pdf"
    assert item["stored_name"].endswith(".pdf")
    assert "акт" not in item["stored_name"]
    assert (uploads / item["stored_name"]).exists()
    downloaded = client.get(f"/api/repairs/attachments/{item['id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"%PDF")


def test_repair_attachment_rejects_unsafe_type(tmp_path, monkeypatch):
    monkeypatch.setenv("ATP_REPAIR_UPLOADS", str(tmp_path / "uploads"))
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    response = client.post(
        f"/api/repairs/orders/{order['id']}/attachments",
        files={"file": ("../run.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert not list((tmp_path / "uploads").glob("*")) if (tmp_path / "uploads").exists() else True
