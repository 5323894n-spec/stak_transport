# -*- coding: utf-8 -*-
"""Сохранённая матрица времени рейсов по остановкам."""

import datetime

from fastapi import APIRouter, Depends, HTTPException

from . import db
from .auth import current_user, require_write
from .route_timetable import calculate_trip_stop_times, format_service_time


router = APIRouter(prefix="/api")

DIRECTION_TO_TRACE = {
    "прямое": "forward",
    "forward": "forward",
    "обратное": "backward",
    "backward": "backward",
}


def service_time_to_seconds(value):
    try:
        hours, minutes = map(int, str(value).strip().split(":"))
    except (TypeError, ValueError):
        raise ValueError("Некорректное время отправления рейса")
    if hours < 0 or hours >= 48 or not 0 <= minutes < 60:
        raise ValueError("Некорректное время отправления рейса")
    return hours * 3600 + minutes * 60


def _trace_rows(con, route_id, direction):
    return db.rows(
        con.execute(
            "SELECT rs.*,s.name AS stop_name,s.external_code AS stop_code "
            "FROM route_stops rs JOIN stops s ON s.id=rs.stop_id "
            "WHERE rs.route_id=? AND rs.direction=? ORDER BY rs.sequence,rs.id",
            (route_id, direction),
        )
    )


def _period_for_trip(con, trip, departure_sec):
    if trip.get("period_id"):
        return db.one(
            con.execute("SELECT * FROM day_periods WHERE id=?", (trip["period_id"],))
        )
    departure_min = departure_sec // 60
    return db.one(
        con.execute(
            "SELECT * FROM day_periods WHERE route_id=? AND day_type=? "
            "AND active=1 AND start_min<=? AND end_min>? "
            "ORDER BY priority,start_min,id LIMIT 1",
            (trip["route_id"], trip["day_type"], departure_min, departure_min),
        )
    )


def _runtime_overrides(con, period_id):
    if not period_id:
        return {}
    return {
        row["route_stop_id"]: row["run_time_sec"]
        for row in con.execute(
            "SELECT route_stop_id,run_time_sec FROM route_stop_runtimes "
            "WHERE period_id=?",
            (period_id,),
        )
    }


def recalculate_trip_stop_times_in_connection(con, trip_id, preserve_manual=True):
    trip = db.one(con.execute("SELECT * FROM route_trips WHERE id=?", (trip_id,)))
    if not trip:
        raise HTTPException(404, "Рейс не найден")
    existing = db.rows(
        con.execute(
            "SELECT * FROM trip_stop_times WHERE trip_id=? ORDER BY sequence,id",
            (trip_id,),
        )
    )
    if preserve_manual and any(row["is_manual_override"] for row in existing):
        return existing

    trace_direction = DIRECTION_TO_TRACE.get(trip.get("direction"))
    if not trace_direction:
        raise ValueError("Неизвестное направление рейса")
    trace = _trace_rows(con, trip["route_id"], trace_direction)
    departure_sec = service_time_to_seconds(trip.get("dep_time"))
    period = _period_for_trip(con, trip, departure_sec)
    rows = calculate_trip_stop_times(
        trace,
        departure_sec=departure_sec,
        runtime_factor=(period or {}).get("travel_time_factor", 1),
        runtime_overrides=_runtime_overrides(con, (period or {}).get("id")),
    )

    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    con.execute("DELETE FROM trip_stop_times WHERE trip_id=?", (trip_id,))
    for row in rows:
        con.execute(
            "INSERT INTO trip_stop_times(trip_id,route_stop_id,sequence,arrival_sec,"
            "departure_sec,is_timing_point,is_manual_override,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                trip_id,
                row["route_stop_id"],
                row["sequence"],
                row["arrival_sec"],
                row["departure_sec"],
                row["is_timing_point"],
                0,
                timestamp,
                timestamp,
            ),
        )
    if period and not trip.get("period_id"):
        con.execute("UPDATE route_trips SET period_id=? WHERE id=?", (period["id"], trip_id))
    con.execute(
        "UPDATE route_trips SET arr_time=? WHERE id=?",
        (format_service_time(rows[-1]["arrival_sec"]), trip_id),
    )
    return db.rows(
        con.execute(
            "SELECT * FROM trip_stop_times WHERE trip_id=? ORDER BY sequence,id",
            (trip_id,),
        )
    )


def _serialized_time_row(row):
    return {
        **row,
        "arrival_time": format_service_time(row["arrival_sec"]),
        "departure_time": format_service_time(row["departure_sec"]),
        "is_timing_point": bool(row["is_timing_point"]),
        "is_manual_override": bool(row["is_manual_override"]),
    }


@router.post("/trips/{trip_id}/stop-times/recalculate")
def trip_stop_times_recalculate(trip_id: int, user=Depends(current_user)):
    require_write(user, "trips")
    con = db.connect()
    try:
        rows = recalculate_trip_stop_times_in_connection(
            con, trip_id, preserve_manual=False
        )
        db.audit(
            con,
            user["username"],
            "пересчёт поостановочного расписания",
            "route_trips",
            trip_id,
            new={"stop_times": len(rows)},
        )
        con.commit()
        return {"ok": True, "items": [_serialized_time_row(row) for row in rows]}
    except ValueError as exc:
        con.rollback()
        raise HTTPException(400, str(exc))
    finally:
        con.close()


@router.get("/routes/{route_id}/stop-times")
def stop_time_matrix(
    route_id: int,
    day_type: str,
    direction: str = "",
    output_number: int = 0,
    user=Depends(current_user),
):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM routes WHERE id=?", (route_id,)).fetchone():
            raise HTTPException(404, "Маршрут не найден")
        stops = {
            "forward": _trace_rows(con, route_id, "forward"),
            "backward": _trace_rows(con, route_id, "backward"),
        }
        query = (
            "SELECT * FROM route_trips WHERE route_id=? AND day_type=?"
        )
        args = [route_id, day_type]
        if direction:
            query += " AND direction=?"
            args.append(direction)
        if output_number:
            query += " AND output_number=?"
            args.append(output_number)
        query += " ORDER BY output_number,dep_time,trip_number,id"
        trips = []
        for trip in db.rows(con.execute(query, args)):
            times = db.rows(
                con.execute(
                    "SELECT * FROM trip_stop_times WHERE trip_id=? "
                    "ORDER BY sequence,id",
                    (trip["id"],),
                )
            )
            trips.append(
                {
                    "trip_id": trip["id"],
                    "output_number": trip["output_number"],
                    "shift_number": trip["shift_number"],
                    "trip_number": trip["trip_number"],
                    "direction": trip["direction"],
                    "period_id": trip.get("period_id"),
                    "dep_time": trip["dep_time"],
                    "arr_time": trip["arr_time"],
                    "times": [_serialized_time_row(row) for row in times],
                }
            )
        return {"stops": stops, "trips": trips}
    finally:
        con.close()
