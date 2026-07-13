# -*- coding: utf-8 -*-
"""Сводка и лениво загружаемые вкладки технической карточки автобуса."""
from fastapi import APIRouter, Depends, HTTPException

from . import db
from .auth import current_user
from .repair_service import ACTIVE_REPAIR_STATUSES

router = APIRouter(
    prefix="/api/repairs/vehicles/{bus_id}/card",
    tags=["vehicle-card"],
)


def require_vehicle(con, bus_id):
    item = db.one(con.execute("SELECT * FROM buses WHERE id=?", (bus_id,)))
    if not item:
        raise HTTPException(404, "Автобус не найден")
    return item


def active_orders(con, bus_id):
    marks = ",".join("?" for _ in ACTIVE_REPAIR_STATUSES)
    return db.rows(con.execute(
        "SELECT ro.*,ro.number order_number,rr.number request_number,"
        "rt.name repair_type_name,u.full_name responsible_master_name "
        "FROM repair_orders ro "
        "LEFT JOIN repair_requests rr ON rr.id=ro.request_id "
        "LEFT JOIN repair_types rt ON rt.id=ro.repair_type_id "
        "LEFT JOIN users u ON u.id=ro.responsible_master_id "
        f"WHERE ro.bus_id=? AND ro.status IN ({marks}) "
        "ORDER BY ro.created_at DESC,ro.id DESC",
        (bus_id, *ACTIVE_REPAIR_STATUSES),
    ))


def next_maintenance(con, bus_id):
    return db.one(con.execute(
        "SELECT mp.*,rt.name repair_type_name FROM maintenance_plans mp "
        "JOIN repair_types rt ON rt.id=mp.repair_type_id "
        "WHERE mp.bus_id=? AND mp.active=1 "
        "ORDER BY CASE WHEN mp.next_date IS NULL THEN 1 ELSE 0 END,"
        "mp.next_date,mp.next_odometer,mp.id LIMIT 1",
        (bus_id,),
    ))


def open_damages(con, bus_id):
    return db.rows(con.execute(
        "SELECT vd.*,vi.incident_type,vi.occurred_at FROM vehicle_damages vd "
        "JOIN vehicle_incidents vi ON vi.id=vd.incident_id "
        "WHERE vi.bus_id=? AND vi.cancelled_at IS NULL AND vd.resolved=0 "
        "ORDER BY vi.occurred_at DESC,vd.id DESC",
        (bus_id,),
    ))


@router.get("")
def card_summary(bus_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        vehicle = require_vehicle(con, bus_id)
        totals = db.one(con.execute(
            "SELECT COUNT(*) repairs,COALESCE(SUM(total_cost),0) cost,"
            "COALESCE(SUM(downtime_hours),0) downtime_hours,"
            "COALESCE(SUM(labor_cost),0) labor_cost,"
            "COALESCE(SUM(parts_cost),0) parts_cost,"
            "COALESCE(SUM(external_cost),0) external_cost,"
            "COALESCE(SUM(other_cost),0) other_cost "
            "FROM repair_orders WHERE bus_id=? AND status<>'отменен'",
            (bus_id,),
        ))
        totals["incidents"] = con.execute(
            "SELECT COUNT(*) FROM vehicle_incidents "
            "WHERE bus_id=? AND cancelled_at IS NULL",
            (bus_id,),
        ).fetchone()[0]
        damages = open_damages(con, bus_id)
        totals["open_damages"] = len(damages)
        cover = db.one(con.execute(
            "SELECT id,original_name,mime_type,caption,captured_at "
            "FROM repair_attachments WHERE bus_id=? AND is_cover=1 "
            "AND cancelled_at IS NULL ORDER BY id DESC LIMIT 1",
            (bus_id,),
        ))
        current = active_orders(con, bus_id)
        plans = db.rows(con.execute(
            "SELECT mp.*,rt.name repair_type_name FROM maintenance_plans mp "
            "JOIN repair_types rt ON rt.id=mp.repair_type_id "
            "WHERE mp.bus_id=? AND mp.active=1 ORDER BY mp.next_date,mp.id",
            (bus_id,),
        ))
        return {
            "vehicle": vehicle,
            "totals": totals,
            "cover": cover,
            "active_order": current[0] if current else None,
            "active_orders": current,
            "next_maintenance": next_maintenance(con, bus_id),
            "maintenance_plans": plans,
            "open_damages": damages,
        }
    finally:
        con.close()


@router.get("/repairs")
def card_repairs(bus_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        require_vehicle(con, bus_id)
        items = db.rows(con.execute(
            "SELECT ro.*,ro.number order_number,rr.number request_number,"
            "rr.description fault_description,rt.name repair_type_name,"
            "u.full_name responsible_master_name,"
            "(SELECT COUNT(*) FROM repair_operations op WHERE op.order_id=ro.id) operation_count,"
            "(SELECT COUNT(*) FROM repair_order_workers rw WHERE rw.order_id=ro.id) worker_count,"
            "(SELECT COUNT(*) FROM repair_parts rp WHERE rp.order_id=ro.id) part_count "
            "FROM repair_orders ro "
            "LEFT JOIN repair_requests rr ON rr.id=ro.request_id "
            "LEFT JOIN repair_types rt ON rt.id=ro.repair_type_id "
            "LEFT JOIN users u ON u.id=ro.responsible_master_id "
            "WHERE ro.bus_id=? ORDER BY ro.created_at DESC,ro.id DESC",
            (bus_id,),
        ))
        return {"items": items}
    finally:
        con.close()


@router.get("/operations")
def card_operations(bus_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        require_vehicle(con, bus_id)
        return {"items": db.rows(con.execute(
            "SELECT op.*,ro.number order_number,u.full_name worker_name,"
            "vs.name vehicle_system_name FROM repair_operations op "
            "JOIN repair_orders ro ON ro.id=op.order_id "
            "LEFT JOIN users u ON u.id=op.worker_id "
            "LEFT JOIN vehicle_systems vs ON vs.id=op.vehicle_system_id "
            "WHERE ro.bus_id=? ORDER BY ro.created_at DESC,op.sequence_no,op.id",
            (bus_id,),
        ))}
    finally:
        con.close()


@router.get("/parts")
def card_parts(bus_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        require_vehicle(con, bus_id)
        return {"items": db.rows(con.execute(
            "SELECT rp.*,p.code,p.name,p.unit,ro.number order_number,"
            "rp.installed_qty*rp.unit_price line_cost "
            "FROM repair_parts rp JOIN parts p ON p.id=rp.part_id "
            "JOIN repair_orders ro ON ro.id=rp.order_id "
            "WHERE ro.bus_id=? ORDER BY ro.created_at DESC,rp.id DESC",
            (bus_id,),
        ))}
    finally:
        con.close()


@router.get("/workers")
def card_workers(bus_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        require_vehicle(con, bus_id)
        return {"items": db.rows(con.execute(
            "SELECT rw.*,u.full_name,u.username,ro.number order_number,"
            "rw.actual_hours*rw.hourly_rate labor_cost "
            "FROM repair_order_workers rw JOIN users u ON u.id=rw.worker_id "
            "JOIN repair_orders ro ON ro.id=rw.order_id "
            "WHERE ro.bus_id=? ORDER BY ro.created_at DESC,rw.id DESC",
            (bus_id,),
        ))}
    finally:
        con.close()


@router.get("/maintenance")
def card_maintenance(bus_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        require_vehicle(con, bus_id)
        plans = db.rows(con.execute(
            "SELECT mp.*,rt.name repair_type_name FROM maintenance_plans mp "
            "JOIN repair_types rt ON rt.id=mp.repair_type_id "
            "WHERE mp.bus_id=? ORDER BY mp.active DESC,mp.next_date,mp.id",
            (bus_id,),
        ))
        events = db.rows(con.execute(
            "SELECT me.*,mp.name plan_name,rt.name repair_type_name,"
            "rr.number request_number,ro.number order_number "
            "FROM maintenance_events me "
            "JOIN maintenance_plans mp ON mp.id=me.plan_id "
            "JOIN repair_types rt ON rt.id=mp.repair_type_id "
            "LEFT JOIN repair_requests rr ON rr.id=me.request_id "
            "LEFT JOIN repair_orders ro ON ro.id=me.order_id "
            "WHERE me.bus_id=? ORDER BY COALESCE(me.completed_at,me.due_date) DESC,me.id DESC",
            (bus_id,),
        ))
        return {"plans": plans, "events": events}
    finally:
        con.close()


@router.get("/costs")
def card_costs(bus_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        require_vehicle(con, bus_id)
        row = con.execute(
            "SELECT COALESCE(SUM(labor_cost),0),COALESCE(SUM(parts_cost),0),"
            "COALESCE(SUM(external_cost),0),COALESCE(SUM(other_cost),0),"
            "COALESCE(SUM(total_cost),0) FROM repair_orders "
            "WHERE bus_id=? AND status<>'отменен'",
            (bus_id,),
        ).fetchone()
        monthly = db.rows(con.execute(
            "SELECT substr(COALESCE(closed_at,created_at),1,7) month,"
            "SUM(labor_cost) labor,SUM(parts_cost) parts,"
            "SUM(external_cost) external,SUM(other_cost) other,"
            "SUM(total_cost) total FROM repair_orders "
            "WHERE bus_id=? AND status<>'отменен' "
            "GROUP BY substr(COALESCE(closed_at,created_at),1,7) "
            "ORDER BY month DESC",
            (bus_id,),
        ))
        return {
            "totals": {
                "labor": row[0],
                "parts": row[1],
                "external": row[2],
                "other": row[3],
                "total": row[4],
            },
            "monthly": monthly,
        }
    finally:
        con.close()


@router.get("/timeline")
def card_timeline(bus_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        require_vehicle(con, bus_id)
        items = []
        for row in db.rows(con.execute(
            "SELECT id,number,status,COALESCE(closed_at,actual_end,created_at) event_at,"
            "result,total_cost FROM repair_orders WHERE bus_id=?",
            (bus_id,),
        )):
            items.append({
                "id": row["id"],
                "event_type": "ремонт",
                "event_at": row["event_at"],
                "title": row["number"],
                "status": row["status"],
                "description": row.get("result") or "",
                "amount": row.get("total_cost") or 0,
            })
        for row in db.rows(con.execute(
            "SELECT id,incident_type,status,occurred_at,circumstances,"
            "actual_damage_cost FROM vehicle_incidents "
            "WHERE bus_id=? AND cancelled_at IS NULL",
            (bus_id,),
        )):
            items.append({
                "id": row["id"],
                "event_type": row["incident_type"],
                "event_at": row["occurred_at"],
                "title": row["incident_type"],
                "status": row["status"],
                "description": row["circumstances"],
                "amount": row.get("actual_damage_cost") or 0,
            })
        for row in db.rows(con.execute(
            "SELECT me.id,me.status,COALESCE(me.completed_at,me.due_date,me.created_at) event_at,"
            "mp.name FROM maintenance_events me "
            "JOIN maintenance_plans mp ON mp.id=me.plan_id WHERE me.bus_id=?",
            (bus_id,),
        )):
            items.append({
                "id": row["id"],
                "event_type": "ТО",
                "event_at": row["event_at"],
                "title": row["name"],
                "status": row["status"],
                "description": "",
                "amount": 0,
            })
        for row in db.rows(con.execute(
            "SELECT id,category,caption,COALESCE(captured_at,uploaded_at) event_at "
            "FROM repair_attachments WHERE bus_id=? AND cancelled_at IS NULL",
            (bus_id,),
        )):
            items.append({
                "id": row["id"],
                "event_type": "фотография",
                "event_at": row["event_at"],
                "title": row.get("category") or "Фотография",
                "status": "",
                "description": row.get("caption") or "",
                "amount": 0,
            })
        items.sort(
            key=lambda item: (
                item.get("event_at") or "",
                item.get("event_type") or "",
                item.get("id") or 0,
            ),
            reverse=True,
        )
        return {"items": items}
    finally:
        con.close()
