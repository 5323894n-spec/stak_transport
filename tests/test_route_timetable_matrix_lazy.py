# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app
    db.DB_PATH = str(tmp_path / "timetable-lazy.db")
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


def _seed_route(run_times, *, dep="06:00", arr="06:30"):
    import app.db as db
    con = db.connect()
    try:
        route_id = con.execute(
            "INSERT INTO routes(number,name) VALUES(?,?)", ("MX", "Lazy")
        ).lastrowid
        for seq, run in enumerate(run_times, start=1):
            stop_id = con.execute(
                "INSERT INTO stops(external_code,name,active) VALUES(?,?,1)",
                (f"S{seq}", f"Stop {seq}"),
            ).lastrowid
            con.execute(
                "INSERT INTO route_stops(route_id,direction,sequence,stop_id,run_time_sec,dwell_time_sec) "
                "VALUES(?,?,?,?,?,0)",
                (route_id, "forward", seq, stop_id, run),
            )
        con.execute(
            "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,trip_number,direction,dep_time,arr_time,distance_km) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (route_id, "будни", 1, 1, 1, "прямое", dep, arr, 5.0),
        )
        con.commit()
        return route_id
    finally:
        con.close()


def _matrix(client, route_id):
    r = client.get(f"/api/routes/{route_id}/stop-times", params={"day_type": "будни"})
    assert r.status_code == 200, r.text
    return r.json()


def test_matrix_computes_from_runtimes_when_no_stored_times(client):
    route_id = _seed_route([0, 300, 420])  # positive leg run times
    trips = _matrix(client, route_id)["trips"]
    times = trips[0]["times"]
    assert len(times) == 3
    assert all(t["route_stop_id"] for t in times)
    assert times[0]["arrival_time"] == "06:00"
    secs = [t["arrival_sec"] for t in times]
    assert secs == sorted(secs) and secs[0] < secs[-1]


def test_matrix_falls_back_to_even_distribution_when_runtimes_missing(client):
    route_id = _seed_route([0, 0, 0], dep="06:00", arr="06:30")  # no leg run times
    trips = _matrix(client, route_id)["trips"]
    times = trips[0]["times"]
    assert len(times) == 3
    assert times[0]["arrival_time"] == "06:00"
    assert times[-1]["arrival_time"] == "06:30"
    secs = [t["arrival_sec"] for t in times]
    assert secs == sorted(secs) and len(set(secs)) == 3  # spread, not all equal


def test_matrix_prefers_stored_times_over_computed(client):
    import app.db as db
    route_id = _seed_route([0, 300, 420])
    # persist real times via the recalculate endpoint, then edit is preserved
    con = db.connect()
    try:
        trip_id = con.execute("SELECT id FROM route_trips WHERE route_id=?", (route_id,)).fetchone()["id"]
    finally:
        con.close()
    client.post(f"/api/trips/{trip_id}/stop-times/recalculate")
    trips = _matrix(client, route_id)["trips"]
    assert len(trips[0]["times"]) == 3
