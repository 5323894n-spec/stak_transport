# -*- coding: utf-8 -*-
"""API заявок на ремонт и техническое обслуживание."""
import datetime
from fastapi import APIRouter, Body, Depends, HTTPException
from . import db
from .auth import current_user
from .repair_service import audit_change, next_document_number, require_repair_action

router = APIRouter(prefix="/api/repairs", tags=["repairs"])

def _row(con, request_id):
    item = db.one(con.execute(
        "SELECT rr.*,rr.number request_number,rr.bus_id vehicle_id,rr.description fault_description,"
        "rr.source request_source,rr.priority criticality,b.garage_number,b.plate,b.brand,b.model "
        "FROM repair_requests rr JOIN buses b ON b.id=rr.bus_id WHERE rr.id=?", (request_id,)))
    if not item: raise HTTPException(404, "Заявка на ремонт не найдена")
    return item

@router.get("/requests")
def list_requests(status: str = "", vehicle_id: int = 0, q: str = "", user=Depends(current_user)):
    con = db.connect()
    try:
        where, args = [], []
        if status: where.append("rr.status=?"); args.append(status)
        if vehicle_id: where.append("rr.bus_id=?"); args.append(vehicle_id)
        if q:
            where.append("(rr.number LIKE ? OR rr.description LIKE ? OR b.garage_number LIKE ? OR b.plate LIKE ?)")
            args.extend([f"%{q}%"] * 4)
        sql = ("SELECT rr.*,rr.number request_number,rr.bus_id vehicle_id,rr.description fault_description,"
               "rr.source request_source,rr.priority criticality,b.garage_number,b.plate,b.brand,b.model "
               "FROM repair_requests rr JOIN buses b ON b.id=rr.bus_id")
        if where: sql += " WHERE " + " AND ".join(where)
        return {"items": db.rows(con.execute(sql + " ORDER BY rr.created_at DESC,rr.id DESC", args))}
    finally: con.close()

@router.post("/requests", status_code=201)
def create_request(payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "create_request")
    bus_id = int(payload.get("vehicle_id") or payload.get("bus_id") or 0)
    description = str(payload.get("fault_description") or payload.get("description") or "").strip()
    odometer = payload.get("odometer")
    if not bus_id or odometer in (None, "") or not description:
        raise HTTPException(400, "Укажите автобус, пробег и описание неисправности")
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM buses WHERE id=?", (bus_id,)).fetchone(): raise HTTPException(400, "Автобус не найден")
        setting = con.execute("SELECT value FROM settings WHERE key='repair_repeat_days'").fetchone()
        try:
            repeat_days = int(setting["value"]) if setting else 30
        except (TypeError, ValueError):
            repeat_days = 30
        repeat_days = min(365, max(1, repeat_days))
        previous = db.one(con.execute(
            "SELECT id,number FROM repair_requests WHERE bus_id=? AND LOWER(TRIM(description))=LOWER(TRIM(?)) AND created_at>=datetime('now',?) ORDER BY created_at DESC,id DESC LIMIT 1",
            (bus_id, description, f"-{repeat_days} days"),
        ))
        number = next_document_number(con, "request", "ЗР")
        request_id = con.execute(
            "INSERT INTO repair_requests(number,created_by,bus_id,source,status,priority,odometer,description,location,desired_at,repeated,repeated_from_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (number, user["id"], bus_id, payload.get("request_source") or "ручная", "новая", payload.get("criticality") or "обычная", float(odometer), description, payload.get("location") or "", payload.get("desired_at") or None, 1 if previous else 0, previous["id"] if previous else None)).lastrowid
        item = _row(con, request_id)
        audit_change(con, user, "создание заявки на ремонт", "repair_request", request_id, new=item)
        con.commit(); return item
    except Exception: con.rollback(); raise
    finally: con.close()

@router.get("/requests/{request_id}")
def get_request(request_id: int, user=Depends(current_user)):
    con = db.connect()
    try: return _row(con, request_id)
    finally: con.close()

@router.patch("/requests/{request_id}")
def update_request(request_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "create_request")
    fields = {"criticality":"priority", "fault_description":"description", "request_source":"source", "odometer":"odometer", "location":"location", "desired_at":"desired_at"}
    con = db.connect()
    try:
        old = _row(con, request_id); values = [(fields[k], v) for k, v in payload.items() if k in fields]
        if values: con.execute("UPDATE repair_requests SET " + ",".join(f"{k}=?" for k, _ in values) + " WHERE id=?", [v for _, v in values] + [request_id])
        item = _row(con, request_id); audit_change(con, user, "изменение заявки на ремонт", "repair_request", request_id, old=old, new=item)
        con.commit(); return item
    finally: con.close()

@router.post("/requests/{request_id}/cancel")
def cancel_request(request_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "create_request")
    reason = str(payload.get("reason") or "").strip()
    if not reason: raise HTTPException(400, "Укажите причину отмены")
    con = db.connect()
    try:
        old = _row(con, request_id)
        con.execute("UPDATE repair_requests SET status='отменена',cancel_reason=?,closed_at=? WHERE id=?", (reason, datetime.datetime.now().isoformat(timespec="seconds"), request_id))
        item = _row(con, request_id); audit_change(con, user, "отмена заявки на ремонт", "repair_request", request_id, old=old, new=item, comment=reason)
        con.commit(); return item
    finally: con.close()
