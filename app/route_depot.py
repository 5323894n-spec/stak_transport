# -*- coding: utf-8 -*-
"""Доменная логика парковых рейсов маршрута."""

import datetime
import json
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from . import db


DIRECTIONS = ("depot_out", "depot_in")
_SQLITE_INTEGER_MAX = 2**63 - 1


class DepotNotFoundError(ValueError):
    """Указанный маршрут или остановка отсутствует."""


def validate_direction(direction):
    if direction not in DIRECTIONS:
        raise ValueError("Направление должно быть depot_out или depot_in")
    return direction


def _decimal(value, label):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} должно быть числом")
    try:
        result = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} должно быть числом") from None
    if not result.is_finite():
        raise ValueError(f"{label} должно быть конечным числом")
    return result


def _positive_integer(value, label):
    number = _decimal(value, label)
    if number != number.to_integral_value() or number <= 0:
        raise ValueError(f"{label} должно быть положительным целым числом")
    result = int(number)
    if result > _SQLITE_INTEGER_MAX:
        raise ValueError(f"{label} должно быть целым числом в допустимом диапазоне")
    return result


def _nonnegative_integer(value, label):
    number = _decimal(value, label)
    if number < 0:
        raise ValueError(f"{label} не может быть отрицательным")
    if number != number.to_integral_value():
        raise ValueError(f"{label} должно быть целым числом")
    result = int(number)
    if result > _SQLITE_INTEGER_MAX:
        raise ValueError(f"{label} должно быть целым числом в допустимом диапазоне")
    return result


def _nonnegative_distance(value):
    number = _decimal(value, "Расстояние")
    if number < 0:
        raise ValueError("Расстояние не может быть отрицательным")
    result = float(number)
    if not math.isfinite(result):
        raise ValueError("Расстояние должно быть конечным числом")
    return round(result, 3)


def _next_cumulative_km(cumulative_km, distance):
    result = cumulative_km + distance
    if not math.isfinite(result):
        raise ValueError("Накопленное расстояние должно быть конечным числом")
    return round(result, 3)


def _safe_legacy_distance(value):
    try:
        return _nonnegative_distance(0 if value in (None, "") else value)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _legacy_distance(item):
    value = item.get("distance_km")
    if value is None and item.get("distance_m") is not None:
        try:
            value = _decimal(item["distance_m"], "Расстояние") / Decimal(1000)
        except ValueError:
            return 0.0
    return _safe_legacy_distance(value)


def normalize_items(items):
    """Проверить, упорядочить и дополнить строки накопительными итогами."""
    if not isinstance(items, list):
        raise ValueError("Поле items должно быть списком")
    normalized = []
    for source_item in items:
        if not isinstance(source_item, dict):
            raise ValueError("Каждая остановка должна быть объектом")
        source_detail = source_item.get("source_detail")
        if source_detail is not None and not isinstance(source_detail, str):
            raise ValueError("Поле source_detail должно быть строкой или null")
        item = {
            "stop_id": _positive_integer(source_item.get("stop_id"), "ID остановки"),
            "sequence": _positive_integer(source_item.get("sequence"), "Порядковый номер"),
            "distance_from_prev_km": _nonnegative_distance(
                source_item.get("distance_from_prev_km", 0)
            ),
            "run_time_day_sec": _nonnegative_integer(
                source_item.get("run_time_day_sec", 0), "Дневное время"
            ),
            "run_time_night_sec": _nonnegative_integer(
                source_item.get("run_time_night_sec", 0), "Ночное время"
            ),
            "source_detail": source_detail,
        }
        normalized.append(item)

    normalized.sort(key=lambda item: item["sequence"])
    sequences = [item["sequence"] for item in normalized]
    if sequences != list(range(1, len(normalized) + 1)):
        raise ValueError(
            "Последовательность остановок должна начинаться с 1 и не иметь пропусков"
        )

    cumulative_km = 0.0
    cumulative_day_sec = 0
    cumulative_night_sec = 0
    for item in normalized:
        cumulative_km = _next_cumulative_km(cumulative_km, item["distance_from_prev_km"])
        cumulative_day_sec += item["run_time_day_sec"]
        cumulative_night_sec += item["run_time_night_sec"]
        if cumulative_day_sec > _SQLITE_INTEGER_MAX:
            raise ValueError(
                "Накопленное дневное время должно быть целым числом в допустимом диапазоне"
            )
        if cumulative_night_sec > _SQLITE_INTEGER_MAX:
            raise ValueError(
                "Накопленное ночное время должно быть целым числом в допустимом диапазоне"
            )
        item["cumulative_km"] = cumulative_km
        item["cumulative_day_sec"] = cumulative_day_sec
        item["cumulative_night_sec"] = cumulative_night_sec
    return normalized


def _stop_from_joined(row):
    return {
        "id": row["stop_id"],
        "external_code": row.pop("external_code"),
        "name": row.pop("name"),
        "latitude": row.pop("latitude"),
        "longitude": row.pop("longitude"),
        "address": row.pop("address"),
        "stop_kind": row.pop("stop_kind"),
        "is_terminal": row.pop("is_terminal"),
        "has_dispatcher": row.pop("has_dispatcher"),
        "municipality": row.pop("municipality"),
        "registry_flags": row.pop("registry_flags"),
        "source": row.pop("stop_source"),
        "active": row.pop("active"),
        "notes": row.pop("stop_notes"),
    }


def _with_cumulative(rows):
    cumulative_km = 0.0
    cumulative_day_sec = 0
    cumulative_night_sec = 0
    for row in rows:
        distance = _safe_legacy_distance(row.get("distance_from_prev_km"))
        try:
            cumulative_km = _next_cumulative_km(cumulative_km, distance)
        except ValueError:
            distance = 0.0
        row["distance_from_prev_km"] = distance
        cumulative_day_sec += int(row["run_time_day_sec"] or 0)
        cumulative_night_sec += int(row["run_time_night_sec"] or 0)
        if not math.isfinite(cumulative_km):
            cumulative_km = 0.0
        row["cumulative_km"] = cumulative_km
        row["cumulative_day_sec"] = cumulative_day_sec
        row["cumulative_night_sec"] = cumulative_night_sec
    return rows


def _stored_rows(con, route_id, direction):
    rows = db.rows(con.execute(
        "SELECT rds.*, s.external_code, s.name, s.latitude, s.longitude, s.address, "
        "s.stop_kind, s.is_terminal, s.has_dispatcher, s.municipality, "
        "s.registry_flags, s.source AS stop_source, s.active, s.notes AS stop_notes "
        "FROM route_depot_stops rds JOIN stops s ON s.id=rds.stop_id "
        "WHERE rds.route_id=? AND rds.direction=? ORDER BY rds.sequence",
        (route_id, direction),
    ))
    normalized = normalize_items(rows)
    for row, metrics in zip(rows, normalized):
        for field in (
            "distance_from_prev_km", "run_time_day_sec", "run_time_night_sec",
            "cumulative_km", "cumulative_day_sec", "cumulative_night_sec",
        ):
            row[field] = metrics[field]
        row["stop"] = _stop_from_joined(row)
    return rows


def _travel_seconds(value):
    if value in (None, ""):
        return 0
    text = str(value).strip()
    if not text:
        return 0
    parts = text.split(":")
    if len(parts) == 3:
        try:
            hours = _nonnegative_integer(parts[0], "Часы")
            minutes = _nonnegative_integer(parts[1], "Минуты")
            seconds = _nonnegative_integer(parts[2], "Секунды")
            if minutes > 59 or seconds > 59:
                return 0
            total = hours * 3600 + minutes * 60 + seconds
            return total if total <= _SQLITE_INTEGER_MAX else 0
        except (TypeError, ValueError, OverflowError):
            return 0
    try:
        number = _decimal(text, "Время")
        if number < 0:
            return 0
        seconds = (number * Decimal(60)).to_integral_value(rounding=ROUND_HALF_UP)
        if seconds > _SQLITE_INTEGER_MAX:
            return 0
        return int(seconds)
    except (TypeError, ValueError, OverflowError):
        return 0


def _normalized_identity_text(value):
    return " ".join(str(value or "").strip().casefold().split())


def _item_value(item, primary, fallback):
    value = item.get(primary)
    if value is None or (isinstance(value, str) and not value.strip()):
        value = item.get(fallback)
    return value


def _coordinate_pair(item):
    try:
        latitude = float(_item_value(item, "latitude", "lat"))
        longitude = float(_item_value(item, "longitude", "lon"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        return None
    return latitude, longitude


def _add_stop_to_index(index, row):
    external_code = str(row.get("external_code") or "").strip()
    if external_code:
        index["external"].setdefault(external_code, []).append(row)
    normalized_name = _normalized_identity_text(row.get("name"))
    if normalized_name:
        index["names"].setdefault(normalized_name, []).append(row)


def _build_stop_index(con):
    index = {"external": {}, "names": {}}
    for row in db.rows(con.execute("SELECT * FROM stops ORDER BY id")):
        _add_stop_to_index(index, row)
    return index


def _item_address(item):
    return _normalized_identity_text(_item_value(item, "address", "street"))


def _match_indexed_stop(item, index):
    external_code = str(
        item.get("stop_id") or item.get("external_code") or ""
    ).strip()
    if external_code:
        external_matches = index["external"].get(external_code, [])
        if external_matches:
            return external_matches[0]

    name = str(item.get("stop_name") or item.get("name") or "").strip()
    candidates = list(index["names"].get(_normalized_identity_text(name), []))
    if not candidates:
        return None

    address = _item_address(item)
    if address:
        candidates = [
            row for row in candidates
            if _normalized_identity_text(row.get("address")) == address
        ]
        if not candidates:
            return None

    coordinates = _coordinate_pair(item)
    if coordinates is not None:
        latitude, longitude = coordinates
        coordinate_matches = []
        for row in candidates:
            candidate_coordinates = _coordinate_pair(row)
            if candidate_coordinates is None:
                continue
            candidate_latitude, candidate_longitude = candidate_coordinates
            if (
                abs(candidate_latitude - latitude) <= 0.000001
                and abs(candidate_longitude - longitude) <= 0.000001
            ):
                coordinate_matches.append(row)
        candidates = coordinate_matches
        if not candidates:
            return None
    return candidates[0] if len(candidates) == 1 else None


def _stop_by_legacy_identity(con, item, index=None):
    index = index or _build_stop_index(con)
    row = _match_indexed_stop(item, index)
    external_code = str(
        item.get("stop_id") or item.get("external_code") or ""
    ).strip()
    name = str(item.get("stop_name") or item.get("name") or "").strip()
    if row:
        return {
            "id": row["id"],
            "external_code": row["external_code"],
            "name": row["name"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "address": row["address"],
            "stop_kind": row["stop_kind"],
            "is_terminal": row["is_terminal"],
            "has_dispatcher": row["has_dispatcher"],
            "municipality": row["municipality"],
            "registry_flags": row["registry_flags"],
            "source": row["source"],
            "active": row["active"],
            "notes": row["notes"],
        }
    return {
        "id": None,
        "external_code": external_code or None,
        "name": name,
        "latitude": _item_value(item, "latitude", "lat"),
        "longitude": _item_value(item, "longitude", "lon"),
        "address": _item_value(item, "address", "street"),
        "stop_kind": "обычная",
        "is_terminal": 0,
        "has_dispatcher": 0,
        "municipality": None,
        "registry_flags": "{}",
        "source": "legacy_erm",
        "active": 1,
        "notes": None,
    }


def _resolve_or_create_erm_stop(con, item, index):
    stop = _stop_by_legacy_identity(con, item, index)
    if stop["id"] is not None:
        return stop["id"]

    name = str(item.get("stop_name") or "").strip()
    if not name:
        raise ValueError("В ЭРМ указана остановка без наименования")
    coordinates = _coordinate_pair(item)
    latitude, longitude = coordinates if coordinates is not None else (None, None)
    address = _item_value(item, "address", "street")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    cursor = con.execute(
        "INSERT INTO stops(external_code,name,latitude,longitude,address,source,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (
            stop.get("external_code"),
            name,
            latitude,
            longitude,
            address,
            "erm_import",
            now,
            now,
        ),
    )
    _add_stop_to_index(index, {
        "id": cursor.lastrowid,
        "external_code": stop.get("external_code"),
        "name": name,
        "latitude": latitude,
        "longitude": longitude,
        "address": address,
        "stop_kind": "обычная",
        "is_terminal": 0,
        "has_dispatcher": 0,
        "municipality": None,
        "registry_flags": "{}",
        "source": "erm_import",
        "active": 1,
        "notes": None,
        "created_at": now,
        "updated_at": now,
    })
    return cursor.lastrowid


def normalize_erm_depot_sections(con, route_id, details):
    """Persist all ERM depot page sections as one sequence per direction."""
    sheets = details.get("sheets") if isinstance(details, dict) else None
    if not isinstance(sheets, dict):
        return {"depot_out": 0, "depot_in": 0}

    direction_by_kind = {"из парка": "depot_out", "в парк": "depot_in"}
    items_by_direction = {direction: [] for direction in DIRECTIONS}
    stop_index = _build_stop_index(con)
    present = set()
    for sheet_name, sheet in sheets.items():
        sections = sheet.get("sections") if isinstance(sheet, dict) else None
        if not isinstance(sections, list):
            continue
        for section_index, section in enumerate(sections, start=1):
            if not isinstance(section, dict):
                continue
            kind = str(section.get("kind") or sheet_name).strip().casefold()
            direction = direction_by_kind.get(kind)
            if direction is None:
                continue
            stops = section.get("stops")
            if not isinstance(stops, list):
                continue
            for item in stops:
                if not isinstance(item, dict):
                    continue
                rows = items_by_direction[direction]
                runtime = _travel_seconds(item.get("travel_time"))
                rows.append({
                    "stop_id": _resolve_or_create_erm_stop(con, item, stop_index),
                    "sequence": len(rows) + 1,
                    "distance_from_prev_km": _legacy_distance(item),
                    "run_time_day_sec": runtime,
                    "run_time_night_sec": runtime,
                    "source_detail": json.dumps({
                        "sheet": sheet_name,
                        "section": section_index,
                        "header_row": section.get("header_row"),
                        "row": item.get("row"),
                    }, ensure_ascii=False),
                })
                present.add(direction)

    result = {"depot_out": 0, "depot_in": 0}
    for direction in DIRECTIONS:
        if direction not in present:
            continue
        rows = items_by_direction[direction]
        replace_depot_rows(con, route_id, direction, rows, source="erm_import")
        result[direction] = len(rows)
    return result


def _legacy_rows(con, route, direction):
    try:
        notes = json.loads(route.get("notes") or "{}")
    except (TypeError, json.JSONDecodeError):
        return []
    details = notes.get("details") if isinstance(notes, dict) else None
    sheets = details.get("sheets") if isinstance(details, dict) else None
    if not isinstance(sheets, dict):
        return []
    expected_title = "из парка" if direction == "depot_out" else "в парк"
    sheet_name = next(
        (name for name in sheets if str(name).strip().casefold() == expected_title), None
    )
    sheet = sheets.get(sheet_name) if sheet_name is not None else None
    sections = sheet.get("sections") if isinstance(sheet, dict) else None
    if not isinstance(sections, list):
        return []
    stop_index = _build_stop_index(con)
    rows = []
    for section_index, section in enumerate(sections, start=1):
        stops = section.get("stops") if isinstance(section, dict) else None
        if not isinstance(stops, list):
            continue
        for item in stops:
            if not isinstance(item, dict):
                continue
            stop = _stop_by_legacy_identity(con, item, stop_index)
            distance = _legacy_distance(item)
            runtime = _travel_seconds(item.get("travel_time"))
            rows.append({
                "id": None,
                "route_id": route["id"],
                "direction": direction,
                "stop_id": stop["id"],
                "sequence": len(rows) + 1,
                "distance_from_prev_km": distance,
                "run_time_day_sec": runtime,
                "run_time_night_sec": runtime,
                "source": "legacy_erm",
                "source_detail": json.dumps(
                    {"sheet": sheet_name, "section": section_index}, ensure_ascii=False
                ),
                "created_at": None,
                "updated_at": None,
                "stop": stop,
            })
    return _with_cumulative(rows)


def get_depot_rows(con, route_id, direction, legacy_fallback=True):
    validate_direction(direction)
    route = db.one(con.execute("SELECT * FROM routes WHERE id=?", (route_id,)))
    if not route:
        raise DepotNotFoundError("Маршрут не найден")
    rows = _stored_rows(con, route_id, direction)
    if rows or not legacy_fallback:
        return rows
    initialized = con.execute(
        "SELECT 1 FROM route_depot_section_state "
        "WHERE route_id=? AND direction=?",
        (route_id, direction),
    ).fetchone()
    if initialized:
        return []
    return _legacy_rows(con, route, direction)


def replace_depot_rows(con, route_id, direction, items, source="manual"):
    """Атомарно подготовить замену; фиксацией транзакции управляет вызывающий код."""
    validate_direction(direction)
    normalized = normalize_items(items)
    if not con.execute("SELECT 1 FROM routes WHERE id=?", (route_id,)).fetchone():
        raise DepotNotFoundError("Маршрут не найден")
    for item in normalized:
        if not con.execute("SELECT 1 FROM stops WHERE id=?", (item["stop_id"],)).fetchone():
            raise DepotNotFoundError(f"Остановка {item['stop_id']} не найдена")

    con.execute(
        "DELETE FROM route_depot_stops WHERE route_id=? AND direction=?",
        (route_id, direction),
    )
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    for item in normalized:
        con.execute(
            "INSERT INTO route_depot_stops("
            "route_id,direction,stop_id,sequence,distance_from_prev_km,"
            "run_time_day_sec,run_time_night_sec,source,source_detail,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                route_id, direction, item["stop_id"], item["sequence"],
                item["distance_from_prev_km"], item["run_time_day_sec"],
                item["run_time_night_sec"], str(source or "manual"),
                item.get("source_detail"), timestamp, timestamp,
            ),
        )
    con.execute(
        "INSERT INTO route_depot_section_state(route_id,direction,updated_at) "
        "VALUES(?,?,?) ON CONFLICT(route_id,direction) DO UPDATE SET updated_at=excluded.updated_at",
        (route_id, direction, timestamp),
    )
    return get_depot_rows(con, route_id, direction, legacy_fallback=False)
