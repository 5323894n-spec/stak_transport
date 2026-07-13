# -*- coding: utf-8 -*-
"""Исполнители и учёт труда по заказ-нарядам."""
import datetime
import sqlite3
from fastapi import APIRouter, Body, Depends, HTTPException
from . import db
from .auth import current_user
from .repair_service import audit_change, calculate_cost, require_repair_action

router = APIRouter(prefix="/api/repairs", tags=["repair-workers"])

def worker_row(con, assignment_id):
    item = db.one(con.execute("SELECT rw.*,u.full_name,u.username FROM repair_order_workers rw JOIN users u ON u.id=rw.worker_id WHERE rw.id=?", (assignment_id,)))
    if not item: raise HTTPException(404, "Исполнитель не найден")
    return item

def recalc_labor(con, order_id):
    totals = con.execute("SELECT COALESCE(SUM(actual_hours),0),COALESCE(SUM(actual_hours*hourly_rate),0) FROM repair_order_workers WHERE order_id=?", (order_id,)).fetchone()
    order = db.one(con.execute("SELECT * FROM repair_orders WHERE id=?", (order_id,)))
    total_cost = calculate_cost(labor=totals[1], parts=order["parts_cost"], external=order["external_cost"], other=order["other_cost"])
    con.execute("UPDATE repair_orders SET actual_hours=?,labor_cost=?,total_cost=?,updated_at=? WHERE id=?", (totals[0], totals[1], total_cost, datetime.datetime.now().isoformat(timespec="seconds"), order_id))

@router.get("/workers/available")
def available_workers(user=Depends(current_user)):
    con = db.connect()
    try: return {"items": db.rows(con.execute("SELECT id,username,full_name,role FROM users WHERE active=1 ORDER BY full_name,username"))}
    finally: con.close()

@router.post("/orders/{order_id}/workers", status_code=201)
def assign_worker(order_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "manage_order")
    worker_id = int(payload.get("worker_id") or 0); role = str(payload.get("role") or "слесарь")
    if not worker_id: raise HTTPException(400, "Укажите исполнителя")
    con = db.connect()
    try:
        order = db.one(con.execute("SELECT id,number,planned_start,planned_end FROM repair_orders WHERE id=?", (order_id,)))
        if not order: raise HTTPException(404, "Заказ-наряд не найден")
        if order.get("planned_start") and order.get("planned_end"):
            conflict = con.execute(
                "SELECT ro.number FROM repair_order_workers rw JOIN repair_orders ro ON ro.id=rw.order_id WHERE rw.worker_id=? AND rw.order_id<>? AND rw.status<>'завершен' AND ro.status NOT IN ('завершен','отменен') AND ro.planned_start IS NOT NULL AND ro.planned_end IS NOT NULL AND ro.planned_start<? AND ?<ro.planned_end LIMIT 1",
                (worker_id, order_id, order["planned_end"], order["planned_start"]),
            ).fetchone()
            if conflict:
                raise HTTPException(409, f"Рабочее время исполнителя пересекается с заказ-нарядом {conflict['number']}")
        try:
            assignment_id = con.execute("INSERT INTO repair_order_workers(order_id,worker_id,role,status,planned_hours,hourly_rate,comment) VALUES(?,?,?,?,?,?,?)", (order_id, worker_id, role, "назначен", float(payload.get("planned_hours") or 0), float(payload.get("hourly_rate") or 0), payload.get("comment") or "")).lastrowid
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper(): raise HTTPException(409, "Исполнитель уже назначен в этой роли")
            raise
        item = worker_row(con, assignment_id); audit_change(con, user, "назначение исполнителя", "repair_worker", assignment_id, new=item)
        con.commit(); return item
    finally: con.close()

@router.post("/workers/{assignment_id}/start")
def start_worker(assignment_id: int, user=Depends(current_user)):
    require_repair_action(user, "work_assignment")
    con = db.connect()
    try:
        old = worker_row(con, assignment_id)
        if old["status"] != "назначен": raise HTTPException(409, "Работу нельзя начать в текущем статусе")
        con.execute("UPDATE repair_order_workers SET status='в работе',started_at=? WHERE id=?", (datetime.datetime.now().isoformat(timespec="seconds"), assignment_id))
        item = worker_row(con, assignment_id); audit_change(con, user, "начало работы исполнителя", "repair_worker", assignment_id, old=old, new=item)
        con.commit(); return item
    finally: con.close()

@router.post("/workers/{assignment_id}/finish")
def finish_worker(assignment_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "work_assignment")
    hours = float(payload.get("actual_hours") or 0)
    if hours < 0: raise HTTPException(400, "Фактические часы не могут быть отрицательными")
    con = db.connect()
    try:
        old = worker_row(con, assignment_id)
        if old["status"] != "в работе": raise HTTPException(409, "Работа исполнителя не запущена")
        con.execute("UPDATE repair_order_workers SET status='завершен',actual_hours=?,finished_at=? WHERE id=?", (hours, datetime.datetime.now().isoformat(timespec="seconds"), assignment_id))
        recalc_labor(con, old["order_id"]); item = worker_row(con, assignment_id)
        audit_change(con, user, "завершение работы исполнителя", "repair_worker", assignment_id, old=old, new=item)
        con.commit(); return item
    finally: con.close()
