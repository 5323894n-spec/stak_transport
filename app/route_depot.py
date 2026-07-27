# -*- coding: utf-8 -*-
"""Доменная логика парковых рейсов маршрута."""

import datetime
import json
import math
from decimal import Decimal, InvalidOperation

from . import db


DIRECTIONS = ("depot_out", "depot_in")


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
    return int(number)


def _nonnegative_integer(value, label):
    number = _decimal(value, label)
    if number < 0:
        raise ValueError(f"{label} не может быть отрицательным")
    if number != number.to_integral_value():
        raise ValueError(f"{label} должно быть целым числом")
    return int(number)


def _nonnegative_distance(value):
    number = _decimal(value, "Расстояние")
    if number < 0:
        raise ValueError("Расстояние не может быть отрицательным")
    return round(float(number), 3)


def normalize_items(items):
    """Проверить, упорядочить и дополнить строки накопительными итогами."""
    if not isinstance(items, list):
        raise ValueError("Поле items должно быть списком")
    normalized = []
    for source_item in items:
        if not isinstance(source_item, dict):
            raise ValueError("Каждая остановка должна быть объектом")
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
            "source_detail": source_item.get("source_detail"),
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
        cumulative_km = round(cumulative_km + item["distance_from_prev_km"], 3)
        cumulative_day_sec += item["run_time_day_sec"]
        cumulative_night_sec += item["run_time_night_sec"]
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
        cumulative_km = round(cumulative_km + float(row["distance_from_prev_km"] or 0), 3)
        cumulative_day_sec += int(row["run_time_day_sec"] or 0)
        cumulative_night_sec += int(row["run_time_night_sec"] or 0)
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
    parts = text.split(":")
    if len(parts) == 3:
        try:
            hours, minutes = int(parts[0]), int(parts[1])
            seconds = int(float(parts[2]))
            if min(hours, minutes, seconds) < 0:
                return 0
            return hours * 3600 + minutes * 60 + seconds
        except (TypeError, ValueError):
            return 0
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        return 0
    return max(0, int(round(number * 60))) if math.isfinite(number) else 0


def _stop_by_legacy_identity(con, item):
    external_code = str(item.get("stop_id") or "").strip()
    row = None
    if external_code:
        row = db.one(con.execute(
            "SELECT * FROM stops WHERE external_code=? ORDER BY id LIMIT 1",
            (external_code,),
        ))
    name = str(item.get("stop_name") or "").strip()
    if row is None and name:
        row = db.one(con.execute(
            "SELECT * FROM stops WHERE lower(name)=lower(?) ORDER BY id LIMIT 1", (name,)
        ))
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
        "latitude": item.get("latitude", item.get("lat")),
        "longitude": item.get("longitude", item.get("lon")),
        "address": item.get("street"),
        "stop_kind": "обычная",
        "is_terminal": 0,
        "has_dispatcher": 0,
        "municipality": None,
        "registry_flags": "{}",
        "source": "legacy_erm",
        "active": 1,
        "notes": None,
    }


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
    section_index = next(
        (index for index, section in enumerate(sections)
         if isinstance(section, dict) and isinstance(section.get("stops"), list)
         and section["stops"]),
        None,
    )
    if section_index is None:
        return []
    section = sections[section_index]
    rows = []
    for sequence, item in enumerate(section["stops"], start=1):
        if not isinstance(item, dict):
            continue
        stop = _stop_by_legacy_identity(con, item)
        distance = item.get("distance_km")
        if distance is None and item.get("distance_m") is not None:
            try:
                distance = float(item["distance_m"]) / 1000
            except (TypeError, ValueError):
                distance = 0
        runtime = _travel_seconds(item.get("travel_time"))
        rows.append({
            "id": None,
            "route_id": route["id"],
            "direction": direction,
            "stop_id": stop["id"],
            "sequence": sequence,
            "distance_from_prev_km": round(max(0.0, float(distance or 0)), 3),
            "run_time_day_sec": runtime,
            "run_time_night_sec": runtime,
            "source": "legacy_erm",
            "source_detail": json.dumps(
                {"sheet": sheet_name, "section": section_index + 1}, ensure_ascii=False
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
    return get_depot_rows(con, route_id, direction, legacy_fallback=False)
