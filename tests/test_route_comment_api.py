# -*- coding: utf-8 -*-
from tests.test_route_schedule_document import _client, _seed_route


def test_route_comment_saves_free_text(tmp_path):
    client = _client(tmp_path)
    route_id = _seed_route(number="201")
    response = client.put(
        f"/api/routes/{route_id}/comment",
        json={"comment": "Согласовано с ГИБДД 2026-08-08"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["comment"] == "Согласовано с ГИБДД 2026-08-08"
    network = client.get(f"/api/routes/{route_id}/network").json()
    assert network["route"]["comment"] == "Согласовано с ГИБДД 2026-08-08"


def test_route_comment_does_not_touch_erm_notes(tmp_path):
    import app.db as db
    client = _client(tmp_path)
    route_id = _seed_route(number="202")
    con = db.connect()
    try:
        con.execute(
            "UPDATE routes SET notes=? WHERE id=?",
            ('{"source": "ЭРМ", "details": {}}', route_id),
        )
        con.commit()
    finally:
        con.close()
    client.put(f"/api/routes/{route_id}/comment", json={"comment": "чисто"})
    con = db.connect()
    try:
        row = con.execute(
            "SELECT notes, comment FROM routes WHERE id=?", (route_id,)
        ).fetchone()
    finally:
        con.close()
    assert row["notes"] == '{"source": "ЭРМ", "details": {}}'
    assert row["comment"] == "чисто"


def test_route_comment_unknown_route_is_404(tmp_path):
    client = _client(tmp_path)
    response = client.put("/api/routes/999999/comment", json={"comment": "x"})
    assert response.status_code == 404
