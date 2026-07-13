# -*- coding: utf-8 -*-
from tests.test_repair_requests_api import make_client


def test_admin_can_load_users_in_settings(tmp_path):
    client, _ = make_client(tmp_path)

    response = client.get("/api/users")

    assert response.status_code == 200, response.text
    assert any(item["username"] == "admin" for item in response.json()["items"])
