# -*- coding: utf-8 -*-
"""Повторно безопасный перенос старых маршрутов в нормализованную трассу."""
import datetime
import hashlib
import json
import math

from .route_network import normalize_stop_name, recalculate_trace


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _split_names(value):
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _legacy_rows(route):
    result = {"forward": [], "backward": []}
    for direction, field in (("forward", "stops"), ("backward", "stops_back")):
        for sequence, name in enumerate(_split_names(route.get(field)), start=1):
            result[direction].append({
                "sequence": sequence,
                "name": name,
                "external_code": None,
                "latitude": None,
                "longitude": None,
                "address": None,
                "distance_from_prev_km": 0.0,
                "cumulative_km": 0.0,
                "run_time_sec": 0,
                "source": "legacy",
            })
    return result


def _seconds(value):
    if value is None or value == "":
        return 0
    if hasattr(value, "total_seconds"):
        return int(value.total_seconds())
    if isinstance(value, (int, float)):
        return int(round(float(value) * 60))
    parts = str(value).split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
    return 0


def _coordinate(value):
    if value is None:
        return None
    return round(float(value), 6)


def _erm_rows(route):
    try:
        notes = json.loads(route.get("notes") or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    details = notes.get("details") if isinstance(notes, dict) else None
    sheets = details.get("sheets") if isinstance(details, dict) else None
    parameters = sheets.get("параметры") if isinstance(sheets, dict) else None
    sections = parameters.get("sections") if isinstance(parameters, dict) else None
    if not isinstance(sections, list):
        return None
    result = {"forward": [], "backward": []}
    for section in sections:
        direction_text = str(section.get("direction") or "").lower()
        direction = "backward" if "обрат" in direction_text else "forward"
        for sequence, stop in enumerate(section.get("stops") or [], start=1):
            distance = stop.get("distance_km")
            if distance is None and stop.get("distance_m") is not None:
                distance = float(stop["distance_m"]) / 1000
            result[direction].append({
                "sequence": sequence,
                "name": str(stop.get("stop_name") or "").strip(),
                "external_code": str(stop.get("stop_id") or "").strip() or None,
                "latitude": _coordinate(stop.get("latitude", stop.get("lat"))),
                "longitude": _coordinate(stop.get("longitude", stop.get("lon"))),
                "address": stop.get("street"),
                "distance_from_prev_km": float(distance or 0),
                "cumulative_km": float(stop.get("cumulative_km") or 0),
                "run_time_sec": _seconds(stop.get("travel_time")),
                "source": "erm",
            })
    return result if result["forward"] or result["backward"] else None


def _distance_metres(lat1, lon1, lat2, lon2):
    radius = 6371000
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    dphi = math.radians(float(lat2) - float(lat1))
    dlambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _canonical_source(route, rows):
    return {
        "route_id": route["id"],
        "stops": route.get("stops") or "",
        "stops_back": route.get("stops_back") or "",
        "rows": rows,
    }


def _source_hash(source):
    raw = json.dumps(source, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _existing_stops(con):
    return [dict(row) for row in con.execute("SELECT * FROM stops ORDER BY id")]


def _match_stop(row, existing):
    code = str(row.get("external_code") or "").strip()
    if code:
        coded = [stop for stop in existing if str(stop.get("external_code") or "") == code]
        if len(coded) == 1:
            return coded[0], []
    normalized = normalize_stop_name(row.get("name") or "")
    named = [stop for stop in existing if normalize_stop_name(stop.get("name") or "") == normalized]
    lat, lon = row.get("latitude"), row.get("longitude")
    if lat is not None and lon is not None:
        nearby = [
            stop for stop in named
            if stop.get("latitude") is not None and stop.get("longitude") is not None
            and _distance_metres(lat, lon, stop["latitude"], stop["longitude"]) <= 50
        ]
        if len(nearby) == 1:
            return nearby[0], []
        if len(nearby) > 1:
            return None, nearby
        # Same name at a distant location is a different physical stop.
        return None, []
    if len(named) == 1:
        return named[0], []
    if len(named) > 1:
        return None, named
    return None, []


def _log_result(con, route_id, source_hash, status, result):
    con.execute(
        "INSERT INTO route_migration_log(route_id,source_hash,status,details_json,created_at) "
        "VALUES(?,?,?,?,?) ON CONFLICT(route_id,source_hash) DO UPDATE SET "
        "status=excluded.status,details_json=excluded.details_json,created_at=excluded.created_at",
        (route_id, source_hash, status,
         json.dumps(result, ensure_ascii=False, default=str), _now()),
    )


def migrate_route(con, route_id):
    route_row = con.execute("SELECT * FROM routes WHERE id=?", (route_id,)).fetchone()
    if not route_row:
        raise ValueError("Маршрут не найден")
    route = dict(route_row)
    rows = _erm_rows(route) or _legacy_rows(route)
    source_hash = _source_hash(_canonical_source(route, rows))
    previous = con.execute(
        "SELECT * FROM route_migration_log WHERE route_id=? AND source_hash=?",
        (route_id, source_hash),
    ).fetchone()
    if previous and previous["status"] == "migrated":
        saved = json.loads(previous["details_json"])
        saved["status"] = "unchanged"
        return saved

    existing = _existing_stops(con)
    planned_new = {}
    planned_rows = {"forward": [], "backward": []}
    ambiguous = []
    for direction in ("forward", "backward"):
        for row in rows[direction]:
            if not row.get("name"):
                continue
            matched, candidates = _match_stop(row, existing)
            if candidates:
                ambiguous.append({
                    "direction": direction,
                    "sequence": row["sequence"],
                    "name": row["name"],
                    "candidate_ids": [candidate["id"] for candidate in candidates],
                })
                continue
            key = str(row.get("external_code") or "") or normalize_stop_name(row["name"])
            if matched:
                stop_ref = ("existing", matched["id"])
            else:
                planned_new.setdefault(key, row)
                stop_ref = ("new", key)
            planned_rows[direction].append((row, stop_ref))

    if ambiguous:
        result = {
            "route_id": route_id,
            "status": "needs_review",
            "source_hash": source_hash,
            "ambiguous": ambiguous,
            "created_stops": 0,
        }
        _log_result(con, route_id, source_hash, "needs_review", result)
        return result

    new_ids = {}
    timestamp = _now()
    for key, row in planned_new.items():
        cur = con.execute(
            "INSERT INTO stops(external_code,name,latitude,longitude,address,source,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (row.get("external_code"), row["name"], row.get("latitude"), row.get("longitude"),
             row.get("address"), row.get("source") or "legacy", timestamp, timestamp),
        )
        new_ids[key] = cur.lastrowid

    con.execute("DELETE FROM route_stops WHERE route_id=?", (route_id,))
    direction_counts = {}
    for direction in ("forward", "backward"):
        prepared = []
        for row, stop_ref in planned_rows[direction]:
            stop_id = stop_ref[1] if stop_ref[0] == "existing" else new_ids[stop_ref[1]]
            prepared.append({
                "stop_id": stop_id,
                "sequence": row["sequence"],
                "distance_from_prev_km": row.get("distance_from_prev_km") or 0,
                "run_time_sec": row.get("run_time_sec") or 0,
                "source": row.get("source") or "legacy",
            })
        prepared = recalculate_trace(prepared)
        for item in prepared:
            con.execute(
                "INSERT INTO route_stops("
                "route_id,direction,stop_id,sequence,distance_from_prev_km,cumulative_km,"
                "run_time_sec,distance_source,source_detail,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (route_id, direction, item["stop_id"], item["sequence"],
                 item["distance_from_prev_km"], item["cumulative_km"], item["run_time_sec"],
                 item["source"], json.dumps({"migration_hash": source_hash}), timestamp, timestamp),
            )
        direction_counts[direction] = len(prepared)

    result = {
        "route_id": route_id,
        "status": "migrated",
        "source_hash": source_hash,
        "created_stops": len(new_ids),
        "forward_stops": direction_counts["forward"],
        "backward_stops": direction_counts["backward"],
        "ambiguous": [],
    }
    _log_result(con, route_id, source_hash, "migrated", result)
    return result
