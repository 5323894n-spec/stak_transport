# -*- coding: utf-8 -*-
"""Shift types and per-route structural shift settings."""

import datetime
import re
import sqlite3

from fastapi import APIRouter, Body, Depends, HTTPException

from . import db
from .auth import current_user, require_write


router = APIRouter(prefix="/api")
SHIFT_CODE_RE = re.compile(r"^[a-z0-9_]+$")


def _shift_type_payload(row):
    if not row:
        return None
    item = dict(row)
    item["allow_split"] = bool(item["allow_split"])
    item["active"] = bool(item["active"])
    return item


def _shift_type_by_id(con, shift_type_id):
    if shift_type_id is None:
        return None
    return db.one(
        con.execute("SELECT * FROM shift_types WHERE id=?", (shift_type_id,))
    )


def _validated_shift_type(payload):
    code = str(payload.get("code") or "").strip().lower()
    name = str(payload.get("name") or "").strip()
    if not code or not SHIFT_CODE_RE.fullmatch(code):
        raise ValueError("Код типа смены: только латинские буквы, цифры и подчёркивание")
    if not name:
        raise ValueError("Укажите название типа смены")
    try:
        planned = int(payload.get("planned_duration_min"))
        maximum = int(payload.get("max_duration_min"))
        driver_slots = int(payload.get("driver_slots", 1))
    except (TypeError, ValueError):
        raise ValueError("Длительность и количество водителей должны быть числами")
    if planned <= 0:
        raise ValueError("Плановая длительность должна быть больше нуля")
    if maximum < planned:
        raise ValueError("Максимальная длительность не может быть меньше плановой")
    if driver_slots not in (1, 2):
        raise ValueError("Количество водительских мест должно быть 1 или 2")
    color = str(payload.get("color") or "#2563eb").strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("Цвет должен быть задан в формате #RRGGBB")
    return {
        "code": code,
        "name": name,
        "work_pattern": str(payload.get("work_pattern") or "custom").strip(),
        "planned_duration_min": planned,
        "max_duration_min": maximum,
        "driver_slots": driver_slots,
        "allow_split": 1 if payload.get("allow_split") else 0,
        "color": color.lower(),
        "active": 1 if payload.get("active", True) else 0,
    }


def _route_or_404(con, route_id):
    route = db.one(con.execute("SELECT * FROM routes WHERE id=?", (route_id,)))
    if not route:
        raise HTTPException(404, "Маршрут не найден")
    return route


def _active_type_or_error(con, shift_type_id, field_name, *, optional=False):
    if shift_type_id in (None, ""):
        if optional:
            return None
        raise ValueError(f"Укажите {field_name}")
    try:
        shift_type_id = int(shift_type_id)
    except (TypeError, ValueError):
        raise ValueError(f"Некорректный {field_name}")
    row = _shift_type_by_id(con, shift_type_id)
    if not row:
        raise ValueError(f"{field_name.capitalize()} не найден")
    if not row["active"]:
        raise ValueError(f"{field_name.capitalize()} должен быть активным")
    return row


def _settings_payload(con, route_id, day_type):
    settings = db.one(
        con.execute(
            "SELECT * FROM route_shift_settings WHERE route_id=? AND day_type=?",
            (route_id, day_type),
        )
    )
    persisted = settings is not None
    if not settings:
        default_type = db.one(
            con.execute("SELECT * FROM shift_types WHERE code='single_8h'")
        )
        long_type = db.one(
            con.execute("SELECT * FROM shift_types WHERE code='two_driver_long'")
        )
        settings = {
            "id": None,
            "route_id": route_id,
            "day_type": day_type,
            "default_shift_type_id": default_type["id"],
            "long_shift_type_id": long_type["id"],
            "handover_min": 10,
            "long_run_threshold_min": 720,
            "auto_split": 1,
            "updated_at": None,
        }
    default_type = _shift_type_by_id(con, settings["default_shift_type_id"])
    long_type = _shift_type_by_id(con, settings.get("long_shift_type_id"))
    return {
        **settings,
        "auto_split": bool(settings["auto_split"]),
        "persisted": persisted,
        "default_shift_type": _shift_type_payload(default_type),
        "long_shift_type": _shift_type_payload(long_type),
    }


@router.get("/shift-types")
def shift_types_list(active_only: bool = True, user=Depends(current_user)):
    con = db.connect()
    try:
        where = "WHERE active=1" if active_only else ""
        rows = db.rows(
            con.execute(f"SELECT * FROM shift_types {where} ORDER BY name,code,id")
        )
        return {"items": [_shift_type_payload(row) for row in rows]}
    finally:
        con.close()


@router.post("/shift-types")
def shift_type_save(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "trips")
    try:
        values = _validated_shift_type(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    con = db.connect()
    try:
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        shift_type_id = payload.get("id")
        old = None
        if shift_type_id:
            old = _shift_type_by_id(con, shift_type_id)
            if not old:
                raise HTTPException(404, "Тип смены не найден")
            con.execute(
                """
                UPDATE shift_types
                SET code=?,name=?,work_pattern=?,planned_duration_min=?,
                    max_duration_min=?,driver_slots=?,allow_split=?,color=?,
                    active=?,updated_at=? WHERE id=?
                """,
                (*values.values(), timestamp, shift_type_id),
            )
            action = "изменение типа смены"
        else:
            cursor = con.execute(
                """
                INSERT INTO shift_types(
                  code,name,work_pattern,planned_duration_min,max_duration_min,
                  driver_slots,allow_split,color,active,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (*values.values(), timestamp, timestamp),
            )
            shift_type_id = cursor.lastrowid
            action = "создание типа смены"
        saved = _shift_type_payload(_shift_type_by_id(con, shift_type_id))
        db.audit(con, user["username"], action, "shift_types", shift_type_id,
                 old=old, new=saved)
        con.commit()
        return saved
    except sqlite3.IntegrityError:
        con.rollback()
        raise HTTPException(400, "Тип смены с таким кодом уже существует")
    finally:
        con.close()


@router.get("/routes/{route_id}/shift-settings/{day_type}")
def route_shift_settings_get(route_id: int, day_type: str,
                             user=Depends(current_user)):
    con = db.connect()
    try:
        _route_or_404(con, route_id)
        return _settings_payload(con, route_id, day_type)
    finally:
        con.close()


@router.put("/routes/{route_id}/shift-settings/{day_type}")
def route_shift_settings_save(route_id: int, day_type: str,
                              payload: dict = Body(...),
                              user=Depends(current_user)):
    require_write(user, "trips")
    con = db.connect()
    try:
        _route_or_404(con, route_id)
        default_type = _active_type_or_error(
            con, payload.get("default_shift_type_id"), "основной тип смены"
        )
        long_type = _active_type_or_error(
            con, payload.get("long_shift_type_id"), "тип длинной смены",
            optional=True,
        )
        try:
            handover_min = int(payload.get("handover_min", 10))
            threshold = int(payload.get("long_run_threshold_min", 720))
        except (TypeError, ValueError):
            raise ValueError("Параметры длительности должны быть числами")
        if handover_min < 0:
            raise ValueError("Время пересмены не может быть отрицательным")
        if threshold <= 0:
            raise ValueError("Порог длинного выпуска должен быть больше нуля")
        old = db.one(con.execute(
            "SELECT * FROM route_shift_settings WHERE route_id=? AND day_type=?",
            (route_id, day_type),
        ))
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        con.execute(
            """
            INSERT INTO route_shift_settings(
              route_id,day_type,default_shift_type_id,long_shift_type_id,
              handover_min,long_run_threshold_min,auto_split,updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(route_id,day_type) DO UPDATE SET
              default_shift_type_id=excluded.default_shift_type_id,
              long_shift_type_id=excluded.long_shift_type_id,
              handover_min=excluded.handover_min,
              long_run_threshold_min=excluded.long_run_threshold_min,
              auto_split=excluded.auto_split,updated_at=excluded.updated_at
            """,
            (route_id, day_type, default_type["id"],
             long_type["id"] if long_type else None, handover_min, threshold,
             1 if payload.get("auto_split", True) else 0, timestamp),
        )
        saved = _settings_payload(con, route_id, day_type)
        db.audit(con, user["username"], "настройка смен маршрута",
                 "route_shift_settings", saved["id"], old=old, new=saved)
        con.commit()
        return saved
    except ValueError as exc:
        con.rollback()
        raise HTTPException(400, str(exc))
    finally:
        con.close()
