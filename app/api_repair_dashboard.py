# -*- coding: utf-8 -*-
"""Панель ремонта, канбан и техническая карточка автобуса."""
import datetime
from fastapi import APIRouter, Depends, HTTPException
from . import db
from .auth import current_user

router = APIRouter(prefix="/api/repairs", tags=["repair-dashboard"])
ACTIVE = ("черновик", "диагностика", "ожидает запчасти", "готов к работе", "в работе", "приостановлен", "контроль")

def order_query():
    return ("SELECT ro.*,ro.number order_number,rr.number request_number,b.garage_number,b.plate,"
            "rt.name repair_type_name,u.full_name responsible_master_name FROM repair_orders ro "
            "LEFT JOIN repair_requests rr ON rr.id=ro.request_id JOIN buses b ON b.id=ro.bus_id "
            "LEFT JOIN repair_types rt ON rt.id=ro.repair_type_id LEFT JOIN users u ON u.id=ro.responsible_master_id")

@router.get("/dashboard")
def dashboard(user=Depends(current_user)):
    con = db.connect()
    try:
        marks = ",".join("?" for _ in ACTIVE)
        active = db.rows(con.execute(order_query() + f" WHERE ro.status IN ({marks}) ORDER BY ro.planned_end,ro.id", ACTIVE))
        today = datetime.datetime.now().isoformat(timespec="seconds")
        overdue = sum(1 for item in active if item.get("planned_end") and item["planned_end"] < today)
        open_requests = con.execute("SELECT COUNT(*) FROM repair_requests WHERE status NOT IN ('закрыта','отменена')").fetchone()[0]
        closed = con.execute("SELECT COUNT(*) FROM repair_orders WHERE status='завершен'").fetchone()[0]
        totals = con.execute("SELECT COALESCE(SUM(total_cost),0),COALESCE(SUM(downtime_hours),0) FROM repair_orders WHERE status='завершен'").fetchone()
        kanban = {status: [] for status in ACTIVE}
        for item in active: kanban.setdefault(item["status"], []).append(item)
        return {"active_orders": len(active), "open_requests": open_requests, "overdue_orders": overdue,
                "closed_orders": closed, "closed_cost": totals[0], "closed_downtime_hours": totals[1], "kanban": kanban}
    finally: con.close()

@router.get("/calendar")
def repair_calendar(date_from: str = "", date_to: str = "", user=Depends(current_user)):
    try:
        start_date = datetime.date.fromisoformat(date_from) if date_from else datetime.date.today()
        end_date = datetime.date.fromisoformat(date_to) if date_to else start_date + datetime.timedelta(days=30)
    except ValueError:
        raise HTTPException(400, "Неверный формат дат календаря")
    if start_date > end_date:
        raise HTTPException(400, "Начало диапазона календаря позже окончания")
    start_value = start_date.isoformat() + "T00:00:00"
    end_value = end_date.isoformat() + "T23:59:59"
    con = db.connect()
    try:
        orders = db.rows(con.execute(
            "SELECT ro.id order_id,ro.number order_number,ro.status,ro.planned_start start,ro.planned_end end,b.id bus_id,b.garage_number,b.plate,COALESCE(rt.name,'Ремонт') title FROM repair_orders ro JOIN buses b ON b.id=ro.bus_id LEFT JOIN repair_types rt ON rt.id=ro.repair_type_id WHERE COALESCE(ro.planned_end,ro.planned_start)>=? AND COALESCE(ro.planned_start,ro.planned_end)<=?",
            (start_value, end_value),
        ))
        plans = db.rows(con.execute(
            "SELECT mp.id plan_id,mp.name title,mp.next_date start,mp.next_date end,b.id bus_id,b.garage_number,b.plate,rt.name repair_type_name FROM maintenance_plans mp JOIN buses b ON b.id=mp.bus_id JOIN repair_types rt ON rt.id=mp.repair_type_id WHERE mp.active=1 AND mp.next_date BETWEEN ? AND ?",
            (start_date.isoformat(), end_date.isoformat()),
        ))
        items = [{**item, "event_type": "ремонт"} for item in orders]
        items.extend({**item, "event_type": "ТО", "status": "запланировано"} for item in plans)
        items.sort(key=lambda item: (item.get("start") or "", item["event_type"], item.get("order_id") or item.get("plan_id") or 0))
        return {"date_from": start_date.isoformat(), "date_to": end_date.isoformat(), "items": items}
    finally:
        con.close()
@router.get("/vehicles/{bus_id}/card")
def vehicle_card(bus_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        vehicle = db.one(con.execute("SELECT * FROM buses WHERE id=?", (bus_id,)))
        if not vehicle: raise HTTPException(404, "Автобус не найден")
        marks = ",".join("?" for _ in ACTIVE)
        active_orders = db.rows(con.execute(order_query() + f" WHERE ro.bus_id=? AND ro.status IN ({marks}) ORDER BY ro.id DESC", (bus_id, *ACTIVE)))
        history = db.rows(con.execute("SELECT * FROM vehicle_repair_history WHERE bus_id=? ORDER BY closed_at DESC,id DESC", (bus_id,)))
        totals = con.execute("SELECT COUNT(*),COALESCE(SUM(total_cost),0),COALESCE(SUM(downtime_hours),0) FROM vehicle_repair_history WHERE bus_id=?", (bus_id,)).fetchone()
        plans = db.rows(con.execute("SELECT mp.*,rt.name repair_type_name FROM maintenance_plans mp JOIN repair_types rt ON rt.id=mp.repair_type_id WHERE mp.bus_id=? AND mp.active=1", (bus_id,)))
        return {"vehicle": vehicle, "active_orders": active_orders, "history": history, "maintenance_plans": plans,
                "totals": {"repairs": totals[0], "cost": totals[1], "downtime_hours": totals[2]}}
    finally: con.close()
@router.get("/metrics/downtime")
def downtime_metrics(user=Depends(current_user)):
    con = db.connect()
    try:
        now = datetime.datetime.now()
        stage_hours = {}
        for event in db.rows(con.execute("SELECT status,entered_at,left_at FROM repair_order_status_events")):
            try:
                start = datetime.datetime.fromisoformat(event["entered_at"])
                end = datetime.datetime.fromisoformat(event["left_at"]) if event.get("left_at") else now
            except (TypeError, ValueError):
                continue
            hours = max(0.0, (end - start).total_seconds() / 3600)
            stage_hours[event["status"]] = stage_hours.get(event["status"], 0.0) + hours
        stage_hours = {status: round(hours, 2) for status, hours in stage_hours.items()}
        marks = ",".join("?" for _ in ACTIVE)
        fleet_total = con.execute("SELECT COUNT(*) FROM buses WHERE LOWER(COALESCE(status,'')) NOT IN ('списан','списано')").fetchone()[0]
        unavailable = con.execute(
            f"SELECT COUNT(DISTINCT bus_id) FROM repair_orders WHERE status IN ({marks})", ACTIVE
        ).fetchone()[0]
        readiness = round((fleet_total - unavailable) / fleet_total, 4) if fleet_total else 0.0
        return {
            "fleet_total": fleet_total,
            "unavailable_buses": unavailable,
            "available_buses": max(0, fleet_total - unavailable),
            "readiness_coefficient": readiness,
            "stage_hours": stage_hours,
            "total_stage_hours": round(sum(stage_hours.values()), 2),
        }
    finally:
        con.close()