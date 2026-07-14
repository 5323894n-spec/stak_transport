# -*- coding: utf-8 -*-
"""Остановки и упорядоченные трассы маршрутов."""
import datetime
import sqlite3

from fastapi import APIRouter, Body, Depends, HTTPException

from . import db
from .auth import current_user, require_write
from .route_network import recalculate_trace


router = APIRouter(prefix="/api")

STOP_FIELDS = (
    "external_code", "name", "latitude", "longitude", "address", "stop_kind",
    "is_terminal", "has_dispatcher", "municipality", "registry_flags", "source",
    "active", "notes",
)


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _route_or_404(con, route_id):
    route = db.one(con.execute("SELECT * FROM routes WHERE id=?", (route_id,)))
    if not route:
        raise HTTPException(404, "Маршрут не найден")
    return route


def _stop_or_404(con, stop_id):
    stop = db.one(con.execute("SELECT * FROM stops WHERE id=?", (stop_id,)))
    if not stop:
        raise HTTPException(404, f"Остановка {stop_id} не найдена")
    return stop


def _direction_rows(con, route_id, direction):
    rows = db.rows(con.execute(
        "SELECT rs.*, s.external_code, s.name, s.latitude, s.longitude, s.address, "
        "s.stop_kind, s.is_terminal, s.has_dispatcher, s.municipality, "
        "s.registry_flags, s.source AS stop_source, s.active, s.notes AS stop_notes "
        "FROM route_stops rs JOIN stops s ON s.id=rs.stop_id "
        "WHERE rs.route_id=? AND rs.direction=? ORDER BY rs.sequence",
        (route_id, direction),
    ))
    result = []
    for row in rows:
        stop = {
            "id": row.pop("stop_id"),
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
        row["stop"] = stop
        result.append(row)
    return result


@router.get("/stops")
def stop_list(q: str = "", active: int | None = None, user=Depends(current_user)):
    con = db.connect()
    try:
        sql = "SELECT * FROM stops"
        params = []
        clauses = []
        if active is not None:
            clauses.append("active=?")
            params.append(1 if active else 0)
        if q.strip():
            clauses.append(
                "(lower(name) LIKE ? OR lower(COALESCE(external_code,'')) LIKE ? "
                "OR lower(COALESCE(address,'')) LIKE ?)"
            )
            needle = f"%{q.strip().lower()}%"
            params.extend([needle, needle, needle])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY name,id"
        return {"items": db.rows(con.execute(sql, params))}
    finally:
        con.close()


@router.post("/stops")
def stop_create(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "routes")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Укажите наименование остановки")
    values = {field: payload.get(field) for field in STOP_FIELDS if field in payload}
    values["name"] = name
    code = str(values.get("external_code") or "").strip()
    values["external_code"] = code or None
    values.setdefault("stop_kind", "обычная")
    values.setdefault("registry_flags", "{}")
    values.setdefault("source", "manual")
    values.setdefault("active", 1)
    values["created_at"] = values["updated_at"] = _now()
    fields = list(values)
    con = db.connect()
    try:
        cur = con.execute(
            f"INSERT INTO stops({','.join(fields)}) VALUES({','.join('?' * len(fields))})",
            [values[field] for field in fields],
        )
        db.audit(con, user["username"], "создание остановки", "stops", cur.lastrowid,
                 new=values)
        con.commit()
        return {"id": cur.lastrowid}
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Код остановки уже используется")
    finally:
        con.close()


@router.delete("/stops/{stop_id}")
def stop_delete(stop_id: int, user=Depends(current_user)):
    require_write(user, "routes")
    con = db.connect()
    try:
        stop = _stop_or_404(con, stop_id)
        if con.execute("SELECT 1 FROM route_stops WHERE stop_id=? LIMIT 1", (stop_id,)).fetchone():
            raise HTTPException(409, "Остановка используется в трассе маршрута")
        con.execute("DELETE FROM stops WHERE id=?", (stop_id,))
        db.audit(con, user["username"], "удаление остановки", "stops", stop_id, old=stop)
        con.commit()
        return {"ok": True}
    finally:
        con.close()


@router.get("/routes/{route_id}/network")
def route_network(route_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        route = _route_or_404(con, route_id)
        forward = _direction_rows(con, route_id, "forward")
        backward = _direction_rows(con, route_id, "backward")
        return {
            "route": route,
            "forward": forward,
            "backward": backward,
            "totals": {
                "forward_km": forward[-1]["cumulative_km"] if forward else 0,
                "backward_km": backward[-1]["cumulative_km"] if backward else 0,
            },
            "warnings": [],
        }
    finally:
        con.close()


@router.put("/routes/{route_id}/stops/{direction}")
def route_stops_replace(route_id: int, direction: str, payload: dict = Body(...),
                        user=Depends(current_user)):
    require_write(user, "routes")
    if direction not in ("forward", "backward"):
        raise HTTPException(400, "Направление должно быть forward или backward")
    source_items = payload.get("items")
    if not isinstance(source_items, list):
        raise HTTPException(400, "Поле items должно быть списком")
    try:
        items = recalculate_trace(source_items)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))

    con = db.connect()
    try:
        _route_or_404(con, route_id)
        for item in items:
            _stop_or_404(con, int(item["stop_id"]))
        old = _direction_rows(con, route_id, direction)
        con.execute("DELETE FROM route_stops WHERE route_id=? AND direction=?",
                    (route_id, direction))
        timestamp = _now()
        for item in items:
            con.execute(
                "INSERT INTO route_stops("
                "route_id,direction,stop_id,sequence,distance_from_prev_km,cumulative_km,"
                "run_time_sec,dwell_time_sec,distance_source,boarding_allowed,"
                "alighting_allowed,is_timing_point,source_detail,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    route_id, direction, int(item["stop_id"]), int(item["sequence"]),
                    item["distance_from_prev_km"], item["cumulative_km"],
                    int(item.get("run_time_sec") or 0), int(item.get("dwell_time_sec") or 0),
                    item.get("distance_source") or "manual",
                    1 if item.get("boarding_allowed", 1) else 0,
                    1 if item.get("alighting_allowed", 1) else 0,
                    1 if item.get("is_timing_point", 0) else 0,
                    item.get("source_detail"), timestamp, timestamp,
                ),
            )
        saved = _direction_rows(con, route_id, direction)
        names = ", ".join(row["stop"]["name"] for row in saved)
        total = saved[-1]["cumulative_km"] if saved else 0
        names_field = "stops" if direction == "forward" else "stops_back"
        length_field = "length_km" if direction == "forward" else "length_back_km"
        con.execute(
            f"UPDATE routes SET {names_field}=?, {length_field}=? WHERE id=?",
            (names, total, route_id),
        )
        db.audit(con, user["username"], "замена трассы маршрута", "routes", route_id,
                 old={direction: old}, new={direction: saved})
        con.commit()
        return {"ok": True, "direction": direction, "items": saved, "total_km": total}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    finally:
        con.close()
