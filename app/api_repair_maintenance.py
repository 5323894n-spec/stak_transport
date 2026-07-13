# -*- coding: utf-8 -*-
"""Планы ТО и детерминированная оценка сроков обслуживания."""
import datetime
from fastapi import APIRouter, Body, Depends, HTTPException
from . import db
from .auth import current_user
from .repair_service import audit_change, next_document_number, require_repair_action

router = APIRouter(prefix="/api/repairs/maintenance", tags=["repair-maintenance"])

@router.get("/plans")
def plans(user=Depends(current_user)):
    con = db.connect()
    try:
        return {"items": db.rows(con.execute(
            "SELECT mp.*,mp.bus_id vehicle_id,b.garage_number,b.plate,b.odometer,rt.name repair_type_name "
            "FROM maintenance_plans mp JOIN buses b ON b.id=mp.bus_id JOIN repair_types rt ON rt.id=mp.repair_type_id ORDER BY mp.next_date,mp.id"))}
    finally: con.close()

@router.post("/plans", status_code=201)
def create_plan(payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "manage_order")
    bus_id, repair_type_id = int(payload.get("vehicle_id") or payload.get("bus_id") or 0), int(payload.get("repair_type_id") or 0)
    name = str(payload.get("name") or "").strip()
    if not bus_id or not repair_type_id or not name: raise HTTPException(400, "Укажите автобус, вид и название ТО")
    con = db.connect()
    try:
        try:
            plan_id = con.execute(
                "INSERT INTO maintenance_plans(bus_id,repair_type_id,name,interval_days,interval_km,last_date,last_odometer,next_date,next_odometer,warning_days,warning_km,active) VALUES(?,?,?,?,?,?,?,?,?,?,?,1)",
                (bus_id, repair_type_id, name, payload.get("interval_days"), payload.get("interval_km"), payload.get("last_date"), payload.get("last_odometer"), payload.get("next_date"), payload.get("next_odometer"), int(payload.get("warning_days") or 7), float(payload.get("warning_km") or 500))).lastrowid
        except Exception as exc:
            if "UNIQUE" in str(exc).upper(): raise HTTPException(409, "План ТО для этого автобуса и вида уже существует")
            raise
        item = db.one(con.execute("SELECT * FROM maintenance_plans WHERE id=?", (plan_id,)))
        audit_change(con, user, "создание плана ТО", "maintenance_plan", plan_id, new=item)
        con.commit(); return item
    finally: con.close()

@router.post("/evaluate")
def evaluate(payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "manage_order")
    today = datetime.date.fromisoformat(payload.get("date") or datetime.date.today().isoformat())
    con = db.connect()
    created = notifications = 0
    try:
        rows = db.rows(con.execute(
            "SELECT mp.*,b.odometer,b.garage_number,rt.name repair_type_name FROM maintenance_plans mp "
            "JOIN buses b ON b.id=mp.bus_id JOIN repair_types rt ON rt.id=mp.repair_type_id WHERE mp.active=1"))
        for plan in rows:
            due_date = bool(plan["next_date"] and datetime.date.fromisoformat(plan["next_date"]) <= today)
            due_km = bool(plan["next_odometer"] is not None and float(plan["odometer"] or 0) >= float(plan["next_odometer"]))
            if not (due_date or due_km): continue
            event = db.one(con.execute("SELECT * FROM maintenance_events WHERE plan_id=? AND status IN ('запланировано','заявка создана') ORDER BY id DESC LIMIT 1", (plan["id"],)))
            if event: continue
            number = next_document_number(con, "request", "ЗР", year=today.year)
            description = f"Плановое ТО: {plan['name']} ({plan['repair_type_name']})"
            request_id = con.execute(
                "INSERT INTO repair_requests(number,created_by,bus_id,source,repair_type_id,status,priority,odometer,description) VALUES(?,?,?,?,?,?,?,?,?)",
                (number, user["id"], plan["bus_id"], "плановое ТО", plan["repair_type_id"], "новая", "обычная", plan["odometer"] or 0, description)).lastrowid
            con.execute("INSERT INTO maintenance_events(plan_id,bus_id,due_date,due_odometer,status,request_id) VALUES(?,?,?,?,?,?)", (plan["id"], plan["bus_id"], plan["next_date"], plan["next_odometer"], "заявка создана", request_id))
            db.notify(con, "warning", "плановое ТО", f"Автобус {plan['garage_number']}: наступил срок {plan['name']}, создана заявка {number}")
            audit_change(con, user, "автоматическая заявка планового ТО", "repair_request", request_id, new={"number": number, "plan_id": plan["id"]})
            created += 1; notifications += 1
        con.commit(); return {"plans_checked": len(rows), "requests_created": created, "notifications_created": notifications}
    except Exception: con.rollback(); raise
    finally: con.close()
