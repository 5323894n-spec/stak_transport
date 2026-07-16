# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "timetable-api.db")
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
def route_with_trace(client):
    import app.db as db

    con = db.connect()
    try:
        route_id = con.execute(
            "INSERT INTO routes(number,name,trip_time_min,trip_time_back_min) "
            "VALUES(?,?,?,?)",
            ("M1", "Matrix route", 13, 13),
        ).lastrowid
        stop_ids = []
        for code, name in (("S1", "First"), ("S2", "Middle"), ("S3", "Last")):
            stop_ids.append(
                con.execute(
                    "INSERT INTO stops(external_code,name,active) VALUES(?,?,1)",
                    (code, name),
                ).lastrowid
            )
        for sequence, (stop_id, run_time, dwell) in enumerate(
            zip(stop_ids, (0, 300, 420), (0, 30, 0)), start=1
        ):
            con.execute(
                "INSERT INTO route_stops(route_id,direction,sequence,stop_id,"
                "run_time_sec,dwell_time_sec,is_timing_point) VALUES(?,?,?,?,?,?,?)",
                (route_id, "forward", sequence, stop_id, run_time, dwell,
                 1 if sequence in (1, 3) else 0),
            )
        trip_id = con.execute(
            "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,"
            "trip_number,direction,dep_time,arr_time,distance_km) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (route_id, "будни", 1, 1, 1, "прямое", "06:00", "06:13", 5.0),
        ).lastrowid
        con.commit()
        return route_id, trip_id
    finally:
        con.close()


def test_recalculate_trip_builds_persisted_matrix(client, route_with_trace):
    route_id, trip_id = route_with_trace

    response = client.post(f"/api/trips/{trip_id}/stop-times/recalculate")
    assert response.status_code == 200, response.text
    matrix = client.get(
        f"/api/routes/{route_id}/stop-times?day_type=будни"
    )
    assert matrix.status_code == 200, matrix.text
    data = matrix.json()
    assert [stop["sequence"] for stop in data["stops"]["forward"]] == [1, 2, 3]
    assert data["stops"]["backward"] == []
    assert data["trips"][0]["trip_id"] == trip_id
    assert [row["departure_time"] for row in data["trips"][0]["times"]] == [
        "06:00", "06:05", "06:12"
    ]
    assert data["trips"][0]["times"][1]["departure_sec"] == 21930


def test_legacy_trip_edit_recalculates_existing_stop_times(client, route_with_trace):
    route_id, trip_id = route_with_trace
    assert client.post(f"/api/trips/{trip_id}/stop-times/recalculate").status_code == 200
    trip = client.get(
        f"/api/trips?route_id={route_id}&day_type=будни"
    ).json()["items"][0]
    trip["dep_time"] = "07:00"
    trip["arr_time"] = "07:13"

    response = client.post("/api/trips", json=trip)
    assert response.status_code == 200, response.text
    matrix = client.get(
        f"/api/routes/{route_id}/stop-times?day_type=будни"
    ).json()
    assert matrix["trips"][0]["times"][0]["departure_time"] == "07:00"
    assert matrix["trips"][0]["times"][-1]["arrival_time"] == "07:12"


def test_deleting_trip_cascades_stop_times(client, route_with_trace):
    import app.db as db

    _, trip_id = route_with_trace
    assert client.post(f"/api/trips/{trip_id}/stop-times/recalculate").status_code == 200
    assert client.delete(f"/api/trips/{trip_id}").status_code == 200
    con = db.connect()
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM trip_stop_times WHERE trip_id=?", (trip_id,)
        ).fetchone()[0] == 0
    finally:
        con.close()
