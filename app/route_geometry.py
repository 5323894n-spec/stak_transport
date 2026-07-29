# -*- coding: utf-8 -*-
"""Доменная валидация и чтение геометрии маршрута."""
import json
import math
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
    return (
        abs(first[0] - second[0]) <= ANCHOR_TOLERANCE
        and abs(first[1] - second[1]) <= ANCHOR_TOLERANCE
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
