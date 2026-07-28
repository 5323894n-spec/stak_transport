# -*- coding: utf-8 -*-
from fastapi.testclient import TestClient


def test_route_card_assets_and_entry_points_are_served():
    from app.main import app

    client = TestClient(app)
    index = client.get("/")
    assert index.status_code == 200
    assert "/static/route-card.js" in index.text
    assert '/static/route-card.js?v=3.7' in index.text

    asset = client.get("/static/route-card.js")
    assert asset.status_code == 200
    source = asset.text
    assert "VIEWS.routeCard" in source
    assert "routeCardOpen" in source
    assert "/api/routes/" in source
    assert "/network" in source
