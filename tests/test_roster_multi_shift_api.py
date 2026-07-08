# -*- coding: utf-8 -*-

from fastapi.testclient import TestClient


DATE = "2026-07-06"
DAY_TYPE = "\u0431\u0443\u0434\u043d\u0438"


def make_client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "atp-roster-multi-shift.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
    finally:
        con.close()

    client = TestClient(app)
    token = client.post("/api/login", json={"username": "admin", "password": "admin"}).json()["token"]
    client.headers.update({"Authorization": "Bearer " + token})
    return client


def create_driver(client):
    response = client.post("/api/refs/drivers", json={
        "tab_number": "9001",
        "fio": "Multi Shift Driver",
        "status": "\u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442",
        "default_schedule": "2/2",
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def create_bus(client, driver_id):
    response = client.post("/api/refs/buses", json={
        "garage_number": "B9001",
        "plate": "A001AA",
        "brand": "PAZ",
        "model": "Test",
        "status": "\u0438\u0441\u043f\u0440\u0430\u0432\u0435\u043d",
        "assigned_driver_id": driver_id,
    })
    assert response.status_code == 200, response.text
    bus_id = response.json()["id"]
    update = client.put(f"/api/refs/drivers/{driver_id}", json={
        "assigned_bus_id": bus_id,
        "status": "\u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442",
    })
    assert update.status_code == 200, update.text
    return bus_id


def create_route_with_two_shifts(client):
    response = client.post("/api/refs/routes", json={
        "number": "22",
        "name": "Depot - Center",
        "comm_type": "city",
        "start_point": "Depot",
        "end_point": "Center",
        "length_km": 35,
        "length_back_km": 40,
        "trip_time_min": 90,
        "trip_time_back_min": 90,
        "interval_min": 15,
        "outputs_count": 1,
        "bus_types": "large",
        "work_days": "daily",
        "version": 1,
        "active": 1,
    })
    assert response.status_code == 200, response.text
    route_id = response.json()["id"]
    trips = [
        (1, 1, 1, "06:00", "09:00", 15.0),
        (1, 1, 2, "09:15", "13:30", 20.0),
        (1, 2, 1, "14:00", "18:00", 20.0),
        (1, 2, 2, "18:15", "22:00", 20.0),
    ]
    for output, shift, trip_no, dep, arr, dist in trips:
        response = client.post("/api/trips", json={
            "route_id": route_id,
            "day_type": DAY_TYPE,
            "output_number": output,
            "shift_number": shift,
            "trip_number": trip_no,
            "direction": "\u043f\u0440\u044f\u043c\u043e\u0435" if trip_no % 2 else "\u043e\u0431\u0440\u0430\u0442\u043d\u043e\u0435",
            "dep_time": dep,
            "arr_time": arr,
            "distance_km": dist,
            "break_after_min": 0,
            "break_type": "",
        })
        assert response.status_code == 200, response.text
    return route_id


def post_assignment(client, driver_id, route_id, shift_number, **overrides):
    payload = {
        "driver_id": driver_id,
        "date": DATE,
        "route_id": route_id,
        "output_number": 1,
        "shift_number": shift_number,
        "trip_from": 1,
        "trip_to": 2,
        "comment": "",
    }
    payload.update(overrides)
    response = client.post("/api/roster/assignment", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_roster_schedule_options_suggests_shift_from_trips(tmp_path):
    client = make_client(tmp_path)
    route_id = create_route_with_two_shifts(client)

    response = client.get(
        f"/api/roster/schedule-options?route_id={route_id}&date={DATE}&output_number=1&shift_number=1"
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["day_type"] == DAY_TYPE
    assert {(o["output_number"], o["shift_number"]) for o in data["outputs"]} == {(1, 1), (1, 2)}
    assert [t["trip_number"] for t in data["trips"]] == [1, 2]
    assert data["suggestion"]["start_time"] == "06:00"
    assert data["suggestion"]["end_time"] == "13:30"
    assert data["suggestion"]["trip_from"] == 1
    assert data["suggestion"]["trip_to"] == 2
    assert data["suggestion"]["trips_count"] == 2
    assert data["suggestion"]["distance_km"] == 35.0
    assert data["suggestion"]["hours"] == 7.0

    partial = client.get(
        f"/api/roster/schedule-options?route_id={route_id}&date={DATE}&output_number=1&shift_number=1&trip_from=1&trip_to=1"
    )
    assert partial.status_code == 200, partial.text
    assert partial.json()["suggestion"]["start_time"] == "06:00"
    assert partial.json()["suggestion"]["end_time"] == "09:00"
    assert partial.json()["suggestion"]["trips_count"] == 1
    assert partial.json()["suggestion"]["distance_km"] == 15.0


def test_roster_assignments_allow_two_shifts_and_return_overtime_warning(tmp_path):
    client = make_client(tmp_path)
    driver_id = create_driver(client)
    route_id = create_route_with_two_shifts(client)

    first = post_assignment(client, driver_id, route_id, 1)
    second = post_assignment(client, driver_id, route_id, 2)

    assert first["assignment"]["shift_number"] == 1
    assert second["assignment"]["shift_number"] == 2
    assert any(v["type"] == "\u041f\u0440\u0435\u0432\u044b\u0448\u0435\u043d\u0438\u0435 \u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u0438 \u0441\u043c\u0435\u043d\u044b" for v in second["violations"])

    assignments = client.get(f"/api/roster/assignments?driver_id={driver_id}&date={DATE}")
    assert assignments.status_code == 200, assignments.text
    assert len(assignments.json()["items"]) == 2

    roster = client.get(f"/api/roster?date_from={DATE}&date_to={DATE}&driver_id={driver_id}")
    assert roster.status_code == 200, roster.text
    item = roster.json()["items"][0]
    assert item["status"] == "\u0440\u0430\u0431\u043e\u0442\u0430"
    assert item["assignment_count"] == 2
    assert item["start_time"] == "06:00"
    assert item["end_time"] == "22:00"
    assert item["hours"] == 14.5


def test_order_generation_uses_two_assignments_for_same_driver(tmp_path):
    client = make_client(tmp_path)
    driver_id = create_driver(client)
    create_bus(client, driver_id)
    route_id = create_route_with_two_shifts(client)
    post_assignment(client, driver_id, route_id, 1)
    post_assignment(client, driver_id, route_id, 2)

    generated = client.post("/api/orders/generate", json={"date": DATE, "regenerate": True})
    assert generated.status_code == 200, generated.text

    order = client.get(f"/api/orders?date={DATE}")
    assert order.status_code == 200, order.text
    lines = [line for line in order.json()["lines"] if line["route_id"] == route_id]
    assert len(lines) == 2
    assert [line["shift_number"] for line in lines] == [1, 2]
    assert {line["driver_id"] for line in lines} == {driver_id}
