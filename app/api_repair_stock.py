# -*- coding: utf-8 -*-
"""Запчасти, складские движения и стоимость ремонта."""
from fastapi import APIRouter, Body, Depends, HTTPException
from . import db
from .auth import current_user
from .repair_service import audit_change, calculate_cost, next_document_number, require_repair_action

router = APIRouter(prefix="/api/repairs", tags=["repair-stock"])

def repair_part_row(con, repair_part_id):
    item = db.one(con.execute(
        "SELECT rp.*,p.code,p.name,p.unit,p.stock_qty,p.warehouse_id FROM repair_parts rp JOIN parts p ON p.id=rp.part_id WHERE rp.id=?", (repair_part_id,)))
    if not item: raise HTTPException(404, "Запчасть заказ-наряда не найдена")
    return item

def recalc_cost(con, order_id):
    parts_cost = con.execute("SELECT COALESCE(SUM(installed_qty*unit_price),0) FROM repair_parts WHERE order_id=?", (order_id,)).fetchone()[0]
    order = db.one(con.execute("SELECT * FROM repair_orders WHERE id=?", (order_id,)))
    total = calculate_cost(labor=order["labor_cost"], parts=parts_cost, external=order["external_cost"], other=order["other_cost"])
    con.execute("UPDATE repair_orders SET parts_cost=?,total_cost=? WHERE id=?", (parts_cost, total, order_id))

@router.get("/stock/parts")
def stock_parts(user=Depends(current_user)):
    con = db.connect()
    try: return {"items": db.rows(con.execute("SELECT p.*,w.name warehouse_name FROM parts p LEFT JOIN warehouses w ON w.id=p.warehouse_id WHERE p.active=1 ORDER BY p.name"))}
    finally: con.close()

@router.get("/orders/{order_id}/parts")
def order_parts(order_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        order = db.one(con.execute("SELECT * FROM repair_orders WHERE id=?", (order_id,)))
        if not order: raise HTTPException(404, "Заказ-наряд не найден")
        return {"order": order, "items": db.rows(con.execute("SELECT rp.*,p.code,p.name,p.unit,p.stock_qty FROM repair_parts rp JOIN parts p ON p.id=rp.part_id WHERE rp.order_id=? ORDER BY rp.id", (order_id,)))}
    finally: con.close()

@router.post("/orders/{order_id}/parts", status_code=201)
def request_part(order_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "manage_order")
    part_id, quantity = int(payload.get("part_id") or 0), float(payload.get("quantity") or 0)
    if not part_id or quantity <= 0: raise HTTPException(400, "Укажите запчасть и положительное количество")
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM repair_orders WHERE id=?", (order_id,)).fetchone(): raise HTTPException(404, "Заказ-наряд не найден")
        part = db.one(con.execute("SELECT * FROM parts WHERE id=? AND active=1", (part_id,)))
        if not part: raise HTTPException(404, "Запчасть не найдена")
        repair_part_id = con.execute("INSERT INTO repair_parts(order_id,part_id,requested_qty,unit_price,status) VALUES(?,?,?,?,?)", (order_id, part_id, quantity, part["unit_price"], "запрошено")).lastrowid
        item = repair_part_row(con, repair_part_id); audit_change(con, user, "запрос запчасти", "repair_part", repair_part_id, new=item)
        con.commit(); return item
    finally: con.close()

@router.post("/parts/{repair_part_id}/issue")
def issue_part(repair_part_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "stock")
    quantity = float(payload.get("quantity") or 0)
    if quantity <= 0: raise HTTPException(400, "Количество должно быть положительным")
    con = db.connect()
    try:
        old = repair_part_row(con, repair_part_id)
        if old["issued_qty"] + quantity > old["requested_qty"]: raise HTTPException(409, "Выдача превышает запрошенное количество")
        reserve_used = min(quantity, float(old["reserved_qty"] or 0))
        changed = con.execute("UPDATE parts SET stock_qty=stock_qty-?,reserved_qty=reserved_qty-? WHERE id=? AND stock_qty>=? AND reserved_qty>=?", (quantity, reserve_used, old["part_id"], quantity, reserve_used))
        if changed.rowcount != 1: raise HTTPException(409, "Недостаточно запчастей на складе")
        con.execute("UPDATE repair_parts SET issued_qty=issued_qty+?,reserved_qty=reserved_qty-?,status='выдано' WHERE id=?", (quantity, reserve_used, repair_part_id))
        con.execute("INSERT INTO stock_movements(number,part_id,warehouse_id,repair_part_id,movement_type,quantity,unit_price,performed_by) VALUES(?,?,?,?,?,?,?,?)", (next_document_number(con, "stock", "СК"), old["part_id"], old["warehouse_id"], repair_part_id, "выдача", quantity, old["unit_price"], user["id"]))
        item = repair_part_row(con, repair_part_id); audit_change(con, user, "выдача запчасти", "repair_part", repair_part_id, old=old, new=item)
        con.commit(); return item
    except Exception: con.rollback(); raise
    finally: con.close()

@router.post("/parts/{repair_part_id}/install")
def install_part(repair_part_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "work_assignment")
    quantity = float(payload.get("quantity") or 0)
    if quantity <= 0: raise HTTPException(400, "Количество должно быть положительным")
    con = db.connect()
    try:
        old = repair_part_row(con, repair_part_id)
        available = old["issued_qty"] - old["returned_qty"] - old["installed_qty"]
        if quantity > available: raise HTTPException(409, "Установка превышает выданное количество")
        con.execute("UPDATE repair_parts SET installed_qty=installed_qty+?,status='установлено' WHERE id=?", (quantity, repair_part_id))
        recalc_cost(con, old["order_id"])
        item = repair_part_row(con, repair_part_id); audit_change(con, user, "установка запчасти", "repair_part", repair_part_id, old=old, new=item)
        con.commit(); return item
    finally: con.close()
