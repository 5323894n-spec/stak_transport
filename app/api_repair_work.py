# -*- coding: utf-8 -*-
"""Операции и фактическая работа по заказ-нарядам."""
import datetime
from fastapi import APIRouter, Body, Depends, HTTPException
from . import db
from .auth import current_user
from .repair_service import audit_change, require_repair_action

router = APIRouter(prefix="/api/repairs", tags=["repair-work"])

def operation_row(con, operation_id):
    item = db.one(con.execute("SELECT * FROM repair_operations WHERE id=?", (operation_id,)))
    if not item: raise HTTPException(404, "Операция ремонта не найдена")
    return item

def recalc_hours(con, order_id):
    row = con.execute("SELECT COALESCE(SUM(norm_hours),0),COALESCE(SUM(actual_hours),0) FROM repair_operations WHERE order_id=?", (order_id,)).fetchone()
    con.execute("UPDATE repair_orders SET planned_hours=?,actual_hours=?,updated_at=? WHERE id=?", (row[0], row[1], datetime.datetime.now().isoformat(timespec="seconds"), order_id))

@router.get("/orders/{order_id}/work")
def order_work(order_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        order = db.one(con.execute("SELECT ro.*,ro.number order_number,b.garage_number,b.plate FROM repair_orders ro JOIN buses b ON b.id=ro.bus_id WHERE ro.id=?", (order_id,)))
        if not order: raise HTTPException(404, "Заказ-наряд не найден")
        return {"order": order, "operations": db.rows(con.execute("SELECT * FROM repair_operations WHERE order_id=? ORDER BY sequence_no,id", (order_id,))), "workers": db.rows(con.execute("SELECT rw.*,u.full_name FROM repair_order_workers rw JOIN users u ON u.id=rw.worker_id WHERE rw.order_id=? ORDER BY rw.id", (order_id,)))}
    finally: con.close()

@router.post("/orders/{order_id}/operations", status_code=201)
def add_operation(order_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "manage_order")
    name = str(payload.get("name") or "").strip()
    if not name: raise HTTPException(400, "Укажите наименование операции")
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM repair_orders WHERE id=?", (order_id,)).fetchone(): raise HTTPException(404, "Заказ-наряд не найден")
        sequence = con.execute("SELECT COALESCE(MAX(sequence_no),0)+1 FROM repair_operations WHERE order_id=?", (order_id,)).fetchone()[0]
        operation_id = con.execute("INSERT INTO repair_operations(order_id,sequence_no,name,status,norm_hours,price) VALUES(?,?,?,?,?,?)", (order_id, sequence, name, "запланирована", float(payload.get("norm_hours") or 0), float(payload.get("price") or 0))).lastrowid
        recalc_hours(con, order_id); item = operation_row(con, operation_id)
        audit_change(con, user, "добавление операции ремонта", "repair_operation", operation_id, new=item)
        con.commit(); return item
    finally: con.close()

@router.post("/operations/{operation_id}/start")
def start_operation(operation_id: int, user=Depends(current_user)):
    require_repair_action(user, "work_assignment")
    con = db.connect()
    try:
        old = operation_row(con, operation_id)
        if old["status"] != "запланирована": raise HTTPException(409, "Операцию нельзя запустить в текущем статусе")
        con.execute("UPDATE repair_operations SET status='в работе',started_at=? WHERE id=?", (datetime.datetime.now().isoformat(timespec="seconds"), operation_id))
        item = operation_row(con, operation_id); audit_change(con, user, "начало операции ремонта", "repair_operation", operation_id, old=old, new=item)
        con.commit(); return item
    finally: con.close()

@router.post("/operations/{operation_id}/complete")
def complete_operation(operation_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "work_assignment")
    hours = float(payload.get("actual_hours") or 0); result = str(payload.get("result") or "").strip()
    if hours < 0 or not result: raise HTTPException(400, "Укажите результат и фактические часы")
    con = db.connect()
    try:
        old = operation_row(con, operation_id)
        if old["status"] != "в работе": raise HTTPException(409, "Сначала запустите операцию")
        con.execute("UPDATE repair_operations SET status='выполнена',actual_hours=?,result=?,completed_at=? WHERE id=?", (hours, result, datetime.datetime.now().isoformat(timespec="seconds"), operation_id))
        recalc_hours(con, old["order_id"]); item = operation_row(con, operation_id)
        audit_change(con, user, "завершение операции ремонта", "repair_operation", operation_id, old=old, new=item)
        con.commit(); return item
    finally: con.close()
