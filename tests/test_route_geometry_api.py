# -*- coding: utf-8 -*-
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import app.route_geometry as route_geometry
from app.route_geometry import (
    ANCHOR_TOLERANCE,
    DIRECTIONS,
    MAX_COORDINATES,
    MAX_GEOMETRY_BYTES,
    GeometryValidationError,
    GeometryVersionConflict,
    geometry_record,
    get_geometry,
    stop_anchors,
    validate_geometry,
    validate_geometry_shape,
)


def _geometry(coordinates):
    return {"type": "LineString", "coordinates": coordinates}


def _open_route_db(tmp_path, monkeypatch):
    from app import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "route-geometry-api.db"))
    db.init_db()
    return db.connect()


def _insert_route(con):
    return con.execute(
        "INSERT INTO routes(number, name) VALUES(?, ?)",
        ("G1", "Геометрия маршрута"),
    ).lastrowid


def _insert_route_stop(con, route_id, sequence, longitude, latitude):
    stop_id = con.execute(
        "INSERT INTO stops(name, longitude, latitude) VALUES(?, ?, ?)",
        (f"Остановка {sequence}", longitude, latitude),
    ).lastrowid
    con.execute(
        """
        INSERT INTO route_stops(route_id, direction, stop_id, sequence)
        VALUES(?, 'forward', ?, ?)
        """,
        (route_id, stop_id, sequence),
    )
    return stop_id


def test_public_geometry_contract_constants_and_errors():
    assert DIRECTIONS == ("forward", "backward")
    assert ANCHOR_TOLERANCE == 0.000001
    assert MAX_COORDINATES == 20_000
    assert MAX_GEOMETRY_BYTES == 2 * 1024 * 1024
    assert issubclass(GeometryValidationError, ValueError)
    assert issubclass(GeometryVersionConflict, ValueError)


def test_validate_geometry_accepts_ordered_anchors_and_normalizes_lists():
    result = validate_geometry(
        _geometry([(30, 50), (30.5, 50.5), (31, 51)]),
        [(30, 50), (31, 51)],
    )

    assert result == {
        "type": "LineString",
        "coordinates": [[30.0, 50.0], [30.5, 50.5], [31.0, 51.0]],
    }
    assert all(type(value) is float for point in result["coordinates"] for value in point)

def test_validate_geometry_shape_accepts_numeric_values_convertible_to_float():
    assert validate_geometry_shape(
        _geometry([[Decimal("30.25"), Decimal("50.5")], [31, 51]])
    ) == [[30.25, 50.5], [31.0, 51.0]]



@pytest.mark.parametrize(
    "geometry",
    [
        None,
        [],
        {},
        {"coordinates": [[0, 0], [1, 1]]},
        {"type": "Polygon", "coordinates": [[0, 0], [1, 1]]},
        {"type": "linestring", "coordinates": [[0, 0], [1, 1]]},
    ],
)
def test_validate_geometry_shape_rejects_wrong_or_missing_type(geometry):
    with pytest.raises(GeometryValidationError, match="LineString"):
        validate_geometry_shape(geometry)


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "LineString"},
        {"type": "LineString", "coordinates": ((0, 0), (1, 1))},
    ],
)
def test_validate_geometry_shape_requires_coordinates_list(geometry):
    with pytest.raises(GeometryValidationError, match="списком"):
        validate_geometry_shape(geometry)


@pytest.mark.parametrize(
    "coordinates",
    [
        [],
        [[0, 0]],
    ],
)
def test_validate_geometry_shape_rejects_too_few_coordinates(coordinates):
    with pytest.raises(GeometryValidationError, match="не менее двух"):
        validate_geometry_shape(_geometry(coordinates))


def test_validate_geometry_shape_rejects_too_many_coordinates():
    coordinates = [[0, 0]] * (MAX_COORDINATES + 1)

    with pytest.raises(GeometryValidationError, match="20000"):
        validate_geometry_shape(_geometry(coordinates))


@pytest.mark.parametrize(
    "point",
    [
        None,
        "0,0",
        [],
        [0],
        [0, 0, 0],
    ],
)
def test_validate_geometry_shape_rejects_malformed_point_length(point):
    with pytest.raises(GeometryValidationError, match="двух"):
        validate_geometry_shape(_geometry([[0, 0], point]))


@pytest.mark.parametrize(
    "point",
    [
        [True, 0],
        [0, False],
    ],
)
def test_validate_geometry_shape_rejects_boolean_values(point):
    with pytest.raises(GeometryValidationError, match="числ"):
        validate_geometry_shape(_geometry([[0, 0], point]))


def test_validate_geometry_shape_rejects_numeric_strings():
    with pytest.raises(GeometryValidationError, match="числ"):
        validate_geometry_shape(_geometry([[0, 0], ["1", 2]]))


@pytest.mark.parametrize(
    "point",
    [
        [float("nan"), 0],
        [float("inf"), 0],
        [0, float("-inf")],
    ],
)
def test_validate_geometry_shape_rejects_non_finite_values(point):
    with pytest.raises(GeometryValidationError, match="конечн"):
        validate_geometry_shape(_geometry([[0, 0], point]))


@pytest.mark.parametrize(
    ("point", "label"),
    [
        ([180.000001, 0], "Долгота"),
        ([-180.000001, 0], "Долгота"),
        ([0, 90.000001], "Широта"),
        ([0, -90.000001], "Широта"),
    ],
)
def test_validate_geometry_shape_rejects_coordinate_ranges(point, label):
    with pytest.raises(GeometryValidationError, match=label):
        validate_geometry_shape(_geometry([[0, 0], point]))


@pytest.mark.parametrize(
    "second_point",
    [
        [10, 20],
        [10 + ANCHOR_TOLERANCE / 2, 20 - ANCHOR_TOLERANCE / 2],
    ],
)
def test_validate_geometry_shape_rejects_consecutive_duplicates(second_point):
    with pytest.raises(GeometryValidationError, match="совпад"):
        validate_geometry_shape(_geometry([[10, 20], second_point, [11, 21]]))


def test_validate_geometry_rejects_missing_anchor():
    with pytest.raises(GeometryValidationError, match="останов"):
        validate_geometry(
            _geometry([[0, 0], [1, 1], [2, 2]]),
            [(0, 0), (3, 3)],
        )


def test_validate_geometry_rejects_reversed_anchors():
    with pytest.raises(GeometryValidationError, match="поряд"):
        validate_geometry(
            _geometry([[0, 0], [1, 1], [2, 2]]),
            [(2, 2), (0, 0)],
        )


def test_validate_geometry_anchor_tolerance_is_inclusive():
    result = validate_geometry(
        _geometry([[0, 0], [1, 1]]),
        [(ANCHOR_TOLERANCE, -ANCHOR_TOLERANCE), (1, 1)],
    )

    assert result["coordinates"] == [[0.0, 0.0], [1.0, 1.0]]


def test_validate_geometry_rejects_anchor_just_outside_tolerance():
    with pytest.raises(GeometryValidationError, match="останов"):
        validate_geometry(
            _geometry([[0, 0], [1, 1]]),
            [(ANCHOR_TOLERANCE * 1.01, 0), (1, 1)],
        )


@pytest.mark.parametrize(
    ("start", "anchor"),
    [
        ([30, 50], [30.000001, 50.000001]),
        ([-30, -50], [-30.000001, -50.000001]),
    ],
)
def test_validate_geometry_accepts_realistic_decimal_tolerance_boundary(
    start, anchor
):
    result = validate_geometry(
        _geometry([start, [0, 0]]),
        [anchor, [0, 0]],
    )

    assert result["coordinates"] == [
        [float(start[0]), float(start[1])],
        [0.0, 0.0],
    ]


@pytest.mark.parametrize(
    ("start", "anchor"),
    [
        ([30, 50], [30.00000101, 50.00000101]),
        ([-30, -50], [-30.00000101, -50.00000101]),
    ],
)
def test_validate_geometry_rejects_realistic_coordinates_just_outside_tolerance(
    start, anchor
):
    with pytest.raises(GeometryValidationError, match="останов"):
        validate_geometry(_geometry([start, [0, 0]]), [anchor, [0, 0]])


def test_validate_geometry_shape_enforces_serialized_byte_limit(monkeypatch):
    monkeypatch.setattr(route_geometry, "MAX_GEOMETRY_BYTES", 20)

    with pytest.raises(GeometryValidationError, match="2 МБ"):
        validate_geometry_shape(_geometry([[0, 0], [1, 1]]))


def test_stop_anchors_returns_longitude_latitude_in_sequence_order(
    tmp_path, monkeypatch
):
    con = _open_route_db(tmp_path, monkeypatch)
    try:
        route_id = _insert_route(con)
        _insert_route_stop(con, route_id, 2, 31.25, 51.5)
        _insert_route_stop(con, route_id, 1, 30, 50)

        assert stop_anchors(con, route_id, "forward") == [
            (30.0, 50.0),
            (31.25, 51.5),
        ]
    finally:
        con.close()


def test_stop_anchors_rejects_too_few_stops(tmp_path, monkeypatch):
    con = _open_route_db(tmp_path, monkeypatch)
    try:
        route_id = _insert_route(con)
        _insert_route_stop(con, route_id, 1, 30, 50)

        with pytest.raises(GeometryValidationError, match="не менее двух"):
            stop_anchors(con, route_id, "forward")
    finally:
        con.close()


@pytest.mark.parametrize(
    ("longitude", "latitude"),
    [
        (None, 50),
        (30, None),
    ],
)
def test_stop_anchors_rejects_missing_coordinates(
    tmp_path, monkeypatch, longitude, latitude
):
    con = _open_route_db(tmp_path, monkeypatch)
    try:
        route_id = _insert_route(con)
        _insert_route_stop(con, route_id, 1, 30, 50)
        _insert_route_stop(con, route_id, 2, longitude, latitude)

        with pytest.raises(GeometryValidationError, match="координат"):
            stop_anchors(con, route_id, "forward")
    finally:
        con.close()


def test_geometry_record_returns_none_for_missing_row():
    assert geometry_record(None) is None


def test_get_geometry_returns_none_when_not_saved(tmp_path, monkeypatch):
    con = _open_route_db(tmp_path, monkeypatch)
    try:
        route_id = _insert_route(con)

        assert get_geometry(con, route_id, "forward") is None
    finally:
        con.close()


def test_geometry_record_and_get_geometry_serialize_public_fields(
    tmp_path, monkeypatch
):
    con = _open_route_db(tmp_path, monkeypatch)
    try:
        route_id = _insert_route(con)
        geometry = _geometry([[30.0, 50.0], [31.0, 51.0]])
        con.execute(
            """
            INSERT INTO route_geometries(
              route_id, direction, geometry_json, source, version,
              updated_by, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                route_id,
                "backward",
                json.dumps(geometry),
                "manual",
                4,
                "admin",
                "2026-07-29 12:34:56",
            ),
        )
        row = con.execute(
            """
            SELECT geometry_json, source, version, updated_by, updated_at
            FROM route_geometries
            WHERE route_id=? AND direction='backward'
            """,
            (route_id,),
        ).fetchone()
        expected = {
            "geometry": geometry,
            "source": "manual",
            "version": 4,
            "updated_by": "admin",
            "updated_at": "2026-07-29 12:34:56",
        }

        assert geometry_record(row) == expected
        assert get_geometry(con, route_id, "backward") == expected
        assert get_geometry(con, route_id, "forward") is None
    finally:
        con.close()

@pytest.fixture
def geometry_api(tmp_path, monkeypatch):
    from app import db
    from app.auth import ensure_admin
    from app.main import app

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "route-geometry-http.db"))
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
    finally:
        con.close()
    client = TestClient(app)
    token = client.post(
        "/api/login", json={"username": "admin", "password": "admin"}
    ).json()["token"]
    client.headers.update({"Authorization": "Bearer " + token})
    created = client.post(
        "/api/refs/routes", json={"number": "G-API", "name": "Геометрия API"}
    )
    assert created.status_code == 200, created.text
    route_id = created.json()["id"]
    stops = []
    for number, (longitude, latitude) in enumerate(
        ((30.0, 50.0), (31.0, 51.0)), 1
    ):
        response = client.post("/api/stops", json={
            "name": f"Точка {number}", "external_code": f"G{number}",
            "longitude": longitude, "latitude": latitude,
        })
        assert response.status_code == 200, response.text
        stops.append(response.json()["id"])
    forward = [[30.0, 50.0], [31.0, 51.0]]
    backward = list(reversed(forward))
    for direction, ordered_stops in (
        ("forward", stops), ("backward", list(reversed(stops)))
    ):
        response = client.put(
            f"/api/routes/{route_id}/stops/{direction}",
            json={"items": [
                {"stop_id": stop_id, "sequence": sequence,
                 "distance_from_prev_km": 0 if sequence == 1 else 1.4}
                for sequence, stop_id in enumerate(ordered_stops, 1)
            ]},
        )
        assert response.status_code == 200, response.text
    return {"client": client, "route_id": route_id,
            "forward": forward, "backward": backward}


def _put_geometry(client, route_id, direction, coordinates, expected_version):
    return client.put(
        f"/api/routes/{route_id}/geometry/{direction}",
        json={"geometry": _geometry(coordinates),
              "expected_version": expected_version},
    )


def _delete_geometry(client, route_id, direction, expected_version):
    return client.request(
        "DELETE", f"/api/routes/{route_id}/geometry/{direction}",
        json={"expected_version": expected_version},
    )


def _geometry_audits():
    from app import db

    con = db.connect()
    try:
        return [dict(row) for row in con.execute(
            "SELECT * FROM audit_log "
            "WHERE object_type='route_geometry' ORDER BY id"
        )]
    finally:
        con.close()


def _stored_geometry(route_id, direction):
    from app import db

    con = db.connect()
    try:
        return get_geometry(con, route_id, direction)
    finally:
        con.close()


def test_geometry_api_first_put_and_network_read_are_compatible(geometry_api):
    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    coordinates = [geometry_api["forward"][0], [30.5, 50.5],
                   geometry_api["forward"][1]]

    response = _put_geometry(client, route_id, "forward", coordinates, 0)

    assert response.status_code == 200, response.text
    assert response.json()["geometry"] == _geometry(coordinates)
    assert response.json()["source"] == "manual"
    assert response.json()["version"] == 1
    network = client.get(f"/api/routes/{route_id}/network")
    assert network.status_code == 200, network.text
    body = network.json()
    assert {"route", "forward", "backward", "totals", "warnings"} <= body.keys()
    assert [row["stop"]["longitude"] for row in body["forward"]] == [30.0, 31.0]
    assert body["geometries"]["forward"] == response.json()
    assert body["geometries"]["backward"] is None


def test_geometry_api_updates_versions_and_directions_independently(geometry_api):
    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    assert _put_geometry(
        client, route_id, "forward", geometry_api["forward"], 0
    ).status_code == 200
    updated_coordinates = [geometry_api["forward"][0], [30.25, 50.25],
                           geometry_api["forward"][1]]

    updated = _put_geometry(client, route_id, "forward", updated_coordinates, 1)
    backward = _put_geometry(
        client, route_id, "backward", geometry_api["backward"], 0
    )

    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert updated.json()["geometry"] == _geometry(updated_coordinates)
    assert backward.status_code == 200, backward.text
    assert backward.json()["version"] == 1
    geometries = client.get(
        f"/api/routes/{route_id}/network"
    ).json()["geometries"]
    assert geometries["forward"]["version"] == 2
    assert geometries["backward"]["version"] == 1


def test_stop_coordinate_move_updates_geometry_anchor_and_version(geometry_api):
    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    coordinates = [geometry_api["forward"][0], [30.5, 50.5],
                   geometry_api["forward"][1]]
    saved = _put_geometry(client, route_id, "forward", coordinates, 0)
    assert saved.status_code == 200, saved.text
    stop_id = client.get(
        f"/api/routes/{route_id}/network"
    ).json()["forward"][0]["stop"]["id"]

    response = client.put(
        f"/api/stops/{stop_id}", json={"longitude": 30.25}
    )

    assert response.status_code == 200, response.text
    updated = _stored_geometry(route_id, "forward")
    assert updated["geometry"]["coordinates"] == [
        [30.25, 50.0], [30.5, 50.5], [31.0, 51.0]
    ]
    assert updated["source"] == "manual"
    assert updated["version"] == 2
    from app import db
    con = db.connect()
    try:
        audit_value = con.execute(
            "SELECT new_value FROM audit_log WHERE object_type='stops' "
            "AND object_id=? ORDER BY id DESC LIMIT 1", (str(stop_id),)
        ).fetchone()["new_value"]
    finally:
        con.close()
    audit_new = json.loads(audit_value)
    assert audit_new["geometry_changes"][0]["old"]["version"] == 1
    assert audit_new["geometry_changes"][0]["new"]["version"] == 2
    assert audit_new["geometry_changes"][0]["new"]["coordinates"] == 3
    assert "LineString" not in audit_value


def test_stale_create_and_update_leave_geometry_and_audit_unchanged(geometry_api):
    client = geometry_api["client"]
    route_id = geometry_api["route_id"]

    stale_create = _put_geometry(
        client, route_id, "backward", geometry_api["backward"], 1
    )
    assert stale_create.status_code == 409
    assert _stored_geometry(route_id, "backward") is None
    assert _geometry_audits() == []

    saved = _put_geometry(client, route_id, "forward", geometry_api["forward"], 0)
    assert saved.status_code == 200, saved.text
    before = _stored_geometry(route_id, "forward")
    audits_before = _geometry_audits()
    stale_update = _put_geometry(
        client, route_id, "forward",
        [geometry_api["forward"][0], [30.5, 50.5],
         geometry_api["forward"][1]], 0,
    )

    assert stale_update.status_code == 409
    assert _stored_geometry(route_id, "forward") == before
    assert _geometry_audits() == audits_before


def test_delete_requires_current_version_and_returns_network_null(geometry_api):
    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    saved = _put_geometry(client, route_id, "forward", geometry_api["forward"], 0)
    assert saved.status_code == 200, saved.text

    stale = _delete_geometry(client, route_id, "forward", 0)
    assert stale.status_code == 409
    assert _stored_geometry(route_id, "forward")["version"] == 1
    audits_after_stale = _geometry_audits()
    deleted = _delete_geometry(client, route_id, "forward", 1)

    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"ok": True, "direction": "forward"}
    network = client.get(f"/api/routes/{route_id}/network").json()
    assert network["geometries"]["forward"] is None
    assert len(_geometry_audits()) == len(audits_after_stale) + 1


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_geometry_api_rejects_invalid_direction(geometry_api, method):
    client = geometry_api["client"]
    payload = {"expected_version": 0}
    if method == "PUT":
        payload["geometry"] = _geometry(geometry_api["forward"])

    response = client.request(
        method,
        f"/api/routes/{geometry_api['route_id']}/geometry/sideways",
        json=payload,
    )

    assert response.status_code == 400
    assert "направ" in response.json()["detail"].lower()
    assert _geometry_audits() == []


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
@pytest.mark.parametrize("expected_version", [None, True, -1, 1.5, "1"])
def test_geometry_api_rejects_invalid_expected_version(
    geometry_api, method, expected_version
):
    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    payload = {}
    if expected_version is not None:
        payload["expected_version"] = expected_version
    if method == "PUT":
        payload["geometry"] = _geometry(geometry_api["forward"])

    response = client.request(
        method, f"/api/routes/{route_id}/geometry/forward", json=payload
    )

    assert response.status_code == 400
    assert "верс" in response.json()["detail"].lower()
    assert _stored_geometry(route_id, "forward") is None
    assert _geometry_audits() == []


@pytest.mark.parametrize("geometry", [
    {"type": "Polygon", "coordinates": [[30, 50], [31, 51]]},
    _geometry([[30, 50], [30.5, 50.5]]),
])
def test_geometry_api_rejects_invalid_geojson_or_anchor(geometry_api, geometry):
    client = geometry_api["client"]
    route_id = geometry_api["route_id"]

    response = client.put(
        f"/api/routes/{route_id}/geometry/forward",
        json={"geometry": geometry, "expected_version": 0},
    )

    assert response.status_code == 400
    assert _stored_geometry(route_id, "forward") is None
    assert _geometry_audits() == []


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_geometry_api_returns_route_not_found_without_writes(geometry_api, method):
    client = geometry_api["client"]
    payload = {"expected_version": 0}
    if method == "PUT":
        payload["geometry"] = _geometry(geometry_api["forward"])

    response = client.request(
        method, "/api/routes/999999/geometry/forward", json=payload
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Маршрут не найден"
    assert _geometry_audits() == []


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_read_only_role_cannot_change_geometry(geometry_api, method):
    from app import db
    from app.auth import hash_password

    con = db.connect()
    try:
        con.execute(
            "INSERT INTO users(username,password_hash,full_name,role,active) "
            "VALUES(?,?,?,?,1)",
            ("geometry-viewer", hash_password("secret"),
             "Наблюдатель", "руководитель"),
        )
        con.commit()
    finally:
        con.close()
    viewer = TestClient(geometry_api["client"].app)
    token = viewer.post(
        "/api/login",
        json={"username": "geometry-viewer", "password": "secret"},
    ).json()["token"]
    viewer.headers.update({"Authorization": "Bearer " + token})
    payload = {"expected_version": 0}
    if method == "PUT":
        payload["geometry"] = _geometry(geometry_api["forward"])

    response = viewer.request(
        method,
        f"/api/routes/{geometry_api['route_id']}/geometry/forward",
        json=payload,
    )

    assert response.status_code == 403
    assert _stored_geometry(geometry_api["route_id"], "forward") is None
    assert _geometry_audits() == []


def test_successful_geometry_audits_only_contain_summaries(geometry_api):
    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    coordinates = [geometry_api["forward"][0], [30.5, 50.5],
                   geometry_api["forward"][1]]
    saved = _put_geometry(client, route_id, "forward", coordinates, 0)
    assert saved.status_code == 200, saved.text
    deleted = _delete_geometry(client, route_id, "forward", 1)
    assert deleted.status_code == 200, deleted.text

    audits = _geometry_audits()

    assert [row["action"] for row in audits] == [
        "сохранение геометрии маршрута", "сброс геометрии маршрута"
    ]
    summaries = []
    for row in audits:
        assert row["object_id"] == str(route_id)
        summaries.extend([
            json.loads(value)
            for value in (row["old_value"], row["new_value"])
        ])
    absent = {
        "direction": "forward", "source": None, "version": 0,
        "coordinates": 0,
    }
    saved_summary = summaries[1]
    deleted_summary = summaries[2]
    assert summaries[0] == absent
    assert saved_summary == {
        "direction": "forward", "source": "manual", "version": 1,
        "coordinates": 3,
    }
    assert deleted_summary == saved_summary
    assert summaries[3] == absent
    assert all("geometry" not in summary for summary in summaries)
    assert all(isinstance(summary["coordinates"], int) for summary in summaries)


def test_save_and_delete_geometry_domain_lifecycle_without_implicit_commit(
    tmp_path, monkeypatch
):
    con = _open_route_db(tmp_path, monkeypatch)
    try:
        route_id = _insert_route(con)
        _insert_route_stop(con, route_id, 1, 30, 50)
        _insert_route_stop(con, route_id, 2, 31, 51)
        con.commit()
        geometry = _geometry([[30, 50], [30.5, 50.5], [31, 51]])

        old, saved = route_geometry.save_geometry(
            con, route_id, "forward", geometry, "manual", 0,
            "admin", "2026-07-29T12:00:00",
        )

        assert old is None
        assert saved["version"] == 1
        assert con.in_transaction
        stored_json = con.execute(
            "SELECT geometry_json FROM route_geometries WHERE route_id=?",
            (route_id,),
        ).fetchone()["geometry_json"]
        assert stored_json == json.dumps(
            saved["geometry"], ensure_ascii=False, separators=(",", ":")
        )
        con.commit()

        old, updated = route_geometry.save_geometry(
            con, route_id, "forward", geometry, "osrm", 1,
            "admin", "2026-07-29T12:01:00",
        )
        assert old["version"] == 1
        assert updated["version"] == 2
        assert updated["source"] == "osrm"
        assert con.execute(
            "SELECT last_version FROM route_geometry_revisions "
            "WHERE route_id=? AND direction='forward'", (route_id,)
        ).fetchone()["last_version"] == 2
        con.commit()

        deleted = route_geometry.delete_geometry(con, route_id, "forward", 2)
        assert deleted == updated
        assert con.in_transaction
        assert get_geometry(con, route_id, "forward") is None
        assert con.execute(
            "SELECT last_version FROM route_geometry_revisions "
            "WHERE route_id=? AND direction='forward'", (route_id,)
        ).fetchone()["last_version"] == 2
    finally:
        con.close()


@pytest.mark.parametrize("function_name", ["save_geometry", "delete_geometry"])
@pytest.mark.parametrize("expected_version", [True, -1, 1.5, "1", None])
def test_geometry_domain_rejects_invalid_expected_version(
    tmp_path, monkeypatch, function_name, expected_version
):
    con = _open_route_db(tmp_path, monkeypatch)
    try:
        route_id = _insert_route(con)
        _insert_route_stop(con, route_id, 1, 30, 50)
        _insert_route_stop(con, route_id, 2, 31, 51)
        function = getattr(route_geometry, function_name)

        with pytest.raises(GeometryValidationError, match="верс"):
            if function_name == "save_geometry":
                function(
                    con, route_id, "forward", _geometry([[30, 50], [31, 51]]),
                    "manual", expected_version, "admin", "now",
                )
            else:
                function(con, route_id, "forward", expected_version)
    finally:
        con.close()


@pytest.mark.parametrize("direction", ["", "sideways", None])
def test_geometry_domain_rejects_invalid_direction(tmp_path, monkeypatch, direction):
    con = _open_route_db(tmp_path, monkeypatch)
    try:
        route_id = _insert_route(con)
        with pytest.raises(GeometryValidationError, match="Направление"):
            route_geometry.delete_geometry(con, route_id, direction, 0)
    finally:
        con.close()


def test_save_geometry_rejects_invalid_source(tmp_path, monkeypatch):
    con = _open_route_db(tmp_path, monkeypatch)
    try:
        route_id = _insert_route(con)
        with pytest.raises(GeometryValidationError, match="Источник"):
            route_geometry.save_geometry(
                con, route_id, "forward", _geometry([[30, 50], [31, 51]]),
                "import", 0, "admin", "now",
            )
    finally:
        con.close()


def test_geometry_domain_conflict_does_not_mutate_current_row(tmp_path, monkeypatch):
    con = _open_route_db(tmp_path, monkeypatch)
    try:
        route_id = _insert_route(con)
        _insert_route_stop(con, route_id, 1, 30, 50)
        _insert_route_stop(con, route_id, 2, 31, 51)
        route_geometry.save_geometry(
            con, route_id, "forward", _geometry([[30, 50], [31, 51]]),
            "manual", 0, "admin", "first",
        )
        con.commit()
        before = get_geometry(con, route_id, "forward")

        with pytest.raises(GeometryVersionConflict, match="Версия"):
            route_geometry.save_geometry(
                con, route_id, "forward",
                _geometry([[30, 50], [30.5, 50.5], [31, 51]]),
                "manual", 0, "admin", "second",
            )
        assert get_geometry(con, route_id, "forward") == before

        with pytest.raises(GeometryVersionConflict, match="Версия"):
            route_geometry.delete_geometry(con, route_id, "forward", 0)
        assert get_geometry(con, route_id, "forward") == before
    finally:
        con.close()


def test_save_and_delete_detect_zero_rowcount_races(tmp_path, monkeypatch):
    class RaceConnection:
        def __init__(self, con, blocked_statement):
            self.con = con
            self.blocked_statement = blocked_statement

        def execute(self, statement, parameters=()):
            if statement.strip().startswith(self.blocked_statement):
                return type("ZeroRowCursor", (), {"rowcount": 0})()
            return self.con.execute(statement, parameters)

    con = _open_route_db(tmp_path, monkeypatch)
    try:
        route_id = _insert_route(con)
        _insert_route_stop(con, route_id, 1, 30, 50)
        _insert_route_stop(con, route_id, 2, 31, 51)
        geometry = _geometry([[30, 50], [31, 51]])
        route_geometry.save_geometry(
            con, route_id, "forward", geometry, "manual", 0,
            "admin", "first",
        )
        con.commit()

        with pytest.raises(
            GeometryVersionConflict, match="изменена другим пользователем"
        ):
            route_geometry.save_geometry(
                RaceConnection(con, "UPDATE route_geometries"),
                route_id, "forward", geometry, "manual", 1,
                "admin", "second",
            )
        with pytest.raises(
            GeometryVersionConflict, match="изменена другим пользователем"
        ):
            route_geometry.delete_geometry(
                RaceConnection(con, "DELETE FROM route_geometries"),
                route_id, "forward", 1,
            )
        assert get_geometry(con, route_id, "forward")["version"] == 1
    finally:
        con.close()


def test_save_maps_concurrent_unique_insert_to_version_conflict(tmp_path, monkeypatch):
    class InsertRaceConnection:
        def __init__(self, con):
            self.con = con

        def execute(self, statement, parameters=()):
            if statement.strip().startswith("INSERT INTO route_geometries"):
                self.con.execute(statement, parameters)
                raise route_geometry.sqlite3.IntegrityError("UNIQUE constraint failed")
            return self.con.execute(statement, parameters)

    con = _open_route_db(tmp_path, monkeypatch)
    try:
        route_id = _insert_route(con)
        _insert_route_stop(con, route_id, 1, 30, 50)
        _insert_route_stop(con, route_id, 2, 31, 51)

        with pytest.raises(
            GeometryVersionConflict, match="Версия геометрии изменилась"
        ):
            route_geometry.save_geometry(
                InsertRaceConnection(con), route_id, "forward",
                _geometry([[30, 50], [31, 51]]), "manual", 0,
                "admin", "now",
            )
    finally:
        con.close()


class _TrackedGeometryConnection:
    def __init__(self, con, fail_commit=False):
        self._con = con
        self._fail_commit = fail_commit
        self.rollback_calls = 0

    def __getattr__(self, name):
        return getattr(self._con, name)

    def commit(self):
        if self._fail_commit:
            raise RuntimeError("geometry commit failed")
        return self._con.commit()

    def rollback(self):
        self.rollback_calls += 1
        return self._con.rollback()


def _track_endpoint_connection(monkeypatch, fail_commit=False):
    from app import db

    original_connect = db.connect
    calls = 0
    tracked = []

    def connect():
        nonlocal calls
        calls += 1
        con = original_connect()
        if calls == 2:
            proxy = _TrackedGeometryConnection(con, fail_commit=fail_commit)
            tracked.append(proxy)
            return proxy
        return con

    monkeypatch.setattr(db, "connect", connect)
    return tracked


def test_put_explicitly_rolls_back_when_audit_fails(geometry_api, monkeypatch):
    from app import db

    tracked = _track_endpoint_connection(monkeypatch)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("geometry audit failed")

    monkeypatch.setattr(db, "audit", fail_audit)
    with pytest.raises(RuntimeError, match="geometry audit failed"):
        _put_geometry(
            geometry_api["client"], geometry_api["route_id"], "forward",
            geometry_api["forward"], 0,
        )

    assert tracked[0].rollback_calls == 1
    assert _stored_geometry(geometry_api["route_id"], "forward") is None
    assert _geometry_audits() == []


def test_put_explicitly_rolls_back_when_commit_fails(geometry_api, monkeypatch):
    tracked = _track_endpoint_connection(monkeypatch, fail_commit=True)

    with pytest.raises(RuntimeError, match="geometry commit failed"):
        _put_geometry(
            geometry_api["client"], geometry_api["route_id"], "forward",
            geometry_api["forward"], 0,
        )

    assert tracked[0].rollback_calls == 1
    assert _stored_geometry(geometry_api["route_id"], "forward") is None
    assert _geometry_audits() == []


def test_delete_explicitly_rolls_back_when_audit_fails(
    geometry_api, monkeypatch
):
    from app import db

    saved = _put_geometry(
        geometry_api["client"], geometry_api["route_id"], "forward",
        geometry_api["forward"], 0,
    )
    assert saved.status_code == 200, saved.text
    before = _stored_geometry(geometry_api["route_id"], "forward")
    audits_before = _geometry_audits()
    tracked = _track_endpoint_connection(monkeypatch)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("geometry audit failed")

    monkeypatch.setattr(db, "audit", fail_audit)
    with pytest.raises(RuntimeError, match="geometry audit failed"):
        _delete_geometry(
            geometry_api["client"], geometry_api["route_id"], "forward", 1
        )

    assert tracked[0].rollback_calls == 1
    assert _stored_geometry(geometry_api["route_id"], "forward") == before
    assert _geometry_audits() == audits_before


def test_save_propagates_unrelated_insert_integrity_error(tmp_path, monkeypatch):
    con = _open_route_db(tmp_path, monkeypatch)
    try:
        route_id = _insert_route(con)
        _insert_route_stop(con, route_id, 1, 30, 50)
        _insert_route_stop(con, route_id, 2, 31, 51)

        with pytest.raises(route_geometry.sqlite3.IntegrityError):
            route_geometry.save_geometry(
                con, route_id, "forward", _geometry([[30, 50], [31, 51]]),
                "manual", 0, None, "now",
            )
        assert get_geometry(con, route_id, "forward") is None
    finally:
        con.close()


def test_geometry_versions_do_not_repeat_after_delete_and_recreate(geometry_api):
    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    first = _put_geometry(
        client, route_id, "forward", geometry_api["forward"], 0
    )
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 1
    deleted = _delete_geometry(client, route_id, "forward", 1)
    assert deleted.status_code == 200, deleted.text

    recreated = _put_geometry(
        client, route_id, "forward", geometry_api["forward"], 0
    )

    assert recreated.status_code == 200, recreated.text
    assert recreated.json()["version"] == 2
    recreated_geometry = recreated.json()["geometry"]
    stale = _put_geometry(
        client,
        route_id,
        "forward",
        [geometry_api["forward"][0], [30.5, 50.5],
         geometry_api["forward"][1]],
        1,
    )
    assert stale.status_code == 409
    current = _stored_geometry(route_id, "forward")
    assert current["version"] == 2
    assert current["geometry"] == recreated_geometry


def test_geometry_write_lock_prevents_route_delete_interleaving(
    geometry_api, monkeypatch
):
    import threading
    import app.api_route_network as route_api
    from app import db

    entered = threading.Event()
    release = threading.Event()
    original_route_or_404 = route_api._route_or_404

    def paused_route_or_404(con, route_id):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test route lookup was not released")
        return original_route_or_404(con, route_id)

    monkeypatch.setattr(route_api, "_route_or_404", paused_route_or_404)
    result = {}

    def save_request():
        result["response"] = _put_geometry(
            geometry_api["client"], geometry_api["route_id"], "forward",
            geometry_api["forward"], 0,
        )

    worker = threading.Thread(target=save_request)
    worker.start()
    assert entered.wait(5)
    competing = route_geometry.sqlite3.connect(db.DB_PATH, timeout=0.05)
    competing.execute("PRAGMA foreign_keys=ON")
    try:
        with pytest.raises(route_geometry.sqlite3.OperationalError, match="locked"):
            competing.execute(
                "DELETE FROM routes WHERE id=?", (geometry_api["route_id"],)
            )
            competing.commit()
    finally:
        competing.rollback()
        competing.close()
        release.set()
        worker.join(5)

    assert not worker.is_alive()
    assert result["response"].status_code == 200, result["response"].text
    assert _stored_geometry(geometry_api["route_id"], "forward")["version"] == 1

def _create_geometry_stop(client, name, code, longitude, latitude):
    response = client.post("/api/stops", json={
        "name": name,
        "external_code": code,
        "longitude": longitude,
        "latitude": latitude,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _replace_trace(client, route_id, direction, stop_ids):
    response = client.put(
        f"/api/routes/{route_id}/stops/{direction}",
        json={"items": [
            {
                "stop_id": stop_id,
                "sequence": sequence,
                "distance_from_prev_km": 0 if sequence == 1 else 1,
            }
            for sequence, stop_id in enumerate(stop_ids, 1)
        ]},
    )
    assert response.status_code == 200, response.text
    return response


def test_partial_latitude_update_preserves_longitude_and_updates_anchor(geometry_api):
    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    assert _put_geometry(
        client, route_id, "forward", geometry_api["forward"], 0
    ).status_code == 200
    stop_id = client.get(
        f"/api/routes/{route_id}/network"
    ).json()["forward"][0]["stop"]["id"]

    response = client.put(f"/api/stops/{stop_id}", json={"latitude": 49.75})

    assert response.status_code == 200, response.text
    network = client.get(f"/api/routes/{route_id}/network").json()
    assert network["forward"][0]["stop"]["longitude"] == 30.0
    assert network["forward"][0]["stop"]["latitude"] == 49.75
    assert network["geometries"]["forward"]["geometry"]["coordinates"][0] == [
        30.0, 49.75
    ]
    assert network["geometries"]["forward"]["version"] == 2


def test_shared_stop_updates_every_stored_route_direction(geometry_api):
    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    network = client.get(f"/api/routes/{route_id}/network").json()
    shared_stop_id = network["forward"][0]["stop"]["id"]
    for direction in ("forward", "backward"):
        assert _put_geometry(
            client, route_id, direction, geometry_api[direction], 0
        ).status_code == 200

    second_route = client.post(
        "/api/refs/routes", json={"number": "G-API-2", "name": "Второй"}
    )
    assert second_route.status_code == 200, second_route.text
    second_route_id = second_route.json()["id"]
    other_stop_id = _create_geometry_stop(
        client, "Другая", "G-OTHER", 32.0, 52.0
    )
    _replace_trace(
        client, second_route_id, "forward", [shared_stop_id, other_stop_id]
    )
    assert _put_geometry(
        client, second_route_id, "forward", [[30.0, 50.0], [32.0, 52.0]], 0
    ).status_code == 200

    response = client.put(
        f"/api/stops/{shared_stop_id}", json={"latitude": 49.5}
    )

    assert response.status_code == 200, response.text
    assert _stored_geometry(route_id, "forward")["geometry"]["coordinates"][0] == [30.0, 49.5]
    assert _stored_geometry(route_id, "backward")["geometry"]["coordinates"][-1] == [30.0, 49.5]
    assert _stored_geometry(second_route_id, "forward")["geometry"]["coordinates"][0] == [30.0, 49.5]
    assert {
        _stored_geometry(route_id, "forward")["version"],
        _stored_geometry(route_id, "backward")["version"],
        _stored_geometry(second_route_id, "forward")["version"],
    } == {2}


def test_repeated_stop_updates_only_its_ordered_anchor_occurrences(geometry_api):
    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    network = client.get(f"/api/routes/{route_id}/network").json()
    target_stop_id = network["forward"][0]["stop"]["id"]
    final_stop_id = network["forward"][1]["stop"]["id"]
    middle_stop_id = _create_geometry_stop(
        client, "Средняя", "G-MIDDLE", 30.5, 50.5
    )
    _replace_trace(
        client,
        route_id,
        "forward",
        [target_stop_id, middle_stop_id, target_stop_id, final_stop_id],
    )
    coordinates = [
        [30.0, 50.0], [30.5, 50.5], [30.0, 50.0],
        [31.0, 51.0], [30.0, 50.0],
    ]
    assert _put_geometry(client, route_id, "forward", coordinates, 0).status_code == 200

    response = client.put(
        f"/api/stops/{target_stop_id}", json={"longitude": 29.75}
    )

    assert response.status_code == 200, response.text
    updated = _stored_geometry(route_id, "forward")["geometry"]["coordinates"]
    assert updated == [
        [29.75, 50.0], [30.5, 50.5], [29.75, 50.0],
        [31.0, 51.0], [30.0, 50.0],
    ]


def test_missing_coordinate_resets_affected_geometry_and_keeps_revision_monotonic(
    geometry_api,
):
    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    network = client.get(f"/api/routes/{route_id}/network").json()
    target_stop_id = network["forward"][0]["stop"]["id"]
    other_stop_id = network["forward"][1]["stop"]["id"]
    back_first = _create_geometry_stop(client, "Назад 1", "G-B1", 33.0, 53.0)
    back_second = _create_geometry_stop(client, "Назад 2", "G-B2", 34.0, 54.0)
    _replace_trace(client, route_id, "backward", [back_first, back_second])
    assert _put_geometry(
        client, route_id, "forward", geometry_api["forward"], 0
    ).status_code == 200
    assert _put_geometry(
        client, route_id, "backward", [[33.0, 53.0], [34.0, 54.0]], 0
    ).status_code == 200

    response = client.put(
        f"/api/stops/{target_stop_id}", json={"longitude": None}
    )

    assert response.status_code == 200, response.text
    assert _stored_geometry(route_id, "forward") is None
    assert _stored_geometry(route_id, "backward")["version"] == 1
    restored = client.put(
        f"/api/stops/{target_stop_id}", json={"longitude": 30.0}
    )
    assert restored.status_code == 200, restored.text
    recreated = _put_geometry(
        client, route_id, "forward", [[30.0, 50.0], [31.0, 51.0]], 0
    )
    assert recreated.status_code == 200, recreated.text
    assert recreated.json()["version"] == 2
    assert other_stop_id != target_stop_id


def test_unmappable_stored_anchor_resets_safely(geometry_api):
    from app import db

    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    assert _put_geometry(
        client, route_id, "forward", geometry_api["forward"], 0
    ).status_code == 200
    stop_id = client.get(
        f"/api/routes/{route_id}/network"
    ).json()["forward"][0]["stop"]["id"]
    con = db.connect()
    try:
        con.execute(
            "UPDATE route_geometries SET geometry_json=? "
            "WHERE route_id=? AND direction='forward'",
            (json.dumps(_geometry([[29.0, 49.0], [31.0, 51.0]])), route_id),
        )
        con.commit()
    finally:
        con.close()

    response = client.put(
        f"/api/stops/{stop_id}", json={"longitude": 30.25}
    )

    assert response.status_code == 200, response.text
    assert _stored_geometry(route_id, "forward") is None


def test_stop_update_without_coordinate_change_keeps_geometry_unchanged(geometry_api):
    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    assert _put_geometry(
        client, route_id, "forward", geometry_api["forward"], 0
    ).status_code == 200
    before = _stored_geometry(route_id, "forward")
    stop_id = client.get(
        f"/api/routes/{route_id}/network"
    ).json()["forward"][0]["stop"]["id"]

    response = client.put(
        f"/api/stops/{stop_id}", json={"name": "Новое имя", "latitude": 50.0}
    )

    assert response.status_code == 200, response.text
    assert _stored_geometry(route_id, "forward") == before


def test_replacing_forward_trace_resets_only_forward_geometry_with_safe_audit(
    geometry_api,
):
    from app import db

    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    for direction in ("forward", "backward"):
        assert _put_geometry(
            client, route_id, direction, geometry_api[direction], 0
        ).status_code == 200
    network = client.get(f"/api/routes/{route_id}/network").json()
    stop_ids = [row["stop"]["id"] for row in network["forward"]]

    _replace_trace(client, route_id, "forward", stop_ids)

    assert _stored_geometry(route_id, "forward") is None
    assert _stored_geometry(route_id, "backward")["version"] == 1
    con = db.connect()
    try:
        audit = dict(con.execute(
            "SELECT old_value,new_value FROM audit_log "
            "WHERE action='замена трассы маршрута' AND object_id=? "
            "ORDER BY id DESC LIMIT 1",
            (str(route_id),),
        ).fetchone())
    finally:
        con.close()
    old_value = json.loads(audit["old_value"])
    new_value = json.loads(audit["new_value"])
    assert old_value["geometry"] == {
        "direction": "forward", "source": "manual", "version": 1,
        "coordinates": 2,
    }
    assert new_value["geometry"] == {
        "direction": "forward", "source": None, "version": 0,
        "coordinates": 0,
    }
    assert "LineString" not in audit["old_value"]
    assert "LineString" not in audit["new_value"]


def test_failure_after_stop_geometry_sync_rolls_back_every_change(
    geometry_api, monkeypatch
):
    from app import api_route_network, db

    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    assert _put_geometry(
        client, route_id, "forward", geometry_api["forward"], 0
    ).status_code == 200
    network = client.get(f"/api/routes/{route_id}/network").json()
    stop_id = network["forward"][0]["stop"]["id"]
    before_geometry = _stored_geometry(route_id, "forward")
    con = db.connect()
    try:
        before_audits = con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        before_revision = con.execute(
            "SELECT last_version FROM route_geometry_revisions "
            "WHERE route_id=? AND direction='forward'", (route_id,)
        ).fetchone()[0]
    finally:
        con.close()

    def fail_audit(*args, **kwargs):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(api_route_network.db, "audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        client.put(f"/api/stops/{stop_id}", json={"longitude": 30.25})

    assert _stored_geometry(route_id, "forward") == before_geometry
    con = db.connect()
    try:
        stop = con.execute(
            "SELECT longitude,latitude FROM stops WHERE id=?", (stop_id,)
        ).fetchone()
        assert (stop["longitude"], stop["latitude"]) == (30.0, 50.0)
        assert con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before_audits
        assert con.execute(
            "SELECT last_version FROM route_geometry_revisions "
            "WHERE route_id=? AND direction='forward'", (route_id,)
        ).fetchone()[0] == before_revision
    finally:
        con.close()

def test_osrm_preview_is_stale_after_stop_coordinate_mutation(
    geometry_api, monkeypatch
):
    import app.osrm as osrm

    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    monkeypatch.setattr(osrm, "request_route", lambda coordinates, **kwargs: {
        "geometry": _geometry([list(point) for point in coordinates]),
        "legs": [{"distance": 1400, "duration": 120}],
    })
    preview = client.post(f"/api/routes/{route_id}/osrm/preview/forward")
    assert preview.status_code == 200, preview.text
    stop_id = client.get(
        f"/api/routes/{route_id}/network"
    ).json()["forward"][0]["stop"]["id"]
    moved = client.put(
        f"/api/stops/{stop_id}", json={"longitude": 30.25}
    )
    assert moved.status_code == 200, moved.text
    before_apply = client.get(f"/api/routes/{route_id}/network").json()

    response = client.post(
        f"/api/routes/{route_id}/osrm/apply/forward",
        json={
            "preview_token": preview.json()["preview_token"],
            "expected_geometry_version": 0,
        },
    )

    assert response.status_code == 409, response.text
    assert client.get(f"/api/routes/{route_id}/network").json() == before_apply

def test_malformed_stored_geometry_json_resets_safely(geometry_api):
    from app import db

    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    assert _put_geometry(
        client, route_id, "forward", geometry_api["forward"], 0
    ).status_code == 200
    stop_id = client.get(
        f"/api/routes/{route_id}/network"
    ).json()["forward"][0]["stop"]["id"]
    con = db.connect()
    try:
        con.execute(
            "UPDATE route_geometries SET geometry_json='not-json' "
            "WHERE route_id=? AND direction='forward'",
            (route_id,),
        )
        con.commit()
    finally:
        con.close()

    response = client.put(
        f"/api/stops/{stop_id}", json={"longitude": 30.25}
    )

    assert response.status_code == 200, response.text
    assert _stored_geometry(route_id, "forward") is None

def test_failure_after_trace_geometry_reset_rolls_back_trace_and_geometry(
    geometry_api, monkeypatch
):
    from app import api_route_network, db

    client = geometry_api["client"]
    route_id = geometry_api["route_id"]
    assert _put_geometry(
        client, route_id, "forward", geometry_api["forward"], 0
    ).status_code == 200
    before = client.get(f"/api/routes/{route_id}/network").json()
    stop_ids = [row["stop"]["id"] for row in before["forward"]]
    con = db.connect()
    try:
        before_revision = con.execute(
            "SELECT last_version FROM route_geometry_revisions "
            "WHERE route_id=? AND direction='forward'", (route_id,)
        ).fetchone()[0]
    finally:
        con.close()

    def fail_audit(*args, **kwargs):
        raise RuntimeError("injected trace audit failure")

    monkeypatch.setattr(api_route_network.db, "audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected trace audit failure"):
        client.put(
            f"/api/routes/{route_id}/stops/forward",
            json={"items": [
                {
                    "stop_id": stop_id,
                    "sequence": sequence,
                    "distance_from_prev_km": 0 if sequence == 1 else 1,
                }
                for sequence, stop_id in enumerate(stop_ids, 1)
            ]},
        )

    assert client.get(f"/api/routes/{route_id}/network").json() == before
    con = db.connect()
    try:
        assert con.execute(
            "SELECT last_version FROM route_geometry_revisions "
            "WHERE route_id=? AND direction='forward'", (route_id,)
        ).fetchone()[0] == before_revision
    finally:
        con.close()
