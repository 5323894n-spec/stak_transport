# -*- coding: utf-8 -*-
"""Сохранённая матрица времени рейсов по остановкам."""

import datetime

import json
import secrets
import sqlite3
from fastapi import APIRouter, Body, Depends, HTTPException

from . import db
from .auth import current_user, require_write
from .route_periods import calculate_period_preview
from .route_timetable import (
    adjust_stop_times, build_schedule_preview, calculate_trip_stop_times,
    format_service_time,
)


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


def _trip_time_rows(con, trip_id):
    return db.rows(con.execute(
        "SELECT * FROM trip_stop_times WHERE trip_id=? ORDER BY sequence,id",
        (trip_id,),
    ))


@router.patch("/trips/{trip_id}/stop-times/{route_stop_id}")
def trip_stop_time_adjust(
    trip_id: int, route_stop_id: int, payload: dict = Body(...),
    user=Depends(current_user),
):
    require_write(user, "trips")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "Укажите причину ручной корректировки")
    strategy = str(payload.get("strategy") or "selected_only").strip()
    con = db.connect()
    try:
        trip = db.one(con.execute("SELECT * FROM route_trips WHERE id=?", (trip_id,)))
        if not trip:
            raise HTTPException(404, "Рейс не найден")
        rows = _trip_time_rows(con, trip_id)
        if not rows:
            raise HTTPException(400, "Сначала рассчитайте время по остановкам")
        departure_sec = service_time_to_seconds(payload.get("departure_time"))
        if departure_sec + 12 * 3600 < int(rows[0]["arrival_sec"]):
            departure_sec += 24 * 3600
        adjusted = adjust_stop_times(
            rows, route_stop_id=route_stop_id,
            departure_sec=departure_sec, strategy=strategy,
        )
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        for row in adjusted:
            selected = int(row["route_stop_id"]) == route_stop_id
            con.execute(
                "UPDATE trip_stop_times SET arrival_sec=?,departure_sec=?,"
                "is_manual_override=?,override_strategy=?,override_reason=?,updated_at=? "
                "WHERE id=?",
                (row["arrival_sec"], row["departure_sec"],
                 1 if selected else row.get("is_manual_override", 0),
                 strategy if selected else row.get("override_strategy"),
                 reason if selected else row.get("override_reason"),
                 timestamp, row["id"]),
            )
        con.execute(
            "UPDATE route_trips SET dep_time=?,arr_time=? WHERE id=?",
            (format_service_time(adjusted[0]["departure_sec"]),
             format_service_time(adjusted[-1]["arrival_sec"]), trip_id),
        )
        db.audit(
            con, user["username"], "ручная корректировка времени остановки",
            "route_trips", trip_id,
            old={"route_stop_id": route_stop_id, "rows": rows},
            new={"route_stop_id": route_stop_id, "strategy": strategy,
                 "reason": reason, "rows": adjusted},
        )
        con.commit()
        return {"ok": True, "items": [_serialized_time_row(row) for row in adjusted]}
    except ValueError as exc:
        con.rollback()
        raise HTTPException(400, str(exc))
    finally:
        con.close()


@router.post("/routes/{route_id}/stop-times/reset-manual")
def route_stop_times_reset_manual(
    route_id: int, payload: dict = Body(...), user=Depends(current_user),
):
    require_write(user, "trips")
    day_type = str(payload.get("day_type") or "").strip()
    if not day_type:
        raise HTTPException(400, "Не указан тип дня")
    con = db.connect()
    try:
        query = "SELECT id FROM route_trips WHERE route_id=? AND day_type=?"
        args = [route_id, day_type]
        if payload.get("trip_id") is not None:
            query += " AND id=?"
            args.append(int(payload["trip_id"]))
        if payload.get("output_number") is not None:
            query += " AND output_number=?"
            args.append(int(payload["output_number"]))
        trip_ids = [row[0] for row in con.execute(query, args)]
        for selected_trip_id in trip_ids:
            recalculate_trip_stop_times_in_connection(
                con, selected_trip_id, preserve_manual=False
            )
        db.audit(
            con, user["username"], "сброс ручных корректировок времени",
            "routes", route_id,
            new={"day_type": day_type, "trips": len(trip_ids)},
        )
        con.commit()
        return {"ok": True, "updated": len(trip_ids)}
    except (TypeError, ValueError) as exc:
        con.rollback()
        raise HTTPException(400, str(exc))
    finally:
        con.close()


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


def _active_periods(con, route_id, day_type):
    return db.rows(
        con.execute(
            "SELECT * FROM day_periods WHERE route_id=? AND day_type=? AND active=1 "
            "ORDER BY start_min,priority,id",
            (route_id, day_type),
        )
    )


def _all_runtime_overrides(con, period_ids):
    result = {period_id: {} for period_id in period_ids}
    if not period_ids:
        return result
    placeholders = ",".join("?" for _ in period_ids)
    for row in con.execute(
        "SELECT period_id,route_stop_id,run_time_sec FROM route_stop_runtimes "
        f"WHERE period_id IN ({placeholders})",
        period_ids,
    ):
        result.setdefault(row["period_id"], {})[row["route_stop_id"]] = row["run_time_sec"]
    return result


@router.post("/routes/{route_id}/schedule-generation/preview")
def schedule_generation_preview(
    route_id: int,
    payload: dict = Body(...),
    user=Depends(current_user),
):
    require_write(user, "trips")
    day_type = str(payload.get("day_type") or "будни")
    con = db.connect()
    try:
        route = db.one(con.execute("SELECT * FROM routes WHERE id=?", (route_id,)))
        if not route:
            raise HTTPException(404, "Маршрут не найден")
        periods = _active_periods(con, route_id, day_type)
        if not periods:
            raise HTTPException(400, "Для маршрута не заданы периоды движения")
        forward_trace = _trace_rows(con, route_id, "forward")
        backward_trace = _trace_rows(con, route_id, "backward")
        if not forward_trace or not backward_trace:
            raise HTTPException(400, "Для генерации нужны остановки обоих направлений")
        forward_min = int(route.get("trip_time_min") or 0)
        backward_min = int(route.get("trip_time_back_min") or 0)
        if forward_min <= 0 or backward_min <= 0:
            raise HTTPException(400, "Для маршрута не задано время движения")
        outputs = int(payload.get("outputs") or route.get("outputs_count") or 1)
        terminal_layover_min = int(payload.get("terminal_layover_min", 6))
        interval_preview = calculate_period_preview(
            periods,
            forward_min=forward_min,
            backward_min=backward_min,
            terminal_layover_min=terminal_layover_min,
        )
        trips = build_schedule_preview(
            departures=interval_preview["departures"],
            periods=periods,
            forward_trace=forward_trace,
            backward_trace=backward_trace,
            runtime_overrides=_all_runtime_overrides(
                con, [period["id"] for period in periods]
            ),
            outputs=outputs,
            terminal_layover_sec=terminal_layover_min * 60,
        )
        old_trip_count = con.execute(
            "SELECT COUNT(*) FROM route_trips WHERE route_id=? AND day_type=?",
            (route_id, day_type),
        ).fetchone()[0]
        old_stop_time_count = con.execute(
            "SELECT COUNT(*) FROM trip_stop_times tst JOIN route_trips rt "
            "ON rt.id=tst.trip_id WHERE rt.route_id=? AND rt.day_type=?",
            (route_id, day_type),
        ).fetchone()[0]
        diff = {
            "old_trip_count": old_trip_count,
            "new_trip_count": len(trips),
            "old_stop_time_count": old_stop_time_count,
            "new_stop_time_count": sum(len(trip["stop_times"]) for trip in trips),
        }
        plan = {
            "kind": "stop_schedule_generation",
            "route_id": route_id,
            "day_type": day_type,
            "outputs": outputs,
            "terminal_layover_min": terminal_layover_min,
            "trips": trips,
            "periods": interval_preview["periods"],
            "warnings": interval_preview["warnings"],
            "max_buses_required": interval_preview["max_buses_required"],
            "diff": diff,
        }
        token = secrets.token_hex(16)
        now = datetime.datetime.now()
        expires = now + datetime.timedelta(minutes=30)
        con.execute(
            "INSERT INTO schedule_generation_previews(token,route_id,day_type,username,"
            "payload_json,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
            (token, route_id, day_type, user["username"],
             json.dumps(plan, ensure_ascii=False), now.isoformat(timespec="seconds"),
             expires.isoformat(timespec="seconds")),
        )
        con.commit()
        return {"preview_token": token, "expires_at": expires.isoformat(timespec="seconds"), **plan}
    except ValueError as exc:
        con.rollback()
        raise HTTPException(400, str(exc))
    finally:
        con.close()


def _insert_generated_stop_times(con, trip_id, stop_times, timestamp):
    for row in stop_times:
        con.execute(
            "INSERT INTO trip_stop_times(trip_id,route_stop_id,sequence,arrival_sec,"
            "departure_sec,is_timing_point,is_manual_override,override_strategy,"
            "override_reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                trip_id,
                row["route_stop_id"],
                row["sequence"],
                row["arrival_sec"],
                row["departure_sec"],
                1 if row.get("is_timing_point") else 0,
                0,
                None,
                None,
                timestamp,
                timestamp,
            ),
        )


@router.post("/routes/{route_id}/schedule-generation/apply")
def schedule_generation_apply(
    route_id: int,
    payload: dict = Body(...),
    user=Depends(current_user),
):
    require_write(user, "trips")
    day_type = str(payload.get("day_type") or "будни")
    token = str(payload.get("preview_token") or "").strip()
    if not token:
        raise HTTPException(400, "Не указан токен предпросмотра")
    con = db.connect()
    try:
        preview = con.execute(
            "SELECT * FROM schedule_generation_previews WHERE token=? "
            "AND route_id=? AND day_type=? AND username=?",
            (token, route_id, day_type, user["username"]),
        ).fetchone()
        if not preview:
            raise HTTPException(404, "Предпросмотр не найден")
        preview = dict(preview)
        if preview["applied_at"]:
            raise HTTPException(409, "Предпросмотр уже применён")
        if datetime.datetime.fromisoformat(preview["expires_at"]) < datetime.datetime.now():
            raise HTTPException(410, "Срок предпросмотра истёк")
        plan = json.loads(preview["payload_json"])
        if (
            plan.get("kind") != "stop_schedule_generation"
            or int(plan.get("route_id")) != route_id
            or plan.get("day_type") != day_type
        ):
            raise HTTPException(400, "Некорректный план генерации")

        old_trip_count = con.execute(
            "SELECT COUNT(*) FROM route_trips WHERE route_id=? AND day_type=?",
            (route_id, day_type),
        ).fetchone()[0]
        con.execute(
            "DELETE FROM route_trips WHERE route_id=? AND day_type=?",
            (route_id, day_type),
        )
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        inserted = 0
        for source in plan.get("trips") or []:
            cursor = con.execute(
                "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,"
                "trip_number,direction,dep_time,arr_time,distance_km,break_after_min,"
                "break_type,period_id,source,generation_key) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    route_id,
                    day_type,
                    source["output_number"],
                    source.get("shift_number", 1),
                    source["trip_number"],
                    source["direction"],
                    source["dep_time"],
                    source["arr_time"],
                    source.get("distance_km", 0),
                    source.get("break_after_min", 0),
                    source.get("break_type", ""),
                    source.get("period_id"),
                    "period_generation",
                    token,
                ),
            )
            _insert_generated_stop_times(
                con, cursor.lastrowid, source.get("stop_times") or [], timestamp
            )
            inserted += 1
        updated = con.execute(
            "UPDATE schedule_generation_previews SET applied_at=? "
            "WHERE token=? AND applied_at IS NULL",
            (timestamp, token),
        )
        if updated.rowcount != 1:
            raise HTTPException(409, "Предпросмотр уже применён")
        db.audit(
            con,
            user["username"],
            "применение поостановочного расписания",
            "routes",
            route_id,
            old={"day_type": day_type, "trips": old_trip_count},
            new={"day_type": day_type, "trips": inserted, "generation_key": token},
        )
        con.commit()
        return {"ok": True, "trips": inserted, "generation_key": token}
    except (KeyError, TypeError, ValueError, sqlite3.IntegrityError) as exc:
        con.rollback()
        raise HTTPException(400, f"Не удалось применить расписание: {exc}")
    finally:
        con.close()
