# -*- coding: utf-8 -*-
"""Периоды движения маршрутов и их расчётные предпросмотры."""
import datetime

from fastapi import APIRouter, Body, Depends, HTTPException

from . import db
from .auth import current_user, require_write
from .route_periods import validate_periods


router = APIRouter(prefix="/api")

PERIOD_FIELDS = (
    "name",
    "start_min",
    "end_min",
    "interval_min",
    "travel_time_factor",
    "transition_mode",
    "transition_window_min",
    "color",
    "priority",
    "active",
)


def _period_rows(con, route_id, day_type):
    return db.rows(
        con.execute(
            "SELECT * FROM day_periods WHERE route_id=? AND day_type=? "
            "ORDER BY start_min,priority,id",
            (route_id, day_type),
        )
    )


def _route_exists(con, route_id):
    return con.execute("SELECT 1 FROM routes WHERE id=?", (route_id,)).fetchone()


def _replace_periods(con, route_id, day_type, payload, user):
    if not _route_exists(con, route_id):
        raise HTTPException(404, "Маршрут не найден")
    normalized = validate_periods(
        payload.get("items") or [],
        require_continuous=bool(payload.get("require_continuous")),
        service_start=payload.get("service_start"),
        service_end=payload.get("service_end"),
    )
    old = _period_rows(con, route_id, day_type)
    con.execute(
        "DELETE FROM day_periods WHERE route_id=? AND day_type=?",
        (route_id, day_type),
    )
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    for position, source in enumerate(normalized):
        row = {
            "name": str(source.get("name") or f"Период {position + 1}").strip(),
            "start_min": source["start_min"],
            "end_min": source["end_min"],
            "interval_min": source["interval_min"],
            "travel_time_factor": source["travel_time_factor"],
            "transition_mode": source["transition_mode"],
            "transition_window_min": source["transition_window_min"],
            "color": source.get("color") or "#3b82f6",
            "priority": int(source.get("priority", position)),
            "active": 1 if source.get("active", 1) else 0,
        }
        fields = ["route_id", "day_type", *PERIOD_FIELDS, "created_at", "updated_at"]
        con.execute(
            f"INSERT INTO day_periods({','.join(fields)}) "
            f"VALUES({','.join('?' for _ in fields)})",
            [route_id, day_type]
            + [row[field] for field in PERIOD_FIELDS]
            + [timestamp, timestamp],
        )
    saved = _period_rows(con, route_id, day_type)
    db.audit(
        con,
        user["username"],
        "замена периодов движения",
        "routes",
        route_id,
        old={"day_type": day_type, "items": old},
        new={"day_type": day_type, "items": saved},
    )
    return saved


@router.get("/routes/{route_id}/periods/{day_type}")
def periods_get(route_id: int, day_type: str, user=Depends(current_user)):
    con = db.connect()
    try:
        if not _route_exists(con, route_id):
            raise HTTPException(404, "Маршрут не найден")
        return {"items": _period_rows(con, route_id, day_type)}
    finally:
        con.close()


@router.put("/routes/{route_id}/periods/{day_type}")
def periods_replace(
    route_id: int,
    day_type: str,
    payload: dict = Body(...),
    user=Depends(current_user),
):
    require_write(user, "trips")
    con = db.connect()
    try:
        saved = _replace_periods(con, route_id, day_type, payload, user)
        con.commit()
        return {"ok": True, "items": saved}
    except ValueError as exc:
        con.rollback()
        raise HTTPException(400, str(exc))
    finally:
        con.close()
