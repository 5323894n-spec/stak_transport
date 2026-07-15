# -*- coding: utf-8 -*-
"""Периоды движения маршрутов и их расчётные предпросмотры."""
import datetime
import json
import secrets
import sqlite3

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

TEMPLATE_ITEM_FIELDS = PERIOD_FIELDS[:-1]


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


def _template_or_404(con, template_id):
    template = con.execute(
        "SELECT * FROM period_templates WHERE id=?", (template_id,)
    ).fetchone()
    if not template:
        raise HTTPException(404, "Шаблон периодов не найден")
    return dict(template)


def _template_items(con, template_id):
    return db.rows(
        con.execute(
            "SELECT * FROM period_template_items WHERE template_id=? "
            "ORDER BY start_min,priority,id",
            (template_id,),
        )
    )


def _save_template_items(con, template_id, items):
    con.execute("DELETE FROM period_template_items WHERE template_id=?", (template_id,))
    for position, source in enumerate(items):
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
        }
        fields = ["template_id", *TEMPLATE_ITEM_FIELDS]
        con.execute(
            f"INSERT INTO period_template_items({','.join(fields)}) "
            f"VALUES({','.join('?' for _ in fields)})",
            [template_id] + [row[field] for field in TEMPLATE_ITEM_FIELDS],
        )


def _template_payload(con, template_id):
    template = _template_or_404(con, template_id)
    template["items"] = _template_items(con, template_id)
    return template


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


@router.get("/period-templates")
def templates_get(user=Depends(current_user)):
    con = db.connect()
    try:
        templates = db.rows(
            con.execute("SELECT * FROM period_templates ORDER BY name,id")
        )
        for template in templates:
            template["items"] = _template_items(con, template["id"])
        return {"items": templates}
    finally:
        con.close()


@router.post("/period-templates")
def template_create(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "trips")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Укажите название шаблона")
    try:
        items = validate_periods(payload.get("items") or [])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    con = db.connect()
    try:
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        cursor = con.execute(
            "INSERT INTO period_templates(name,description,active,version,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (name, payload.get("description"), 1 if payload.get("active", 1) else 0,
             1, timestamp, timestamp),
        )
        template_id = cursor.lastrowid
        _save_template_items(con, template_id, items)
        saved = _template_payload(con, template_id)
        db.audit(con, user["username"], "создание шаблона периодов",
                 "period_templates", template_id, new=saved)
        con.commit()
        return saved
    except sqlite3.IntegrityError:
        con.rollback()
        raise HTTPException(400, "Шаблон с таким названием уже существует")
    finally:
        con.close()


@router.put("/period-templates/{template_id}")
def template_update(template_id: int, payload: dict = Body(...),
                    user=Depends(current_user)):
    require_write(user, "trips")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Укажите название шаблона")
    try:
        items = validate_periods(payload.get("items") or [])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    con = db.connect()
    try:
        old = _template_payload(con, template_id)
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")
        con.execute(
            "UPDATE period_templates SET name=?,description=?,active=?,"
            "version=version+1,updated_at=? WHERE id=?",
            (name, payload.get("description"),
             1 if payload.get("active", old["active"]) else 0,
             timestamp, template_id),
        )
        _save_template_items(con, template_id, items)
        saved = _template_payload(con, template_id)
        db.audit(con, user["username"], "изменение шаблона периодов",
                 "period_templates", template_id, old=old, new=saved)
        con.commit()
        return saved
    except sqlite3.IntegrityError:
        con.rollback()
        raise HTTPException(400, "Шаблон с таким названием уже существует")
    finally:
        con.close()


@router.delete("/period-templates/{template_id}")
def template_delete(template_id: int, user=Depends(current_user)):
    require_write(user, "trips")
    con = db.connect()
    try:
        old = _template_payload(con, template_id)
        con.execute("DELETE FROM period_templates WHERE id=?", (template_id,))
        db.audit(con, user["username"], "удаление шаблона периодов",
                 "period_templates", template_id, old=old)
        con.commit()
        return {"ok": True}
    finally:
        con.close()


@router.post("/routes/{route_id}/periods/{day_type}/template-preview")
def template_preview(route_id: int, day_type: str, payload: dict = Body(...),
                     user=Depends(current_user)):
    require_write(user, "trips")
    con = db.connect()
    try:
        if not _route_exists(con, route_id):
            raise HTTPException(404, "Маршрут не найден")
        template_id = payload.get("template_id")
        _template_or_404(con, template_id)
        items = validate_periods(_template_items(con, template_id))
        old = _period_rows(con, route_id, day_type)
        plan = {
            "kind": "period_template",
            "template_id": template_id,
            "items": items,
            "diff": {"old": old, "new": items},
        }
        token = secrets.token_hex(16)
        now = datetime.datetime.now()
        expires = now + datetime.timedelta(minutes=30)
        con.execute(
            "INSERT INTO period_previews(token,route_id,day_type,username,payload_json,"
            "created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
            (token, route_id, day_type, user["username"],
             json.dumps(plan, ensure_ascii=False),
             now.isoformat(timespec="seconds"),
             expires.isoformat(timespec="seconds")),
        )
        con.commit()
        return {
            "preview_token": token,
            "expires_at": expires.isoformat(timespec="seconds"),
            "diff": plan["diff"],
        }
    except ValueError as exc:
        con.rollback()
        raise HTTPException(400, str(exc))
    finally:
        con.close()


@router.post("/routes/{route_id}/periods/{day_type}/template-apply")
def template_apply(route_id: int, day_type: str, payload: dict = Body(...),
                   user=Depends(current_user)):
    require_write(user, "trips")
    token = str(payload.get("preview_token") or "").strip()
    if not token:
        raise HTTPException(400, "Не указан токен предпросмотра")
    con = db.connect()
    try:
        preview = con.execute(
            "SELECT * FROM period_previews WHERE token=? AND route_id=? "
            "AND day_type=? AND username=?",
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
        if plan.get("kind") != "period_template":
            raise HTTPException(400, "Некорректный тип предпросмотра")
        saved = _replace_periods(
            con, route_id, day_type, {"items": plan.get("items") or []}, user
        )
        applied_at = datetime.datetime.now().isoformat(timespec="seconds")
        con.execute(
            "UPDATE period_previews SET applied_at=? WHERE token=? AND applied_at IS NULL",
            (applied_at, token),
        )
        con.commit()
        return {"ok": True, "items": saved, "applied_at": applied_at}
    except ValueError as exc:
        con.rollback()
        raise HTTPException(400, str(exc))
    finally:
        con.close()
