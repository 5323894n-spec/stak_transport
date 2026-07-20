# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient


DAY = "будни"


@pytest.fixture
def client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "route-shift-manual.db")
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


def _trips():
    return [
        {"id": 1, "output_number": 1, "dep_sec": 21600, "arr_sec": 25200},
        {"id": 2, "output_number": 1, "dep_sec": 25800, "arr_sec": 29400},
        {"id": 3, "output_number": 1, "dep_sec": 30000, "arr_sec": 33600},
    ]


def _shifts():
    return [
        {"id": 10, "shift_number": 1, "shift_type_id": 1,
         "output_number": 1, "trip_from_id": 1, "trip_to_id": 1,
         "start_sec": 21600, "end_sec": 25200, "driver_slots": 1,
         "handover_after_min": 10},
        {"id": 11, "shift_number": 2, "shift_type_id": 1,
         "output_number": 1, "trip_from_id": 2, "trip_to_id": 3,
         "start_sec": 25800, "end_sec": 33600, "driver_slots": 1,
         "handover_after_min": 0},
    ]


def test_replace_shift_boundaries_moves_neighbor_boundary_without_mutation():
    from app.route_shifts import replace_shift_boundaries

    trips = _trips()
    shifts = _shifts()
    original = [dict(row) for row in shifts]
    replacement_type = {"id": 2, "driver_slots": 2}

    result = replace_shift_boundaries(
        trips, shifts, shift_id=10, trip_from_id=1, trip_to_id=2,
        shift_type=replacement_type,
    )

    assert shifts == original
    assert [(row["trip_from_id"], row["trip_to_id"]) for row in result] == [
        (1, 2), (3, 3)
    ]
    assert result[0]["shift_type_id"] == 2
    assert result[0]["driver_slots"] == 2
    assert result[0]["start_sec"] == 21600
    assert result[0]["end_sec"] == 29400
    assert [row["shift_number"] for row in result] == [1, 2]


def _seed_output(route_number="M1"):
    import app.db as db

    con = db.connect()
    try:
        route_id = con.execute(
            "INSERT INTO routes(number,name) VALUES(?,?)", (route_number, "Manual")
        ).lastrowid
        trips = []
        for number, dep, arr in [(1, "06:00", "07:00"),
                                 (2, "07:10", "08:10"),
                                 (3, "08:20", "09:20")]:
            trips.append(con.execute(
                "INSERT INTO route_trips(route_id,day_type,output_number,"
                "shift_number,trip_number,direction,dep_time,arr_time,distance_km) "
                "VALUES(?,?,1,?,?,\'прямое\',?,?,1)",
                (route_id, DAY, 1 if number == 1 else 2, number, dep, arr),
            ).lastrowid)
        single = con.execute(
            "SELECT id FROM shift_types WHERE code='single_8h'"
        ).fetchone()[0]
        double = con.execute(
            "SELECT id FROM shift_types WHERE code='two_driver_long'"
        ).fetchone()[0]
        shift_ids = []
        for number, first, last, start, end, handover in [
            (1, trips[0], trips[0], 21600, 25200, 10),
            (2, trips[1], trips[2], 25800, 33600, 0),
        ]:
            shift_ids.append(con.execute(
                "INSERT INTO output_shifts(route_id,day_type,output_number,"
                "shift_number,shift_type_id,trip_from_id,trip_to_id,start_sec,"
                "end_sec,driver_slots,handover_after_min,source,is_manual_locked,"
                "created_at,updated_at) VALUES(?,?,1,?,?,?,?,?,?,1,?,\'generated\',"
                "0,datetime(\'now\'),datetime(\'now\'))",
                (route_id, DAY, number, single, first, last, start, end, handover),
            ).lastrowid)
        con.execute("UPDATE route_trips SET output_shift_id=? WHERE id=?",
                    (shift_ids[0], trips[0]))
        con.execute("UPDATE route_trips SET output_shift_id=? WHERE id IN (?,?)",
                    (shift_ids[1], trips[1], trips[2]))
        con.commit()
        return route_id, trips, shift_ids, single, double
    finally:
        con.close()


def _patch(client, shift_id, trips, type_id, reason="boundary correction"):
    return client.patch(
        f"/api/output-shifts/{shift_id}",
        json={"trip_from_id": trips[0], "trip_to_id": trips[1],
              "shift_type_id": type_id, "reason": reason},
    )


def _snapshot(con, route_id):
    return (
        [dict(row) for row in con.execute(
            "SELECT * FROM output_shifts WHERE route_id=? ORDER BY id", (route_id,)
        )],
        [dict(row) for row in con.execute(
            "SELECT id,shift_number,output_shift_id FROM route_trips "
            "WHERE route_id=? ORDER BY id", (route_id,)
        )],
    )


def test_patch_moves_boundary_changes_type_locks_and_audits(client):
    import app.db as db

    route_id, trips, shifts, _, double = _seed_output()
    response = _patch(client, shifts[0], trips, double)
    assert response.status_code == 200, response.text
    con = db.connect()
    try:
        rows = [dict(row) for row in con.execute(
            "SELECT * FROM output_shifts WHERE route_id=? ORDER BY shift_number",
            (route_id,),
        )]
        assert [(r["trip_from_id"], r["trip_to_id"]) for r in rows] == [
            (trips[0], trips[1]), (trips[2], trips[2])
        ]
        assert (rows[0]["shift_type_id"], rows[0]["driver_slots"]) == (double, 2)
        assert (rows[0]["source"], rows[0]["is_manual_locked"],
                rows[0]["manual_reason"]) == (
                    "manual", 1, "boundary correction")
        assert [tuple(row) for row in con.execute(
            "SELECT shift_number,output_shift_id FROM route_trips "
            "WHERE route_id=? ORDER BY trip_number", (route_id,)
        )] == [(1, shifts[0]), (1, shifts[0]), (2, shifts[1])]
        audit = con.execute(
            "SELECT old_value,new_value FROM audit_log WHERE object_type='output_shifts' "
            "AND object_id=? ORDER BY id DESC", (str(shifts[0]),)
        ).fetchone()
        assert audit and str(trips[0]) in audit["old_value"]
        assert str(trips[1]) in audit["new_value"]
    finally:
        con.close()


def test_patch_reason_and_invalid_range_roll_back_full_state(client):
    import app.db as db

    route_id, trips, shifts, single, _ = _seed_output()
    con = db.connect()
    try:
        before = _snapshot(con, route_id)
    finally:
        con.close()
    assert _patch(client, shifts[0], trips, single, " ").status_code == 400
    invalid = client.patch(
        f"/api/output-shifts/{shifts[0]}",
        json={"trip_from_id": trips[0], "trip_to_id": trips[2],
              "shift_type_id": single, "reason": "overlap"},
    )
    assert invalid.status_code == 400
    con = db.connect()
    try:
        assert _snapshot(con, route_id) == before
    finally:
        con.close()


def test_manual_lock_survives_default_preview_and_apply(client):
    import app.db as db

    route_id, trips, shifts, _, double = _seed_output()
    assert _patch(client, shifts[0], trips, double).status_code == 200
    preview = client.post(f"/api/routes/{route_id}/shift-generation/preview",
                          json={"day_type": DAY})
    assert preview.status_code == 200, preview.text
    locked = [s for s in preview.json()["outputs"][0]["shifts"]
              if s.get("is_manual_locked")]
    assert [s["id"] for s in locked] == [shifts[0]]
    applied = client.post(f"/api/routes/{route_id}/shift-generation/apply",
                          json={"day_type": DAY,
                                "preview_token": preview.json()["preview_token"]})
    assert applied.status_code == 200, applied.text
    con = db.connect()
    try:
        assert tuple(con.execute(
            "SELECT is_manual_locked,manual_reason FROM output_shifts WHERE id=?",
            (shifts[0],)).fetchone()) == (1, "boundary correction")
    finally:
        con.close()


def test_reset_is_scoped_validated_and_audited(client):
    import app.db as db

    route_id, trips, shifts, _, double = _seed_output()
    other_route, other_trips, other_shifts, _, other_double = _seed_output("M2")
    assert _patch(client, shifts[0], trips, double).status_code == 200
    assert _patch(client, other_shifts[0], other_trips, other_double).status_code == 200
    assert client.post(
        f"/api/routes/{route_id}/output-shifts/reset-manual",
        json={"day_type": DAY, "shift_id": shifts[0], "output_number": 1},
    ).status_code == 400
    assert client.post(
        f"/api/routes/{other_route}/output-shifts/reset-manual",
        json={"day_type": DAY, "shift_id": shifts[0]},
    ).status_code == 404
    response = client.post(
        f"/api/routes/{route_id}/output-shifts/reset-manual",
        json={"day_type": DAY, "output_number": 1},
    )
    assert response.status_code == 200, response.text
    con = db.connect()
    try:
        assert con.execute("SELECT COUNT(*) FROM output_shifts WHERE route_id=? "
                           "AND is_manual_locked=1", (route_id,)).fetchone()[0] == 0
        assert con.execute("SELECT is_manual_locked FROM output_shifts WHERE id=?",
                           (other_shifts[0],)).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM route_trips WHERE route_id=? "
                           "AND output_shift_id IS NULL", (route_id,)).fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM audit_log WHERE object_type="
                           "'output_shifts' AND action LIKE '%сброс%' AND object_id=?",
                           (str(route_id),)).fetchone()[0] == 1
    finally:
        con.close()


def test_replace_shift_boundaries_rejects_malformed_type_as_value_error():
    from app.route_shifts import replace_shift_boundaries

    with pytest.raises(ValueError):
        replace_shift_boundaries(
            _trips(), _shifts(), shift_id=10, trip_from_id=1, trip_to_id=2,
            shift_type={"driver_slots": 1},
        )


def test_reset_entire_day_without_selector(client):
    route_id, trips, shifts, _, double = _seed_output()
    assert _patch(client, shifts[0], trips, double).status_code == 200
    response = client.post(f"/api/routes/{route_id}/output-shifts/reset-manual",
                           json={"day_type": DAY})
    assert response.status_code == 200, response.text
    assert all(not row["is_manual_locked"] for row in response.json()["shifts"])


def test_patch_rolls_back_when_neighbor_exceeds_its_own_type_max(client):
    import app.db as db

    route_id, trips, shifts, _, double = _seed_output()
    con = db.connect()
    try:
        short_type = con.execute(
            "INSERT INTO shift_types(code,name,work_pattern,planned_duration_min,"
            "max_duration_min,driver_slots,allow_split,color,active,created_at,updated_at) "
            "VALUES('manual_short','Short','single',60,60,1,0,'#123456',1,"
            "datetime('now'),datetime('now'))"
        ).lastrowid
        con.execute("UPDATE output_shifts SET shift_type_id=? WHERE id=?",
                    (short_type, shifts[0]))
        con.commit()
        before = _snapshot(con, route_id)
    finally:
        con.close()

    response = client.patch(
        f"/api/output-shifts/{shifts[1]}",
        json={"trip_from_id": trips[2], "trip_to_id": trips[2],
              "shift_type_id": double, "reason": "move second boundary"},
    )
    assert response.status_code == 400, response.text
    assert "длитель" in response.json()["detail"].lower()
    con = db.connect()
    try:
        assert _snapshot(con, route_id) == before
    finally:
        con.close()


def _add_manual_output(route_id, output_number, day_type=DAY):
    import app.db as db

    con = db.connect()
    try:
        shift_type_id = con.execute(
            "SELECT id FROM shift_types WHERE code='single_8h'"
        ).fetchone()[0]
        trip_id = con.execute(
            "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,"
            "trip_number,direction,dep_time,arr_time,distance_km) "
            "VALUES(?,?,?,?,1,'прямое','10:00','11:00',1)",
            (route_id, day_type, output_number, 1),
        ).lastrowid
        shift_id = con.execute(
            "INSERT INTO output_shifts(route_id,day_type,output_number,shift_number,"
            "shift_type_id,trip_from_id,trip_to_id,start_sec,end_sec,driver_slots,"
            "handover_after_min,source,is_manual_locked,manual_reason,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,1,0,'manual',1,'other scope',datetime('now'),"
            "datetime('now'))",
            (route_id, day_type, output_number, 1, shift_type_id,
             trip_id, trip_id, 36000, 39600),
        ).lastrowid
        con.execute("UPDATE route_trips SET output_shift_id=? WHERE id=?",
                    (shift_id, trip_id))
        con.commit()
        return trip_id, shift_id
    finally:
        con.close()


def test_reset_by_shift_id_regenerates_only_its_output(client):
    import app.db as db

    route_id, trips, shifts, _, double = _seed_output()
    assert _patch(client, shifts[0], trips, double).status_code == 200
    _, other_output_shift = _add_manual_output(route_id, 2)
    _, other_day_shift = _add_manual_output(route_id, 1, "выходные")
    con = db.connect()
    try:
        unrelated_before = [dict(row) for row in con.execute(
            "SELECT * FROM output_shifts WHERE id IN (?,?) ORDER BY id",
            (other_output_shift, other_day_shift),
        )]
    finally:
        con.close()

    response = client.post(
        f"/api/routes/{route_id}/output-shifts/reset-manual",
        json={"day_type": DAY, "shift_id": shifts[0]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["output_numbers"] == [1]
    assert all(not row["is_manual_locked"] for row in response.json()["shifts"])
    con = db.connect()
    try:
        unrelated_after = [dict(row) for row in con.execute(
            "SELECT * FROM output_shifts WHERE id IN (?,?) ORDER BY id",
            (other_output_shift, other_day_shift),
        )]
        assert unrelated_after == unrelated_before
        assert con.execute(
            "SELECT COUNT(*) FROM route_trips WHERE route_id=? AND day_type=? "
            "AND output_number=1 AND output_shift_id IS NULL", (route_id, DAY)
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM audit_log WHERE object_type='output_shifts' "
            "AND action LIKE '%сброс%' AND object_id=?", (str(route_id),)
        ).fetchone()[0] == 1
    finally:
        con.close()


def test_reset_wrong_day_for_valid_route_is_scoped_and_non_mutating(client):
    import app.db as db

    route_id, trips, shifts, _, double = _seed_output()
    assert _patch(client, shifts[0], trips, double).status_code == 200
    con = db.connect()
    try:
        before = _snapshot(con, route_id)
    finally:
        con.close()
    response = client.post(
        f"/api/routes/{route_id}/output-shifts/reset-manual",
        json={"day_type": "выходные"},
    )
    assert response.status_code in (400, 404)
    assert response.json()["detail"]
    con = db.connect()
    try:
        assert _snapshot(con, route_id) == before
    finally:
        con.close()


def test_patch_rolls_back_when_modified_neighbor_type_is_inactive(client):
    import app.db as db

    route_id, trips, shifts, single, double = _seed_output()
    con = db.connect()
    try:
        con.execute("UPDATE shift_types SET active=0 WHERE id=?", (single,))
        con.commit()
        before = _snapshot(con, route_id)
    finally:
        con.close()

    response = client.patch(
        f"/api/output-shifts/{shifts[1]}",
        json={"trip_from_id": trips[2], "trip_to_id": trips[2],
              "shift_type_id": double, "reason": "move inactive neighbor"},
    )
    assert response.status_code == 400, response.text
    assert "актив" in response.json()["detail"].lower()
    con = db.connect()
    try:
        assert _snapshot(con, route_id) == before
    finally:
        con.close()


@pytest.mark.parametrize("operation", ["patch", "reset"])
def test_manual_write_endpoints_return_409_when_database_is_locked(client, operation):
    import sqlite3
    import app.db as db

    route_id, trips, shifts, single, _ = _seed_output()
    blocked = TestClient(client.app, raise_server_exceptions=False)
    blocked.headers.update(client.headers)
    con = db.connect()
    try:
        before = _snapshot(con, route_id)
        audit_before = con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    finally:
        con.close()
    from app.auth import current_user
    client.app.dependency_overrides[current_user] = lambda: {
        "username": "admin", "role": "админ"
    }
    locker = sqlite3.connect(db.DB_PATH, timeout=0)
    try:
        locker.execute("BEGIN IMMEDIATE")
        if operation == "patch":
            response = blocked.patch(
                f"/api/output-shifts/{shifts[0]}",
                json={"trip_from_id": trips[0], "trip_to_id": trips[0],
                      "shift_type_id": single, "reason": "locked"},
            )
        else:
            response = blocked.post(
                f"/api/routes/{route_id}/output-shifts/reset-manual",
                json={"day_type": DAY},
            )
        assert response.status_code == 409, response.text
    finally:
        locker.rollback()
        locker.close()
        client.app.dependency_overrides.pop(current_user, None)
    con = db.connect()
    try:
        assert _snapshot(con, route_id) == before
        assert con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == audit_before
    finally:
        con.close()


def _set_trip_times(con, trips, values):
    for trip_id, (dep, arr) in zip(trips, values):
        con.execute("UPDATE route_trips SET dep_time=?,arr_time=? WHERE id=?",
                    (dep, arr, trip_id))


def test_patch_rejects_new_handover_gap_below_route_setting(client):
    import app.db as db

    route_id, trips, shifts, _, double = _seed_output()
    con = db.connect()
    try:
        _set_trip_times(con, trips, [
            ("06:00", "07:00"), ("08:00", "09:00"),
            ("09:05", "10:05"),
        ])
        con.execute("UPDATE output_shifts SET start_sec=28800,end_sec=36300 "
                    "WHERE id=?", (shifts[1],))
        con.commit()
        before = _snapshot(con, route_id)
    finally:
        con.close()
    response = _patch(client, shifts[0], trips, double)
    assert response.status_code == 400, response.text
    assert "пересмен" in response.json()["detail"].lower()
    con = db.connect()
    try:
        assert _snapshot(con, route_id) == before
    finally:
        con.close()


def test_patch_persists_actual_handover_minutes_after_repartition(client):
    import app.db as db

    route_id, trips, shifts, _, double = _seed_output()
    con = db.connect()
    try:
        _set_trip_times(con, trips, [
            ("06:00", "07:00"), ("08:00", "09:00"),
            ("09:15", "10:15"),
        ])
        con.execute("UPDATE output_shifts SET start_sec=28800,end_sec=36900 "
                    "WHERE id=?", (shifts[1],))
        con.commit()
    finally:
        con.close()
    response = _patch(client, shifts[0], trips, double)
    assert response.status_code == 200, response.text
    assert [row["handover_after_min"] for row in response.json()["shifts"]] == [15, 0]


def test_patch_two_phase_renumbers_legacy_reversed_shifts(client):
    import app.db as db

    route_id, trips, shifts, _, double = _seed_output()
    con = db.connect()
    try:
        con.execute("UPDATE output_shifts SET shift_number=-1 WHERE id=?", (shifts[0],))
        con.execute("UPDATE output_shifts SET shift_number=1 WHERE id=?", (shifts[1],))
        con.execute("UPDATE output_shifts SET shift_number=2 WHERE id=?", (shifts[0],))
        con.execute("UPDATE route_trips SET shift_number=2 WHERE id=?", (trips[0],))
        con.execute("UPDATE route_trips SET shift_number=1 WHERE id IN (?,?)",
                    (trips[1], trips[2]))
        con.commit()
    finally:
        con.close()
    response = _patch(client, shifts[0], trips, double)
    assert response.status_code == 200, response.text
    con = db.connect()
    try:
        rows = con.execute("SELECT id,shift_number FROM output_shifts "
                           "WHERE route_id=? ORDER BY start_sec", (route_id,)).fetchall()
        assert [tuple(row) for row in rows] == [(shifts[0], 1), (shifts[1], 2)]
        links = con.execute("SELECT shift_number,output_shift_id FROM route_trips "
                            "WHERE route_id=? ORDER BY trip_number", (route_id,)).fetchall()
        assert [tuple(row) for row in links] == [
            (1, shifts[0]), (1, shifts[0]), (2, shifts[1])
        ]
    finally:
        con.close()
