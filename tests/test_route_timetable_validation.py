# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def checked_route(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "timetable-validation.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
        route_id = con.execute(
            "INSERT INTO routes(number,name,trip_time_min,trip_time_back_min) "
            "VALUES('V1','Validation route',10,10)"
        ).lastrowid
        for sequence in range(1, 3):
            stop_id = con.execute(
                "INSERT INTO stops(external_code,name,active) VALUES(?,?,1)",
                (f"V{sequence}", f"Validation stop {sequence}"),
            ).lastrowid
            con.execute(
                "INSERT INTO route_stops(route_id,direction,sequence,stop_id,"
                "run_time_sec,dwell_time_sec,is_timing_point) VALUES(?,?,?,?,?,?,1)",
                (route_id, "forward", sequence, stop_id,
                 0 if sequence == 1 else 600, 0),
            )
        trip_id = con.execute(
            "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,"
            "trip_number,direction,dep_time,arr_time,distance_km) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (route_id, "будни", 1, 1, 1, "прямое", "06:00", "06:10", 3.0),
        ).lastrowid
        con.commit()
    finally:
        con.close()
    client = TestClient(app)
    token = client.post(
        "/api/login", json={"username": "admin", "password": "admin"}
    ).json()["token"]
    client.headers.update({"Authorization": "Bearer " + token})
    return client, route_id, trip_id


def test_route_check_reports_missing_stop_times(checked_route):
    client, route_id, _ = checked_route
    response = client.get(f"/api/routes/{route_id}/check?day_type=будни")
    assert response.status_code == 200, response.text
    problem = next(
        item for item in response.json()["problems"]
        if item["kind"] == "missing_stop_times"
    )
    assert problem["severity"] == "ошибка"
    assert problem["recommendation"]


def test_route_check_reports_nonmonotonic_stop_times(checked_route):
    import app.db as db

    client, route_id, trip_id = checked_route
    response = client.post(f"/api/trips/{trip_id}/stop-times/recalculate")
    assert response.status_code == 200, response.text
    con = db.connect()
    try:
        con.execute(
            "UPDATE trip_stop_times SET arrival_sec=20000,departure_sec=20000 "
            "WHERE trip_id=? AND sequence=2",
            (trip_id,),
        )
        con.commit()
    finally:
        con.close()

    response = client.get(f"/api/routes/{route_id}/check?day_type=будни")
    assert response.status_code == 200, response.text
    problems = response.json()["problems"]
    assert any(
        item["kind"] == "nonmonotonic_stop_times"
        and item["severity"] == "критично"
        for item in problems
    )
