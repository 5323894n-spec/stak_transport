# -*- coding: utf-8 -*-
"""Резервирование и возврат запчастей ремонта."""
from fastapi import APIRouter, Body, Depends, HTTPException
from . import db
from .auth import current_user
from .api_repair_stock import repair_part_row
from .repair_service import audit_change, next_document_number, require_repair_action

router = APIRouter(prefix="/api/repairs", tags=["repair-stock"])

@router.post("/parts/{repair_part_id}/reserve")
def reserve_part(repair_part_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "stock")
    quantity = float(payload.get("quantity") or 0)
    if quantity <= 0: raise HTTPException(400, "Количество должно быть положительным")
    con = db.connect()
    try:
        old = repair_part_row(con, repair_part_id)
        if old["reserved_qty"] + quantity > old["requested_qty"]: raise HTTPException(409, "Резерв превышает запрошенное количество")
        changed = con.execute("UPDATE parts SET reserved_qty=reserved_qty+? WHERE id=? AND stock_qty-reserved_qty>=?", (quantity, old["part_id"], quantity))
        if changed.rowcount != 1: raise HTTPException(409, "Недостаточно свободного остатка на складе")
        con.execute("UPDATE repair_parts SET reserved_qty=reserved_qty+?,status='зарезервировано' WHERE id=?", (quantity, repair_part_id))
        item = repair_part_row(con, repair_part_id); audit_change(con, user, "резервирование запчасти", "repair_part", repair_part_id, old=old, new=item)
        con.commit(); return item
    except Exception: con.rollback(); raise
    finally: con.close()

@router.post("/parts/{repair_part_id}/return")
def return_part(repair_part_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "stock")
    quantity = float(payload.get("quantity") or 0)
    if quantity <= 0: raise HTTPException(400, "Количество должно быть положительным")
    con = db.connect()
    try:
        old = repair_part_row(con, repair_part_id)
        available = old["issued_qty"] - old["installed_qty"] - old["returned_qty"]
        if quantity > available: raise HTTPException(409, "Возврат превышает неиспользованное количество")
        con.execute("UPDATE parts SET stock_qty=stock_qty+? WHERE id=?", (quantity, old["part_id"]))
        con.execute("UPDATE repair_parts SET returned_qty=returned_qty+?,status='частично возвращено' WHERE id=?", (quantity, repair_part_id))
        con.execute("INSERT INTO stock_movements(number,part_id,warehouse_id,repair_part_id,movement_type,quantity,unit_price,performed_by) VALUES(?,?,?,?,?,?,?,?)", (next_document_number(con, "stock", "СК"), old["part_id"], old["warehouse_id"], repair_part_id, "возврат", quantity, old["unit_price"], user["id"]))
        item = repair_part_row(con, repair_part_id); audit_change(con, user, "возврат запчасти", "repair_part", repair_part_id, old=old, new=item)
        con.commit(); return item
    except Exception: con.rollback(); raise
    finally: con.close()
