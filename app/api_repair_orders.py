# -*- coding: utf-8 -*-
"""Заказ-наряды ремонта."""
import datetime
from fastapi import APIRouter, Body, Depends, HTTPException
from . import db
from .auth import current_user
from .repair_service import audit_change, calculate_cost, next_document_number, record_order_status_event, require_repair_action, validate_transition

router = APIRouter(prefix="/api/repairs", tags=["repair-orders"])

def order_row(con, order_id):
    item = db.one(con.execute(
        "SELECT ro.*,ro.number order_number,rr.number request_number,b.garage_number,b.plate,rt.name repair_type_name,u.full_name responsible_master_name "
        "FROM repair_orders ro LEFT JOIN repair_requests rr ON rr.id=ro.request_id JOIN buses b ON b.id=ro.bus_id "
        "LEFT JOIN repair_types rt ON rt.id=ro.repair_type_id LEFT JOIN users u ON u.id=ro.responsible_master_id WHERE ro.id=?", (order_id,)))
    if not item: raise HTTPException(404, "Заказ-наряд не найден")
    return item

@router.get("/references")
def references(user=Depends(current_user)):
    con = db.connect()
    try: return {"repair_types": db.rows(con.execute("SELECT * FROM repair_types WHERE active=1 ORDER BY name")), "workshops": db.rows(con.execute("SELECT * FROM workshops WHERE active=1 ORDER BY name")), "repair_posts": db.rows(con.execute("SELECT * FROM repair_posts WHERE active=1 ORDER BY name"))}
    finally: con.close()

@router.get("/orders")
def orders(active_only: bool = False, user=Depends(current_user)):
    con = db.connect()
    try:
        sql = ("SELECT ro.*,ro.number order_number,rr.number request_number,b.garage_number,b.plate,rt.name repair_type_name,u.full_name responsible_master_name "
               "FROM repair_orders ro LEFT JOIN repair_requests rr ON rr.id=ro.request_id JOIN buses b ON b.id=ro.bus_id "
               "LEFT JOIN repair_types rt ON rt.id=ro.repair_type_id LEFT JOIN users u ON u.id=ro.responsible_master_id")
        if active_only: sql += " WHERE ro.status NOT IN ('завершен','отменен')"
        return {"items": db.rows(con.execute(sql + " ORDER BY ro.created_at DESC,ro.id DESC"))}
    finally: con.close()

@router.post("/orders", status_code=201)
def create_order(payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "manage_order")
    request_id, bus_id, repair_type_id = int(payload.get("request_id") or 0), int(payload.get("vehicle_id") or payload.get("bus_id") or 0), int(payload.get("repair_type_id") or 0)
    if not request_id or not bus_id or not repair_type_id: raise HTTPException(400, "Укажите заявку, автобус и вид ремонта")
    con = db.connect()
    try:
        if con.execute("SELECT 1 FROM repair_orders WHERE request_id=?", (request_id,)).fetchone(): raise HTTPException(409, "По заявке уже создан заказ-наряд")
        request = db.one(con.execute("SELECT * FROM repair_requests WHERE id=?", (request_id,)))
        if not request: raise HTTPException(404, "Заявка не найдена")
        if request["bus_id"] != bus_id: raise HTTPException(409, "Автобус не соответствует заявке")
        planned_start = payload.get("planned_start") or None
        planned_end = payload.get("planned_end") or None
        repair_post_id = int(payload.get("repair_post_id") or 0) or None
        if planned_start and planned_end and planned_start >= planned_end:
            raise HTTPException(400, "Плановое окончание должно быть позже начала")
        if repair_post_id and planned_start and planned_end:
            conflict = con.execute(
                "SELECT number FROM repair_orders WHERE repair_post_id=? AND status NOT IN ('завершен','отменен') AND planned_start IS NOT NULL AND planned_end IS NOT NULL AND planned_start<? AND ?<planned_end LIMIT 1",
                (repair_post_id, planned_end, planned_start),
            ).fetchone()
            if conflict:
                raise HTTPException(409, f"Ремонтный пост занят заказ-нарядом {conflict['number']} в выбранное время")
        now = datetime.datetime.now().isoformat(timespec="seconds")
        order_id = con.execute(
            "INSERT INTO repair_orders(number,request_id,bus_id,repair_type_id,status,repair_post_id,responsible_master_id,diagnosis,odometer_in,planned_start,planned_end,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (next_document_number(con, "order", "РМ"), request_id, bus_id, repair_type_id, "черновик", repair_post_id, user["id"], payload.get("diagnosis") or "", request["odometer"], planned_start, planned_end, now)).lastrowid
        record_order_status_event(con, order_id, "черновик", user_id=user["id"], changed_at=now)
        con.execute("UPDATE repair_requests SET status='принята',accepted_at=? WHERE id=?", (now, request_id))
        item = order_row(con, order_id); audit_change(con, user, "создание заказ-наряда", "repair_order", order_id, new=item)
        con.commit(); return item
    except Exception: con.rollback(); raise
    finally: con.close()

@router.patch("/orders/{order_id}")
def update_order(order_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "manage_order")
    con = db.connect()
    try:
        old = order_row(con, order_id)
        if old["status"] in {"завершен", "отменен"}:
            raise HTTPException(409, "Закрытый заказ-наряд нельзя редактировать")
        diagnosis = str(payload["diagnosis"]) if "diagnosis" in payload else old.get("diagnosis") or ""
        repair_type_id = int(payload.get("repair_type_id") or 0) if "repair_type_id" in payload else old.get("repair_type_id")
        repair_post_id = int(payload.get("repair_post_id") or 0) or None if "repair_post_id" in payload else old.get("repair_post_id")
        planned_start = (payload.get("planned_start") or None) if "planned_start" in payload else old.get("planned_start")
        planned_end = (payload.get("planned_end") or None) if "planned_end" in payload else old.get("planned_end")
        external_cost = float(payload["external_cost"] or 0) if "external_cost" in payload else float(old.get("external_cost") or 0)
        other_cost = float(payload["other_cost"] or 0) if "other_cost" in payload else float(old.get("other_cost") or 0)
        if external_cost < 0 or other_cost < 0:
            raise HTTPException(400, "Расходы не могут быть отрицательными")
        if planned_start and planned_end and planned_start >= planned_end:
            raise HTTPException(400, "Плановое окончание должно быть позже начала")
        if repair_post_id and planned_start and planned_end:
            conflict = con.execute(
                "SELECT number FROM repair_orders WHERE id<>? AND repair_post_id=? AND status NOT IN ('завершен','отменен') AND planned_start IS NOT NULL AND planned_end IS NOT NULL AND planned_start<? AND ?<planned_end LIMIT 1",
                (order_id, repair_post_id, planned_end, planned_start),
            ).fetchone()
            if conflict:
                raise HTTPException(409, f"Ремонтный пост занят заказ-нарядом {conflict['number']} в выбранное время")
        total_cost = calculate_cost(
            labor=old.get("labor_cost"), parts=old.get("parts_cost"),
            external=external_cost, other=other_cost,
        )
        now = datetime.datetime.now().isoformat(timespec="seconds")
        con.execute(
            "UPDATE repair_orders SET diagnosis=?,repair_type_id=?,repair_post_id=?,planned_start=?,planned_end=?,external_cost=?,other_cost=?,total_cost=?,updated_at=? WHERE id=?",
            (diagnosis, repair_type_id, repair_post_id, planned_start, planned_end, external_cost, other_cost, total_cost, now, order_id),
        )
        item = order_row(con, order_id)
        audit_change(con, user, "редактирование заказ-наряда", "repair_order", order_id, old=old, new=item)
        con.commit()
        return item
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
@router.post("/orders/{order_id}/status")
def change_status(order_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "manage_order")
    con = db.connect()
    try:
        old = order_row(con, order_id); target = str(payload.get("status") or "")
        validate_transition(old["status"], target, release_allowed=bool(old.get("release_allowed")))
        now = datetime.datetime.now().isoformat(timespec="seconds")
        con.execute("UPDATE repair_orders SET status=?,updated_at=? WHERE id=?", (target, now, order_id))
        record_order_status_event(con, order_id, target, user_id=user["id"], changed_at=now)
        item = order_row(con, order_id); audit_change(con, user, "изменение статуса заказ-наряда", "repair_order", order_id, old=old, new=item)
        con.commit(); return item
    finally: con.close()
