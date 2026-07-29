# -*- coding: utf-8 -*-
"""Доменная валидация и чтение геометрии маршрута."""
import json
import math
import sqlite3
from array import array
from numbers import Number


DIRECTIONS = ("forward", "backward")
ANCHOR_TOLERANCE = 0.000001
MAX_COORDINATES = 20_000
MAX_GEOMETRY_BYTES = 2 * 1024 * 1024
MAX_OSRM_ASSIGNMENT_CELLS = 10_000_000


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


def ordered_anchor_indexes(coordinates, anchors):
    """Find geometry vertex indexes for ordered normalized stop anchors."""
    indexes = []
    coordinate_index = 0
    for anchor in anchors:
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
        indexes.append(coordinate_index)
        coordinate_index += 1
    return indexes


def _unique_ordered_anchor_indexes(coordinates, anchors):
    """Return the only full monotone anchor assignment or reject ambiguity."""
    earliest = ordered_anchor_indexes(coordinates, anchors)
    latest = [None] * len(anchors)
    coordinate_index = len(coordinates) - 1
    for anchor_index in range(len(anchors) - 1, -1, -1):
        anchor = anchors[anchor_index]
        while (
            coordinate_index >= 0
            and not _coordinates_match(coordinates[coordinate_index], anchor)
        ):
            coordinate_index -= 1
        if coordinate_index < 0:
            raise GeometryValidationError(
                "Геометрия не проходит через все остановки маршрута "
                "в заданном порядке."
            )
        latest[anchor_index] = coordinate_index
        coordinate_index -= 1
    if earliest != latest:
        raise GeometryValidationError(
            "Неоднозначная привязка геометрии к остановкам маршрута."
        )
    return earliest


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


def _squared_displacement(coordinate, anchor):
    return (
        (coordinate[0] - anchor[0]) ** 2
        + (coordinate[1] - anchor[1]) ** 2
    )


def _minimum_monotone_assignment(coordinates, anchors):
    coordinate_count = len(coordinates)
    anchor_count = len(anchors)
    if not anchors:
        return []
    if anchor_count == coordinate_count:
        return list(range(coordinate_count))
    if anchor_count == 1:
        return [min(
            range(coordinate_count),
            key=lambda index: _squared_displacement(coordinates[index], anchors[0]),
        )]

    cells = (anchor_count + 1) * (coordinate_count + 1)
    if cells > MAX_OSRM_ASSIGNMENT_CELLS:
        raise GeometryValidationError(
            "Геометрия OSRM слишком сложна для привязки к остановкам маршрута."
        )

    suffix_costs = [None] * (anchor_count + 1)
    suffix_costs[anchor_count] = array("d", [0.0]) * (coordinate_count + 1)
    for anchor_index in range(anchor_count - 1, -1, -1):
        row = array("d", [math.inf]) * (coordinate_count + 1)
        next_row = suffix_costs[anchor_index + 1]
        last_start = coordinate_count - (anchor_count - anchor_index)
        anchor = anchors[anchor_index]
        for coordinate_index in range(last_start, -1, -1):
            assigned = (
                _squared_displacement(
                    coordinates[coordinate_index], anchor
                )
                + next_row[coordinate_index + 1]
            )
            skipped = row[coordinate_index + 1]
            row[coordinate_index] = min(assigned, skipped)
        suffix_costs[anchor_index] = row

    assignment = []
    cursor = 0
    best_cost = suffix_costs[0][0]
    for anchor_index, anchor in enumerate(anchors):
        next_row = suffix_costs[anchor_index + 1]
        last_candidate = coordinate_count - (anchor_count - anchor_index)
        for coordinate_index in range(cursor, last_candidate + 1):
            assigned = (
                _squared_displacement(
                    coordinates[coordinate_index], anchor
                )
                + next_row[coordinate_index + 1]
            )
            if assigned == best_cost:
                assignment.append(coordinate_index)
                cursor = coordinate_index + 1
                best_cost = next_row[cursor]
                break
        else:
            raise GeometryValidationError(
                "Не удалось привязать геометрию OSRM к остановкам маршрута."
            )
    return assignment


def normalize_osrm_geometry(geometry, anchors):
    """Привязать геометрию OSRM к точным координатам остановок."""
    coordinates = validate_geometry_shape(geometry)
    normalized_anchors = [
        _normalize_coordinate(anchor, number)
        for number, anchor in enumerate(anchors, start=1)
    ]
    if len(coordinates) < len(normalized_anchors):
        raise GeometryValidationError(
            "Геометрия OSRM содержит меньше координат, чем остановок маршрута."
        )

    assignment = _minimum_monotone_assignment(
        coordinates, normalized_anchors
    )
    for coordinate_index, anchor in zip(assignment, normalized_anchors):
        coordinates[coordinate_index] = [float(anchor[0]), float(anchor[1])]

    return validate_geometry(
        {"type": "LineString", "coordinates": coordinates},
        normalized_anchors,
    )


def validate_geometry(geometry, anchors):
    """Проверить LineString и прохождение через остановки в заданном порядке."""
    coordinates = validate_geometry_shape(geometry)
    normalized_anchors = [
        _normalize_coordinate(anchor, number)
        for number, anchor in enumerate(anchors, start=1)
    ]

    ordered_anchor_indexes(coordinates, normalized_anchors)

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


def _concurrent_change_conflict():
    return GeometryVersionConflict(
        "Геометрия маршрута была изменена другим пользователем."
    )


def _advance_revision(con, route_id, direction):
    con.execute(
        """
        INSERT INTO route_geometry_revisions(route_id, direction, last_version)
        VALUES(?, ?, 1)
        ON CONFLICT(route_id, direction) DO UPDATE
        SET last_version=last_version + 1
        """,
        (route_id, direction),
    )
    return con.execute(
        """
        SELECT last_version
        FROM route_geometry_revisions
        WHERE route_id=? AND direction=?
        """,
        (route_id, direction),
    ).fetchone()["last_version"]


def _remember_revision(con, route_id, direction, version):
    con.execute(
        """
        INSERT INTO route_geometry_revisions(route_id, direction, last_version)
        VALUES(?, ?, ?)
        ON CONFLICT(route_id, direction) DO UPDATE
        SET last_version=MAX(last_version, excluded.last_version)
        """,
        (route_id, direction, version),
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
    if current:
        new_version = actual_version + 1
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
            raise _concurrent_change_conflict()
        _remember_revision(con, route_id, direction, new_version)
    else:
        new_version = _advance_revision(con, route_id, direction)
        try:
            con.execute(
                """
                INSERT INTO route_geometries(
                  route_id, direction, geometry_json, source, version,
                  updated_by, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    route_id,
                    direction,
                    serialized,
                    source,
                    new_version,
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


def _delete_geometry_row(con, route_id, direction, current):
    cursor = con.execute(
        "DELETE FROM route_geometries WHERE id=? AND version=?",
        (current["id"], current["version"]),
    )
    if cursor.rowcount != 1:
        raise _concurrent_change_conflict()
    _remember_revision(con, route_id, direction, current["version"])


def _stored_coordinate_count(geometry_json):
    try:
        geometry = json.loads(geometry_json)
    except (json.JSONDecodeError, TypeError):
        return 0
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        return 0
    coordinates = geometry.get("coordinates")
    return len(coordinates) if isinstance(coordinates, list) else 0


def reset_stored_geometry(con, route_id, direction):
    """Delete raw stored geometry and return sanitized metadata without commit."""
    _validate_direction(direction)
    current = _current_geometry_row(con, route_id, direction)
    if current is None:
        return None
    geometry_json = current["geometry_json"]
    summary = {
        "source": current["source"],
        "version": current["version"],
    }
    _delete_geometry_row(con, route_id, direction, current)
    summary["coordinates"] = _stored_coordinate_count(geometry_json)
    return summary


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
    _delete_geometry_row(con, route_id, direction, current)
    return old_record


def _synchronization_summary(route_id, direction, record):
    if record is None:
        return {
            "route_id": route_id,
            "direction": direction,
            "source": None,
            "version": 0,
            "coordinates": 0,
        }
    geometry = record.get("geometry")
    coordinates = (
        geometry.get("coordinates") if isinstance(geometry, dict) else None
    )
    return {
        "route_id": route_id,
        "direction": direction,
        "source": record["source"],
        "version": record["version"],
        "coordinates": len(coordinates) if isinstance(coordinates, list) else 0,
    }


def synchronize_stop_anchor(
    con, stop_id, old_coordinate, new_coordinate, username, timestamp
):
    """Align stored geometries after a stop coordinate update, without commit."""
    affected = con.execute(
        """
        SELECT DISTINCT rg.route_id, rg.direction
        FROM route_geometries AS rg
        JOIN route_stops AS rs
          ON rs.route_id=rg.route_id AND rs.direction=rg.direction
        WHERE rs.stop_id=?
        ORDER BY rg.route_id, rg.direction
        """,
        (stop_id,),
    ).fetchall()
    changes = []
    coordinates_complete = (
        old_coordinate is not None
        and new_coordinate is not None
        and len(old_coordinate) == 2
        and len(new_coordinate) == 2
        and None not in old_coordinate
        and None not in new_coordinate
    )

    for affected_row in affected:
        route_id = affected_row["route_id"]
        direction = affected_row["direction"]
        current_row = _current_geometry_row(con, route_id, direction)
        if current_row is None:
            continue
        try:
            current = geometry_record(current_row)
        except (TypeError, ValueError):
            old_summary = {
                "route_id": route_id,
                "direction": direction,
                "source": current_row["source"],
                "version": current_row["version"],
                "coordinates": 0,
            }
            _delete_geometry_row(con, route_id, direction, current_row)
            changes.append({
                "old": old_summary,
                "new": _synchronization_summary(route_id, direction, None),
            })
            continue
        old_summary = _synchronization_summary(route_id, direction, current)
        if not coordinates_complete:
            delete_geometry(con, route_id, direction, current["version"])
            changes.append({
                "old": old_summary,
                "new": _synchronization_summary(route_id, direction, None),
            })
            continue

        try:
            old_target = _normalize_coordinate(old_coordinate, 1)
            new_target = _normalize_coordinate(new_coordinate, 1)
            route_rows = con.execute(
                """
                SELECT rs.stop_id, s.longitude, s.latitude
                FROM route_stops AS rs
                JOIN stops AS s ON s.id=rs.stop_id
                WHERE rs.route_id=? AND rs.direction=?
                ORDER BY rs.sequence
                """,
                (route_id, direction),
            ).fetchall()
            old_anchors = []
            target_anchor_indexes = []
            for anchor_number, route_row in enumerate(route_rows, start=1):
                if route_row["stop_id"] == stop_id:
                    target_anchor_indexes.append(anchor_number - 1)
                    old_anchors.append(old_target)
                else:
                    old_anchors.append(_normalize_coordinate(
                        (route_row["longitude"], route_row["latitude"]),
                        anchor_number,
                    ))
            geometry_coordinates = validate_geometry_shape(current["geometry"])
            vertex_indexes = _unique_ordered_anchor_indexes(
                geometry_coordinates, old_anchors
            )
            for anchor_index in target_anchor_indexes:
                vertex_index = vertex_indexes[anchor_index]
                geometry_coordinates[vertex_index] = list(new_target)
            _, saved = save_geometry(
                con,
                route_id,
                direction,
                {"type": "LineString", "coordinates": geometry_coordinates},
                "manual",
                current["version"],
                username,
                timestamp,
            )
        except GeometryValidationError:
            delete_geometry(con, route_id, direction, current["version"])
            saved = None
        changes.append({
            "old": old_summary,
            "new": _synchronization_summary(route_id, direction, saved),
        })
    return changes
