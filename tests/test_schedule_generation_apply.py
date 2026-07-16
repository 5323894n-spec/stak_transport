# -*- coding: utf-8 -*-
import datetime
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "generation-apply.db")
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
def configured_route(client):
    import app.db as db

    con = db.connect()
    try:
        route_id = con.execute(
            "INSERT INTO routes(number,name,trip_time_min,trip_time_back_min,outputs_count) "
            "VALUES(?,?,?,?,?)",
            ("A1", "Apply route", 10, 10, 3),
        ).lastrowid
        stops = [
            con.execute(
                "INSERT INTO stops(external_code,name,active) VALUES(?,?,1)",
                (code, name),
            ).lastrowid
            for code, name in (("A", "A terminal"), ("B", "B terminal"))
        ]
        for direction, ordered in (
            ("forward", stops), ("backward", list(reversed(stops)))
        ):
            for sequence, stop_id in enumerate(ordered, start=1):
                con.execute(
                    "INSERT INTO route_stops(route_id,direction,sequence,stop_id,"
                    "run_time_sec,dwell_time_sec,is_timing_point) VALUES(?,?,?,?,?,?,1)",
                    (route_id, direction, sequence, stop_id,
                     0 if sequence == 1 else 600, 0),
                )
        con.execute(
            "INSERT INTO day_periods(route_id,day_type,name,start_min,end_min,"
            "interval_min,travel_time_factor,transition_mode,transition_window_min,"
            "color,priority,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (route_id, "будни", "Morning", 360, 400, 10, 1.0, "abrupt", 0,
             "#3b82f6", 0, 1, "2026-07-16", "2026-07-16"),
        )
        con.execute(
            "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,"
            "trip_number,direction,dep_time,arr_time,distance_km) VALUES(?,?,?,?,?,?,?,?,?)",
            (route_id, "будни", 9, 1, 1, "прямое", "05:00", "05:10", 1.0),
        )
        con.commit()
        return route_id
    finally:
        con.close()


def create_preview(client, route_id):
    response = client.post(
        f"/api/routes/{route_id}/schedule-generation/preview",
        json={"day_type": "будни", "outputs": 3, "terminal_layover_min": 5},
    )
    assert response.status_code == 200, response.text
    return response.json()


def apply_preview(client, route_id, token, day_type="будни"):
    return client.post(
        f"/api/routes/{route_id}/schedule-generation/apply",
        json={"day_type": day_type, "preview_token": token},
    )


def test_apply_replaces_route_day_and_persists_compatible_matrix(
    client, configured_route
):
    route_id = configured_route
    token = create_preview(client, route_id)["preview_token"]

    response = apply_preview(client, route_id, token)
    assert response.status_code == 200, response.text
    assert response.json()["trips"] == 8
    trips = client.get(
        f"/api/trips?route_id={route_id}&day_type=будни"
    ).json()["items"]
    matrix = client.get(
        f"/api/routes/{route_id}/stop-times?day_type=будни"
    ).json()["trips"]
    assert len(trips) == len(matrix) == 8
    assert all(trip["source"] == "period_generation" for trip in trips)
    assert all(trip["generation_key"] == token for trip in trips)
    assert all(len(trip["times"]) == 2 for trip in matrix)

    assert apply_preview(client, route_id, token).status_code == 409


def test_apply_token_is_route_day_bound_and_expiring(client, configured_route):
    import app.db as db

    route_id = configured_route
    token = create_preview(client, route_id)["preview_token"]
    assert apply_preview(client, route_id + 1, token).status_code == 404
    assert apply_preview(client, route_id, token, "суббота").status_code == 404

    con = db.connect()
    try:
        expired = (datetime.datetime.now() - datetime.timedelta(minutes=1)).isoformat()
        con.execute(
            "UPDATE schedule_generation_previews SET expires_at=? WHERE token=?",
            (expired, token),
        )
        con.commit()
    finally:
        con.close()
    assert apply_preview(client, route_id, token).status_code == 410


def test_corrupt_preview_rolls_back_existing_schedule(client, configured_route):
    import app.db as db

    route_id = configured_route
    preview = create_preview(client, route_id)
    token = preview["preview_token"]
    con = db.connect()
    try:
        plan = json.loads(con.execute(
            "SELECT payload_json FROM schedule_generation_previews WHERE token=?",
            (token,),
        ).fetchone()[0])
        plan["trips"][0]["stop_times"][0]["route_stop_id"] = 999999
        con.execute(
            "UPDATE schedule_generation_previews SET payload_json=? WHERE token=?",
            (json.dumps(plan), token),
        )
        con.commit()
    finally:
        con.close()

    response = apply_preview(client, route_id, token)
    assert response.status_code == 400
    trips = client.get(
        f"/api/trips?route_id={route_id}&day_type=будни"
    ).json()["items"]
    assert len(trips) == 1
    assert trips[0]["output_number"] == 9
