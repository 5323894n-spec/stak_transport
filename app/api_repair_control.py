# -*- coding: utf-8 -*-
"""Контрольный допуск, закрытие и история ремонтов."""
import datetime
import json
from fastapi import APIRouter, Body, Depends, HTTPException
from . import db
from .auth import current_user
from .repair_service import audit_change, calculate_downtime, record_order_status_event, require_repair_action

router = APIRouter(tags=["repair-control"])

def get_order(con, order_id):
    item = db.one(con.execute("SELECT ro.*,ro.number order_number,rr.number request_number,u.full_name master_name FROM repair_orders ro LEFT JOIN repair_requests rr ON rr.id=ro.request_id LEFT JOIN users u ON u.id=ro.responsible_master_id WHERE ro.id=?", (order_id,)))
    if not item: raise HTTPException(404, "Заказ-наряд не найден")
    return item

@router.post("/api/repairs/orders/{order_id}/inspection", status_code=201)
def inspect_order(order_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "inspect")
    result = str(payload.get("result") or "").strip()
    if not result: raise HTTPException(400, "Укажите результат контрольного осмотра")
    allowed = bool(payload.get("release_allowed"))
    con = db.connect()
    try:
        order = get_order(con, order_id)
        if order["status"] != "контроль": raise HTTPException(409, "Заказ-наряд не передан на контроль")
        inspection_id = con.execute(
            "INSERT INTO repair_inspections(order_id,inspection_type,inspector_id,result,release_allowed,defects,comment) VALUES(?,?,?,?,?,?,?)",
            (order_id, "контрольный", user["id"], result, 1 if allowed else 0, payload.get("defects") or "", payload.get("comment") or "")).lastrowid
        status = "контроль" if allowed else "в работе"
        now = datetime.datetime.now().isoformat(timespec="seconds")
        con.execute("UPDATE repair_orders SET release_allowed=?,status=?,updated_at=? WHERE id=?", (1 if allowed else 0, status, now, order_id))
        record_order_status_event(con, order_id, status, user_id=user["id"], changed_at=now)
        inspection = db.one(con.execute("SELECT * FROM repair_inspections WHERE id=?", (inspection_id,)))
        audit_change(con, user, "контрольный осмотр ремонта", "repair_inspection", inspection_id, new=inspection)
        con.commit(); return {**inspection, "order_status": status}
    finally: con.close()

@router.post("/api/repairs/orders/{order_id}/close")
def close_order(order_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "close_order")
    result = str(payload.get("result") or "").strip()
    if not result: raise HTTPException(400, "Укажите результат ремонта")
    con = db.connect()
    try:
        old = get_order(con, order_id)
        if old["status"] != "контроль" or not old["release_allowed"]: raise HTTPException(409, "Нет положительного контрольного осмотра")
        now = datetime.datetime.now().isoformat(timespec="seconds")
        operations = db.rows(con.execute("SELECT * FROM repair_operations WHERE order_id=? ORDER BY sequence_no,id", (order_id,)))
        workers = db.rows(con.execute("SELECT rw.*,u.full_name FROM repair_order_workers rw JOIN users u ON u.id=rw.worker_id WHERE rw.order_id=?", (order_id,)))
        parts = db.rows(con.execute("SELECT rp.*,p.code,p.name FROM repair_parts rp JOIN parts p ON p.id=rp.part_id WHERE rp.order_id=?", (order_id,)))
        downtime = calculate_downtime(old.get("actual_start") or old.get("created_at"), now)
        con.execute("UPDATE repair_orders SET status='завершен',result=?,actual_end=?,closed_at=?,downtime_hours=?,updated_at=? WHERE id=?", (result, now, now, downtime, now, order_id))
        record_order_status_event(con, order_id, "завершен", user_id=user["id"], changed_at=now)
        if old.get("request_id"): con.execute("UPDATE repair_requests SET status='закрыта',closed_at=? WHERE id=?", (now, old["request_id"]))
        con.execute("UPDATE buses SET status='исправен',last_to_date=? WHERE id=?", (now[:10], old["bus_id"]))
        con.execute(
            "INSERT INTO vehicle_repair_history(bus_id,order_id,request_number,order_number,opened_at,closed_at,odometer,result,operations_json,workers_json,parts_json,labor_cost,parts_cost,external_cost,other_cost,total_cost,downtime_hours,master_name) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (old["bus_id"], order_id, old.get("request_number") or "", old["order_number"], old.get("created_at"), now, old.get("odometer_in"), result, json.dumps(operations, ensure_ascii=False), json.dumps(workers, ensure_ascii=False), json.dumps(parts, ensure_ascii=False), old.get("labor_cost") or 0, old.get("parts_cost") or 0, old.get("external_cost") or 0, old.get("other_cost") or 0, old.get("total_cost") or 0, downtime, old.get("master_name") or ""))
        item = get_order(con, order_id); audit_change(con, user, "закрытие заказ-наряда", "repair_order", order_id, old=old, new=item)
        db.notify(con, "информация", "ремонт", f"Заказ-наряд {old['order_number']} закрыт, автобус допущен к эксплуатации")
        con.commit(); return item
    except Exception: con.rollback(); raise
    finally: con.close()

@router.get("/api/vehicles/{bus_id}/repair-history")
def vehicle_history(bus_id: int, user=Depends(current_user)):
    con = db.connect()
    try: return {"items": db.rows(con.execute("SELECT * FROM vehicle_repair_history WHERE bus_id=? ORDER BY closed_at DESC,id DESC", (bus_id,)))}
    finally: con.close()
