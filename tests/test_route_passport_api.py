# -*- coding: utf-8 -*-
from io import BytesIO
from urllib.parse import unquote
from zipfile import ZipFile

import pytest

from tests.test_route_schedule_document import _client, _seed_route


@pytest.fixture(autouse=True)
def _offline_tiles(monkeypatch):
    import app.route_passport_maps as maps

    def unavailable(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(maps, "urlopen", unavailable)


def _download(client, route_id, *, style="D", effective_date="2026-08-03"):
    return client.get(
        f"/api/routes/{route_id}/passport-document.docx",
        params={"style": style, "effective_date": effective_date},
    )


def test_passport_endpoint_returns_docx(tmp_path):
    client = _client(tmp_path)
    route_id = _seed_route()
    response = _download(client, route_id, style="D")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    disposition = unquote(response.headers["content-disposition"])
    assert "Паспорт_маршрута_" in disposition
    assert "_D_2026-08-03.docx" in disposition
    with ZipFile(BytesIO(response.content)) as package:
        assert "word/document.xml" in package.namelist()


def test_passport_endpoint_accepts_landscape_style(tmp_path):
    client = _client(tmp_path)
    route_id = _seed_route()
    response = _download(client, route_id, style="F")
    assert response.status_code == 200, response.text
    disposition = unquote(response.headers["content-disposition"])
    assert "_F_2026-08-03.docx" in disposition


def test_passport_endpoint_rejects_unknown_style(tmp_path):
    client = _client(tmp_path)
    route_id = _seed_route()
    response = _download(client, route_id, style="X")
    assert response.status_code == 400
    assert response.json()["detail"] == "Оформление паспорта должно быть D или F"


def test_passport_endpoint_rejects_bad_date(tmp_path):
    client = _client(tmp_path)
    route_id = _seed_route()
    response = _download(client, route_id, effective_date="03.08.2026")
    assert response.status_code == 400


def test_passport_endpoint_missing_route_is_404(tmp_path):
    client = _client(tmp_path)
    response = _download(client, 999999)
    assert response.status_code == 404


def test_passport_endpoint_requires_authentication(tmp_path):
    client = _client(tmp_path)
    route_id = _seed_route()
    client.headers.pop("Authorization", None)
    response = _download(client, route_id)
    assert response.status_code in (401, 403)
