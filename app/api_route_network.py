# -*- coding: utf-8 -*-
"""Остановки и упорядоченные трассы маршрутов."""
import datetime
import json
import math
import secrets
import sqlite3
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile

from . import db
from . import osrm
from .auth import current_user, require_write
from .route_import import apply_import_plan, build_import_plan, parse_network_file
from .route_network import recalculate_trace
from .route_migration import migrate_route
from .route_geometry import (
    GeometryValidationError, GeometryVersionConflict, delete_geometry,
    get_geometry, normalize_osrm_geometry, save_geometry, stop_anchors,
)


router = APIRouter(prefix="/api")

STOP_FIELDS = (
    "external_code", "name", "latitude", "longitude", "address", "stop_kind",
    "is_terminal", "has_dispatcher", "municipality", "registry_flags", "source",
    "active", "notes",
)

_SQLITE_INTEGER_MAX = 2**63 - 1


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _coordinate_value(value, label, lower, upper):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise HTTPException(400, f"{label} должна быть от {lower} до {upper}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(
            400, f"{label} должна быть от {lower} до {upper}"
        ) from None
    if not math.isfinite(number) or not lower <= number <= upper:
        raise HTTPException(400, f"{label} должна быть от {lower} до {upper}")
    return number


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


def _nonnegative_runtime(value, label):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} должно быть целым числом")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} должно быть целым числом") from None
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError(f"{label} должно быть целым числом")
    if number < 0:
        raise ValueError(f"{label} не может быть отрицательным")
    result = int(number)
    if result > _SQLITE_INTEGER_MAX:
        raise ValueError(
            f"{label} должно быть целым числом в допустимом диапазоне"
        )
    return result


def _normalize_trace_runtimes(items):
    for item in items:
        main = _nonnegative_runtime(item.get("run_time_sec", 0), "Время хода")
        day = _nonnegative_runtime(
            item["run_time_day_sec"] if "run_time_day_sec" in item else main,
            "Дневное время хода",
        )
        night = _nonnegative_runtime(
            item["run_time_night_sec"] if "run_time_night_sec" in item else main,
            "Ночное время хода",
        )
        item["run_time_sec"] = main
        item["run_time_day_sec"] = day
        item["run_time_night_sec"] = night
    return items


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
        used_on_route = con.execute(
            "SELECT 1 FROM route_stops WHERE stop_id=? LIMIT 1", (stop_id,)
        ).fetchone()
        used_on_depot_route = con.execute(
            "SELECT 1 FROM route_depot_stops WHERE stop_id=? LIMIT 1", (stop_id,)
        ).fetchone()
        if used_on_route or used_on_depot_route:
            raise HTTPException(409, "Остановка используется в трассе маршрута")
        try:
            con.execute("DELETE FROM stops WHERE id=?", (stop_id,))
            db.audit(
                con, user["username"], "удаление остановки", "stops", stop_id, old=stop
            )
            con.commit()
        except sqlite3.IntegrityError as exc:
            con.rollback()
            raise HTTPException(409, "Остановка используется в трассе маршрута") from exc
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
            "geometries": {
                "forward": get_geometry(con, route_id, "forward"),
                "backward": get_geometry(con, route_id, "backward"),
            },
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
        _normalize_trace_runtimes(items)
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
                "run_time_sec,run_time_day_sec,run_time_night_sec,dwell_time_sec,"
                "distance_source,boarding_allowed,"
                "alighting_allowed,is_timing_point,source_detail,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    route_id, direction, int(item["stop_id"]), int(item["sequence"]),
                    item["distance_from_prev_km"], item["cumulative_km"],
                    item["run_time_sec"], item["run_time_day_sec"],
                    item["run_time_night_sec"],
                    int(item.get("dwell_time_sec") or 0),
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
@router.put("/stops/{stop_id}")
def stop_update(stop_id: int, payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "routes")
    con = db.connect()
    try:
        old = _stop_or_404(con, stop_id)
        values = {field: payload.get(field) for field in STOP_FIELDS if field in payload}
        if not values:
            raise HTTPException(400, "Нет данных для изменения")
        if "name" in values:
            values["name"] = str(values["name"] or "").strip()
            if not values["name"]:
                raise HTTPException(400, "Укажите наименование остановки")
        if "external_code" in values:
            code = str(values["external_code"] or "").strip()
            values["external_code"] = code or None
        for field, label, lower, upper in (
            ("latitude", "Широта", -90, 90),
            ("longitude", "Долгота", -180, 180),
        ):
            if field in values:
                values[field] = _coordinate_value(
                    values[field], label, lower, upper
                )
        values["updated_at"] = _now()
        fields = list(values)
        con.execute(
            f"UPDATE stops SET {','.join(field + '=?' for field in fields)} WHERE id=?",
            [values[field] for field in fields] + [stop_id],
        )
        db.audit(
            con, user["username"], "изменение остановки", "stops", stop_id,
            old={field: old.get(field) for field in fields}, new=values,
        )
        con.commit()
        return {"ok": True}
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Код остановки уже используется")
    finally:
        con.close()


@router.post("/routes/{route_id}/migrate-network")
def route_network_migrate(route_id: int, user=Depends(current_user)):
    require_write(user, "routes")
    con = db.connect()
    try:
        result = migrate_route(con, route_id)
        db.audit(
            con, user["username"], "миграция трассы маршрута", "routes", route_id,
            new=result,
        )
        con.commit()
        return result
    except ValueError as exc:
        raise HTTPException(404 if str(exc) == "Маршрут не найден" else 400, str(exc))
    finally:
        con.close()


@router.post("/routes/migrate-network")
def route_network_migrate_all(user=Depends(current_user)):
    require_write(user, "routes")
    con = db.connect()
    try:
        route_ids = [row["id"] for row in con.execute("SELECT id FROM routes ORDER BY id")]
        results = []
        for route_id in route_ids:
            con.execute("SAVEPOINT migrate_one_route")
            try:
                result = migrate_route(con, route_id)
                con.execute("RELEASE SAVEPOINT migrate_one_route")
            except Exception as exc:
                con.execute("ROLLBACK TO SAVEPOINT migrate_one_route")
                con.execute("RELEASE SAVEPOINT migrate_one_route")
                result = {"route_id": route_id, "status": "failed", "error": str(exc)}
            results.append(result)
        summary = {
            "total": len(results),
            "migrated": sum(item["status"] == "migrated" for item in results),
            "unchanged": sum(item["status"] == "unchanged" for item in results),
            "needs_review": sum(item["status"] == "needs_review" for item in results),
            "failed": sum(item["status"] == "failed" for item in results),
        }
        db.audit(
            con, user["username"], "массовая миграция трасс", "routes", "all",
            new={"summary": summary},
        )
        con.commit()
        return {"summary": summary, "results": results}
    finally:
        con.close()
@router.post("/routes/{route_id}/network-import/preview")
async def route_network_import_preview(route_id: int, file: UploadFile = File(...),
                                       user=Depends(current_user)):
    require_write(user, "routes")
    data = await file.read()
    con = db.connect()
    try:
        _route_or_404(con, route_id)
        try:
            grouped = parse_network_file(file.filename or "", data)
            plan = build_import_plan(con, route_id, grouped)
        except (UnicodeError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        token = secrets.token_hex(16)
        created = datetime.datetime.now()
        expires = created + datetime.timedelta(minutes=30)
        con.execute(
            "INSERT INTO route_import_previews("
            "token,route_id,username,created_at,expires_at,source_name,payload_json"
            ") VALUES(?,?,?,?,?,?,?)",
            (token, route_id, user["username"], created.isoformat(timespec="seconds"),
             expires.isoformat(timespec="seconds"), file.filename or "import",
             json.dumps(plan, ensure_ascii=False, default=str)),
        )
        con.commit()
        return {
            "preview_token": token,
            "expires_at": expires.isoformat(timespec="seconds"),
            "summary": plan["summary"],
            "conflicts": plan["conflicts"],
            "rows": plan["rows"],
        }
    finally:
        con.close()


@router.post("/routes/{route_id}/network-import/apply")
def route_network_import_apply(route_id: int, payload: dict = Body(...),
                               user=Depends(current_user)):
    require_write(user, "routes")
    token = str(payload.get("preview_token") or "").strip()
    if not token:
        raise HTTPException(400, "Не передан preview_token")
    con = db.connect()
    try:
        preview = db.one(con.execute(
            "SELECT * FROM route_import_previews WHERE token=? AND route_id=? AND username=?",
            (token, route_id, user["username"]),
        ))
        if not preview:
            raise HTTPException(404, "Предпросмотр не найден")
        if preview["applied_at"]:
            raise HTTPException(409, "Предпросмотр уже применён")
        now = datetime.datetime.now()
        if now > datetime.datetime.fromisoformat(preview["expires_at"]):
            raise HTTPException(410, "Срок действия предпросмотра истёк")
        plan = json.loads(preview["payload_json"])
        try:
            result = apply_import_plan(con, route_id, plan, now.isoformat(timespec="seconds"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        con.execute(
            "UPDATE route_import_previews SET applied_at=? WHERE token=?",
            (now.isoformat(timespec="seconds"), token),
        )
        db.audit(
            con, user["username"], "импорт трассы маршрута", "routes", route_id,
            new={"source_name": preview["source_name"], **result},
        )
        con.commit()
        return {"ok": True, **result}
    finally:
        con.close()
@router.post("/routes/{route_id}/osrm/preview/{direction}")
def route_osrm_preview(route_id: int, direction: str, user=Depends(current_user)):
    require_write(user, "routes")
    if direction not in ("forward", "backward"):
        raise HTTPException(400, "Направление должно быть forward или backward")
    con = db.connect()
    try:
        _route_or_404(con, route_id)
        rows = _direction_rows(con, route_id, direction)
        if len(rows) < 2:
            raise HTTPException(400, "Для расчёта нужны минимум две остановки")
        missing = [row["sequence"] for row in rows if (
            row["stop"]["latitude"] is None or row["stop"]["longitude"] is None
        )]
        if missing:
            raise HTTPException(
                400,
                "Не хватает координат остановок: " + ", ".join(map(str, missing)),
            )
        coordinates = [
            (row["stop"]["longitude"], row["stop"]["latitude"]) for row in rows
        ]
        anchors = stop_anchors(con, route_id, direction)
        settings = db.get_settings(con)
        try:
            calculated = osrm.request_route(
                coordinates,
                base_url=settings.get("osrm_base_url", osrm.DEFAULT_BASE_URL),
                timeout=10,
            )
        except osrm.OSRMTimeout as exc:
            raise HTTPException(503, str(exc))
        except osrm.OSRMError as exc:
            raise HTTPException(502, str(exc))
        try:
            geometry = normalize_osrm_geometry(calculated["geometry"], anchors)
        except GeometryValidationError as exc:
            raise HTTPException(
                502, f"OSRM вернул некорректную геометрию: {exc}"
            ) from exc
        diff = []
        for index, leg in enumerate(calculated["legs"], start=1):
            row = rows[index]
            diff.append({
                "route_stop_id": row["id"],
                "sequence": row["sequence"],
                "old_distance_km": row["distance_from_prev_km"],
                "new_distance_km": round(leg["distance"] / 1000, 3),
                "old_run_time_sec": row["run_time_sec"],
                "new_run_time_sec": int(round(leg["duration"])),
            })
        current_geometry = get_geometry(con, route_id, direction)
        expected_geometry_version = (
            current_geometry["version"] if current_geometry else 0
        )
        plan = {
            "kind": "osrm",
            "route_id": route_id,
            "direction": direction,
            "diff": diff,
            "geometry": geometry,
            "expected_geometry_version": expected_geometry_version,
        }
        token = secrets.token_hex(16)
        created = datetime.datetime.now()
        expires = created + datetime.timedelta(minutes=30)
        con.execute(
            "INSERT INTO route_import_previews("
            "token,route_id,username,created_at,expires_at,source_name,payload_json"
            ") VALUES(?,?,?,?,?,?,?)",
            (
                token,
                route_id,
                user["username"],
                created.isoformat(timespec="seconds"),
                expires.isoformat(timespec="seconds"),
                f"osrm:{direction}",
                json.dumps(plan, ensure_ascii=False),
            ),
        )
        con.commit()
        return {
            "preview_token": token,
            "expires_at": expires.isoformat(timespec="seconds"),
            "diff": diff,
            "geometry": geometry,
            "geometry_version": expected_geometry_version,
        }
    finally:
        con.close()


@router.post("/routes/{route_id}/osrm/apply/{direction}")
def route_osrm_apply(
    route_id: int,
    direction: str,
    payload: dict = Body(...),
    user=Depends(current_user),
):
    require_write(user, "routes")
    if direction not in ("forward", "backward"):
        raise HTTPException(400, "Направление должно быть forward или backward")
    token = str(payload.get("preview_token") or "").strip()
    if not token:
        raise HTTPException(400, "Не передан preview_token")
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        _route_or_404(con, route_id)
        preview = db.one(con.execute(
            "SELECT * FROM route_import_previews "
            "WHERE token=? AND route_id=? AND username=?",
            (token, route_id, user["username"]),
        ))
        if not preview or preview["source_name"] != f"osrm:{direction}":
            raise HTTPException(404, "Предпросмотр OSRM не найден")
        if preview["applied_at"]:
            raise HTTPException(409, "Предпросмотр OSRM уже применён")
        now = datetime.datetime.now()
        if now > datetime.datetime.fromisoformat(preview["expires_at"]):
            raise HTTPException(410, "Срок действия предпросмотра OSRM истёк")
        plan = json.loads(preview["payload_json"])
        if plan.get("kind") != "osrm" or plan.get("direction") != direction:
            raise HTTPException(400, "Некорректный предпросмотр OSRM")
        expected_geometry_version = plan.get("expected_geometry_version")
        payload_geometry_version = payload.get("expected_geometry_version")
        if (
            type(payload_geometry_version) is not int
            or payload_geometry_version != expected_geometry_version
        ):
            raise HTTPException(
                409,
                "Версия геометрии не совпадает с версией предпросмотра OSRM",
            )
        timestamp = now.isoformat(timespec="seconds")
        for item in plan["diff"]:
            con.execute(
                "UPDATE route_stops SET distance_from_prev_km=?,run_time_sec=?,"
                "distance_source='auto_osrm',updated_at=? "
                "WHERE id=? AND route_id=? AND direction=?",
                (
                    item["new_distance_km"],
                    item["new_run_time_sec"],
                    timestamp,
                    item["route_stop_id"],
                    route_id,
                    direction,
                ),
            )
        cumulative = 0.0
        rows = db.rows(con.execute(
            "SELECT id,distance_from_prev_km FROM route_stops "
            "WHERE route_id=? AND direction=? ORDER BY sequence",
            (route_id, direction),
        ))
        for row in rows:
            cumulative = round(
                cumulative + float(row["distance_from_prev_km"] or 0), 3
            )
            con.execute(
                "UPDATE route_stops SET cumulative_km=? WHERE id=?",
                (cumulative, row["id"]),
            )
        length_field = "length_km" if direction == "forward" else "length_back_km"
        con.execute(
            f"UPDATE routes SET {length_field}=? WHERE id=?",
            (cumulative, route_id),
        )
        old_geometry, saved_geometry = save_geometry(
            con,
            route_id,
            direction,
            plan.get("geometry"),
            "osrm",
            expected_geometry_version,
            user["username"],
            timestamp,
        )
        con.execute(
            "UPDATE route_import_previews SET applied_at=? WHERE token=?",
            (timestamp, token),
        )
        db.audit(
            con,
            user["username"],
            "применение трассы OSRM",
            "routes",
            route_id,
            old=_geometry_summary(direction, old_geometry),
            new={
                **_geometry_summary(direction, saved_geometry),
                "total_km": cumulative,
                "diff": plan["diff"],
            },
        )
        con.commit()
        return {"ok": True, "direction": direction, "total_km": cumulative}
    except GeometryVersionConflict as exc:
        con.rollback()
        raise HTTPException(409, str(exc)) from exc
    except GeometryValidationError as exc:
        con.rollback()
        raise HTTPException(400, str(exc)) from exc
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _geometry_summary(direction, record):
    if record is None:
        return {
            "direction": direction,
            "source": None,
            "version": 0,
            "coordinates": 0,
        }
    return {
        "direction": direction,
        "source": record["source"],
        "version": record["version"],
        "coordinates": len(record["geometry"]["coordinates"]),
    }


@router.put("/routes/{route_id}/geometry/{direction}")
def route_geometry_save(
    route_id: int,
    direction: str,
    payload: dict = Body(...),
    user=Depends(current_user),
):
    require_write(user, "routes")
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        _route_or_404(con, route_id)
        old, saved = save_geometry(
            con,
            route_id,
            direction,
            payload.get("geometry"),
            "manual",
            payload.get("expected_version"),
            user["username"],
            _now(),
        )
        db.audit(
            con,
            user["username"],
            "сохранение геометрии маршрута",
            "route_geometry",
            route_id,
            old=_geometry_summary(direction, old),
            new=_geometry_summary(direction, saved),
        )
        con.commit()
        return saved
    except GeometryValidationError as exc:
        con.rollback()
        raise HTTPException(400, str(exc)) from exc
    except GeometryVersionConflict as exc:
        con.rollback()
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


@router.delete("/routes/{route_id}/geometry/{direction}")
def route_geometry_delete(
    route_id: int,
    direction: str,
    payload: dict = Body(...),
    user=Depends(current_user),
):
    require_write(user, "routes")
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        _route_or_404(con, route_id)
        old = delete_geometry(
            con, route_id, direction, payload.get("expected_version")
        )
        db.audit(
            con,
            user["username"],
            "сброс геометрии маршрута",
            "route_geometry",
            route_id,
            old=_geometry_summary(direction, old),
            new=_geometry_summary(direction, None),
        )
        con.commit()
        return {"ok": True, "direction": direction}
    except GeometryValidationError as exc:
        con.rollback()
        raise HTTPException(400, str(exc)) from exc
    except GeometryVersionConflict as exc:
        con.rollback()
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
