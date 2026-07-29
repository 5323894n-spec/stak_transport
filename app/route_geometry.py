# -*- coding: utf-8 -*-
"""Доменная валидация и чтение геометрии маршрута."""
import json
import math
import sqlite3
from numbers import Number


DIRECTIONS = ("forward", "backward")
ANCHOR_TOLERANCE = 0.000001
MAX_COORDINATES = 20_000
MAX_GEOMETRY_BYTES = 2 * 1024 * 1024


class GeometryValidationError(ValueError):
    """Геометрия маршрута не соответствует требованиям."""


class GeometryVersionConflict(ValueError):
    """Версия геометрии была изменена другим пользователем."""


def _normalize_coordinate(point, number):
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise GeometryValidationError(
            f"Координата №{number} должна состоять ровно из двух чисел."
        )

    values = []
    for value in point:
        if isinstance(value, bool) or not isinstance(value, Number):
            raise GeometryValidationError(
                f"Координата №{number} должна содержать только числа."
            )
        try:
            normalized = float(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise GeometryValidationError(
                f"Координата №{number} должна содержать только числа."
            ) from exc
        if not math.isfinite(normalized):
            raise GeometryValidationError(
                f"Координата №{number} должна содержать конечные числа."
            )
        values.append(normalized)

    longitude, latitude = values
    if not -180 <= longitude <= 180:
        raise GeometryValidationError(
            f"Долгота в координате №{number} должна быть от -180 до 180."
        )
    if not -90 <= latitude <= 90:
        raise GeometryValidationError(
            f"Широта в координате №{number} должна быть от -90 до 90."
        )
    return [longitude, latitude]


def _coordinates_match(first, second):
    longitude_tolerance = ANCHOR_TOLERANCE + max(
        math.ulp(first[0]), math.ulp(second[0])
    )
    latitude_tolerance = ANCHOR_TOLERANCE + max(
        math.ulp(first[1]), math.ulp(second[1])
    )
    return (
        abs(first[0] - second[0]) <= longitude_tolerance
        and abs(first[1] - second[1]) <= latitude_tolerance
    )


def validate_geometry_shape(geometry):
    """Проверить структуру LineString и вернуть нормализованные координаты."""
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        raise GeometryValidationError(
            "Геометрия должна быть объектом GeoJSON типа LineString."
        )

    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list):
        raise GeometryValidationError(
            "Координаты GeoJSON LineString должны быть списком."
        )
    if len(coordinates) < 2:
        raise GeometryValidationError(
            "Геометрия маршрута должна содержать не менее двух координат."
        )
    if len(coordinates) > MAX_COORDINATES:
        raise GeometryValidationError(
            f"Геометрия маршрута не должна содержать более {MAX_COORDINATES} координат."
        )

    normalized = []
    for number, point in enumerate(coordinates, start=1):
        current = _normalize_coordinate(point, number)
        if normalized and _coordinates_match(normalized[-1], current):
            raise GeometryValidationError(
                f"Соседние координаты №{number - 1} и №{number} совпадают."
            )
        normalized.append(current)

    serialized = json.dumps(
        {"type": "LineString", "coordinates": normalized},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(serialized) > MAX_GEOMETRY_BYTES:
        raise GeometryValidationError(
            "Размер геометрии маршрута не должен превышать 2 МБ."
        )
    return normalized


def validate_geometry(geometry, anchors):
    """Проверить LineString и прохождение через остановки в заданном порядке."""
    coordinates = validate_geometry_shape(geometry)
    normalized_anchors = [
        _normalize_coordinate(anchor, number)
        for number, anchor in enumerate(anchors, start=1)
    ]

    coordinate_index = 0
    for anchor in normalized_anchors:
        while (
            coordinate_index < len(coordinates)
            and not _coordinates_match(coordinates[coordinate_index], anchor)
        ):
            coordinate_index += 1
        if coordinate_index == len(coordinates):
            raise GeometryValidationError(
                "Геометрия не проходит через все остановки маршрута "
                "в заданном порядке."
            )
        coordinate_index += 1

    return {"type": "LineString", "coordinates": coordinates}


def stop_anchors(con, route_id, direction):
    """Вернуть координаты остановок маршрута в порядке следования."""
    rows = con.execute(
        """
        SELECT s.longitude, s.latitude
        FROM route_stops AS rs
        JOIN stops AS s ON s.id=rs.stop_id
        WHERE rs.route_id=? AND rs.direction=?
        ORDER BY rs.sequence
        """,
        (route_id, direction),
    ).fetchall()
    if len(rows) < 2:
        raise GeometryValidationError(
            "Для геометрии маршрута требуется не менее двух остановок."
        )

    anchors = []
    for row in rows:
        if row["longitude"] is None or row["latitude"] is None:
            raise GeometryValidationError(
                "Для всех остановок маршрута должны быть заданы координаты."
            )
        try:
            anchors.append((float(row["longitude"]), float(row["latitude"])))
        except (OverflowError, TypeError, ValueError) as exc:
            raise GeometryValidationError(
                "Координаты остановок маршрута должны быть числами."
            ) from exc
    return anchors


def geometry_record(row):
    """Преобразовать строку route_geometries в публичное представление."""
    if row is None:
        return None
    return {
        "geometry": json.loads(row["geometry_json"]),
        "source": row["source"],
        "version": row["version"],
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


def get_geometry(con, route_id, direction):
    """Прочитать сохранённую геометрию направления маршрута."""
    row = con.execute(
        """
        SELECT geometry_json, source, version, updated_by, updated_at
        FROM route_geometries
        WHERE route_id=? AND direction=?
        """,
        (route_id, direction),
    ).fetchone()
    return geometry_record(row)


def _validate_direction(direction):
    if direction not in DIRECTIONS:
        raise GeometryValidationError(
            "Направление должно быть forward или backward."
        )


def _validate_expected_version(expected_version):
    if (
        isinstance(expected_version, bool)
        or not isinstance(expected_version, int)
        or expected_version < 0
    ):
        raise GeometryValidationError(
            "Ожидаемая версия геометрии должна быть целым неотрицательным числом."
        )


def _current_geometry_row(con, route_id, direction):
    return con.execute(
        """
        SELECT id, geometry_json, source, version, updated_by, updated_at
        FROM route_geometries
        WHERE route_id=? AND direction=?
        """,
        (route_id, direction),
    ).fetchone()


def _version_conflict(expected_version, actual_version):
    return GeometryVersionConflict(
        "Версия геометрии изменилась: "
        f"ожидалась {expected_version}, текущая {actual_version}."
    )


def save_geometry(
    con,
    route_id,
    direction,
    geometry,
    source,
    expected_version,
    username,
    timestamp,
):
    """Сохранить геометрию с оптимистичной блокировкой без commit."""
    _validate_direction(direction)
    if source not in ("manual", "osrm"):
        raise GeometryValidationError(
            "Источник геометрии должен быть manual или osrm."
        )
    _validate_expected_version(expected_version)
    normalized = validate_geometry(
        geometry, stop_anchors(con, route_id, direction)
    )
    current = _current_geometry_row(con, route_id, direction)
    actual_version = current["version"] if current else 0
    if expected_version != actual_version:
        raise _version_conflict(expected_version, actual_version)

    serialized = json.dumps(
        normalized, ensure_ascii=False, separators=(",", ":")
    )
    old_record = geometry_record(current)
    new_version = actual_version + 1
    if current:
        cursor = con.execute(
            """
            UPDATE route_geometries
            SET geometry_json=?, source=?, version=?, updated_by=?, updated_at=?
            WHERE id=? AND version=?
            """,
            (
                serialized,
                source,
                new_version,
                username,
                timestamp,
                current["id"],
                actual_version,
            ),
        )
        if cursor.rowcount != 1:
            raise _version_conflict(expected_version, actual_version)
    else:
        try:
            con.execute(
                """
                INSERT INTO route_geometries(
                  route_id, direction, geometry_json, source, version,
                  updated_by, created_at, updated_at
                ) VALUES(?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    route_id,
                    direction,
                    serialized,
                    source,
                    username,
                    timestamp,
                    timestamp,
                ),
            )
        except sqlite3.IntegrityError as exc:
            concurrent = _current_geometry_row(con, route_id, direction)
            if concurrent is not None:
                raise _version_conflict(
                    expected_version, concurrent["version"]
                ) from exc
            raise

    return old_record, get_geometry(con, route_id, direction)


def delete_geometry(con, route_id, direction, expected_version):
    """Удалить геометрию с оптимистичной блокировкой без commit."""
    _validate_direction(direction)
    _validate_expected_version(expected_version)
    current = _current_geometry_row(con, route_id, direction)
    actual_version = current["version"] if current else 0
    if expected_version != actual_version:
        raise _version_conflict(expected_version, actual_version)
    if current is None:
        return None

    old_record = geometry_record(current)
    cursor = con.execute(
        "DELETE FROM route_geometries WHERE id=? AND version=?",
        (current["id"], actual_version),
    )
    if cursor.rowcount != 1:
        raise _version_conflict(expected_version, actual_version)
    return old_record
