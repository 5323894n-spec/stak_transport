# -*- coding: utf-8 -*-
import json
from decimal import Decimal

import pytest

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


def _open_route_db(tmp_path):
    from app import db

    db.DB_PATH = str(tmp_path / "route-geometry-api.db")
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


def test_validate_geometry_shape_enforces_serialized_byte_limit(monkeypatch):
    monkeypatch.setattr(route_geometry, "MAX_GEOMETRY_BYTES", 20)

    with pytest.raises(GeometryValidationError, match="2 МБ"):
        validate_geometry_shape(_geometry([[0, 0], [1, 1]]))


def test_stop_anchors_returns_longitude_latitude_in_sequence_order(tmp_path):
    con = _open_route_db(tmp_path)
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


def test_stop_anchors_rejects_too_few_stops(tmp_path):
    con = _open_route_db(tmp_path)
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
def test_stop_anchors_rejects_missing_coordinates(tmp_path, longitude, latitude):
    con = _open_route_db(tmp_path)
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


def test_get_geometry_returns_none_when_not_saved(tmp_path):
    con = _open_route_db(tmp_path)
    try:
        route_id = _insert_route(con)

        assert get_geometry(con, route_id, "forward") is None
    finally:
        con.close()


def test_geometry_record_and_get_geometry_serialize_public_fields(tmp_path):
    con = _open_route_db(tmp_path)
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
