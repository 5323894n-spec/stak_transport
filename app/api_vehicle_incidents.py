# -*- coding: utf-8 -*-
"""ДТП, повреждения и связь происшествий с ремонтом автобуса."""
import datetime

from fastapi import APIRouter, Body, Depends, HTTPException

from . import db
from .auth import current_user
from .repair_service import (
    audit_change,
    next_document_number,
    require_repair_action,
)

router = APIRouter(prefix="/api/repairs", tags=["vehicle-incidents"])
INCIDENT_TYPES = {"ДТП", "повреждение", "вандализм", "страховой случай"}
SEVERITIES = {"незначительная", "средняя", "тяжёлая", "критическая"}


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _vehicle(con, bus_id):
    item = db.one(con.execute("SELECT * FROM buses WHERE id=?", (bus_id,)))
    if not item:
        raise HTTPException(404, "Автобус не найден")
    return item


def _damage(con, damage_id):
    item = db.one(con.execute("SELECT * FROM vehicle_damages WHERE id=?", (damage_id,)))
    if not item:
        raise HTTPException(404, "Повреждение не найдено")
    return item


def incident_details(con, incident_id):
    item = db.one(con.execute(
        "SELECT vi.*,d.fio driver_name,u.full_name responsible_name,"
        "rr.number repair_request_number,ro.number repair_order_number "
        "FROM vehicle_incidents vi "
        "LEFT JOIN drivers d ON d.id=vi.driver_id "
        "LEFT JOIN users u ON u.id=vi.responsible_user_id "
        "LEFT JOIN repair_requests rr ON rr.id=vi.repair_request_id "
        "LEFT JOIN repair_orders ro ON ro.id=vi.repair_order_id "
        "WHERE vi.id=?",
        (incident_id,),
    ))
    if not item:
        raise HTTPException(404, "Событие автобуса не найдено")
    item["damages"] = db.rows(con.execute(
        "SELECT * FROM vehicle_damages WHERE incident_id=? ORDER BY id",
        (incident_id,),
    ))
    return item


def _validate_damage(payload):
    area = str(payload.get("area") or "").strip()
    description = str(payload.get("description") or "").strip()
    severity = payload.get("severity") or "средняя"
    if not area or not description:
        raise HTTPException(400, "Укажите зону и описание повреждения")
    if severity not in SEVERITIES:
        raise HTTPException(400, "Неверная степень повреждения")
    return area, description, severity


def _validate_order_bus(con, order_id, bus_id):
    order = db.one(con.execute(
        "SELECT id,bus_id,request_id,number,total_cost FROM repair_orders WHERE id=?",
        (order_id,),
    ))
    if not order:
        raise HTTPException(404, "Заказ-наряд не найден")
    if order["bus_id"] != bus_id:
        raise HTTPException(409, "Заказ-наряд относится к другому автобусу")
    return order


@router.get("/vehicles/{bus_id}/incidents")
def list_incidents(
    bus_id: int,
    include_cancelled: bool = False,
    user=Depends(current_user),
):
    con = db.connect()
    try:
        _vehicle(con, bus_id)
        where = "vi.bus_id=?"
        if not include_cancelled:
            where += " AND vi.cancelled_at IS NULL"
        ids = [row[0] for row in con.execute(
            f"SELECT vi.id FROM vehicle_incidents vi WHERE {where} "
            "ORDER BY vi.occurred_at DESC,vi.id DESC",
            (bus_id,),
        )]
        return {"items": [incident_details(con, incident_id) for incident_id in ids]}
    finally:
        con.close()


@router.post("/vehicles/{bus_id}/incidents", status_code=201)
def create_incident(
    bus_id: int,
    payload: dict = Body(default={}),
    user=Depends(current_user),
):
    require_repair_action(user, "manage_incident")
    incident_type = payload.get("incident_type")
    circumstances = str(payload.get("circumstances") or "").strip()
    occurred_at = str(payload.get("occurred_at") or "").strip()
    if incident_type not in INCIDENT_TYPES:
        raise HTTPException(400, "Неверный тип события")
    if not occurred_at or not circumstances:
        raise HTTPException(400, "Укажите дату и обстоятельства события")
    try:
        estimated_cost = float(payload.get("estimated_damage_cost") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "Предварительный ущерб должен быть числом")
    if estimated_cost < 0:
        raise HTTPException(400, "Предварительный ущерб не может быть отрицательным")

    con = db.connect()
    try:
        vehicle = _vehicle(con, bus_id)
        incident_id = con.execute(
            "INSERT INTO vehicle_incidents("
            "bus_id,incident_type,occurred_at,place,route_id,waybill_id,driver_id,"
            "circumstances,participants,other_vehicle,fault_status,"
            "police_document_number,insurer,insurance_case_number,"
            "responsible_user_id,status,estimated_damage_cost,comment,created_by"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                bus_id,
                incident_type,
                occurred_at,
                payload.get("place"),
                payload.get("route_id"),
                payload.get("waybill_id"),
                payload.get("driver_id"),
                circumstances,
                payload.get("participants"),
                payload.get("other_vehicle"),
                payload.get("fault_status") or "не установлена",
                payload.get("police_document_number"),
                payload.get("insurer"),
                payload.get("insurance_case_number"),
                payload.get("responsible_user_id"),
                "зарегистрировано",
                estimated_cost,
                payload.get("comment"),
                user["id"],
            ),
        ).lastrowid

        for damage in payload.get("damages") or []:
            area, description, severity = _validate_damage(damage)
            con.execute(
                "INSERT INTO vehicle_damages("
                "incident_id,area,description,severity,repair_required,comment"
                ") VALUES(?,?,?,?,?,?)",
                (
                    incident_id,
                    area,
                    description,
                    severity,
                    1 if damage.get("repair_required", True) else 0,
                    damage.get("comment"),
                ),
            )

        if payload.get("create_repair_request"):
            number = next_document_number(con, "request", "ЗР")
            request_id = con.execute(
                "INSERT INTO repair_requests("
                "number,created_by,bus_id,source,incident_id,driver_id,status,"
                "priority,odometer,description,location"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    number,
                    user["id"],
                    bus_id,
                    incident_type,
                    incident_id,
                    payload.get("driver_id"),
                    "новая",
                    "высокая",
                    float(vehicle.get("odometer") or 0),
                    f"{incident_type}: {circumstances}",
                    payload.get("place") or "",
                ),
            ).lastrowid
            con.execute(
                "UPDATE vehicle_incidents SET repair_request_id=? WHERE id=?",
                (request_id, incident_id),
            )

        item = incident_details(con, incident_id)
        audit_change(
            con,
            user,
            "регистрация события автобуса",
            "vehicle_incident",
            incident_id,
            new=item,
        )
        con.commit()
        return item
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


@router.patch("/incidents/{incident_id}")
def update_incident(
    incident_id: int,
    payload: dict = Body(default={}),
    user=Depends(current_user),
):
    require_repair_action(user, "manage_incident")
    fields = {
        "incident_type",
        "occurred_at",
        "place",
        "route_id",
        "waybill_id",
        "driver_id",
        "circumstances",
        "participants",
        "other_vehicle",
        "fault_status",
        "police_document_number",
        "insurer",
        "insurance_case_number",
        "responsible_user_id",
        "status",
        "estimated_damage_cost",
        "comment",
    }
    con = db.connect()
    try:
        old = incident_details(con, incident_id)
        values = [(key, payload[key]) for key in fields if key in payload]
        if "repair_order_id" in payload:
            order_id = int(payload.get("repair_order_id") or 0)
            if order_id:
                order = _validate_order_bus(con, order_id, old["bus_id"])
                values.extend([
                    ("repair_order_id", order_id),
                    ("actual_damage_cost", float(order.get("total_cost") or 0)),
                ])
                if order.get("request_id"):
                    values.append(("repair_request_id", order["request_id"]))
            else:
                values.extend([
                    ("repair_order_id", None),
                    ("actual_damage_cost", 0),
                ])
        if values:
            values.append(("updated_at", _now()))
            con.execute(
                "UPDATE vehicle_incidents SET "
                + ",".join(f"{key}=?" for key, _ in values)
                + " WHERE id=?",
                [value for _, value in values] + [incident_id],
            )
        item = incident_details(con, incident_id)
        audit_change(
            con,
            user,
            "изменение события автобуса",
            "vehicle_incident",
            incident_id,
            old=old,
            new=item,
        )
        con.commit()
        return item
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


@router.post("/incidents/{incident_id}/damages", status_code=201)
def add_damage(
    incident_id: int,
    payload: dict = Body(default={}),
    user=Depends(current_user),
):
    require_repair_action(user, "manage_incident")
    area, description, severity = _validate_damage(payload)
    con = db.connect()
    try:
        incident = incident_details(con, incident_id)
        if incident.get("cancelled_at"):
            raise HTTPException(409, "Событие отменено")
        damage_id = con.execute(
            "INSERT INTO vehicle_damages("
            "incident_id,area,description,severity,repair_required,comment"
            ") VALUES(?,?,?,?,?,?)",
            (
                incident_id,
                area,
                description,
                severity,
                1 if payload.get("repair_required", True) else 0,
                payload.get("comment"),
            ),
        ).lastrowid
        item = _damage(con, damage_id)
        audit_change(
            con,
            user,
            "добавление повреждения",
            "vehicle_damage",
            damage_id,
            new=item,
        )
        con.commit()
        return item
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


@router.patch("/damages/{damage_id}")
def update_damage(
    damage_id: int,
    payload: dict = Body(default={}),
    user=Depends(current_user),
):
    require_repair_action(user, "manage_incident")
    con = db.connect()
    try:
        old = _damage(con, damage_id)
        incident = incident_details(con, old["incident_id"])
        fields = {"area", "description", "severity", "repair_required", "comment"}
        values = [(key, payload[key]) for key in fields if key in payload]
        if "repair_order_id" in payload:
            order_id = int(payload.get("repair_order_id") or 0)
            if order_id:
                _validate_order_bus(con, order_id, incident["bus_id"])
            values.append(("repair_order_id", order_id or None))
        if "resolved" in payload:
            resolved = 1 if payload.get("resolved") else 0
            values.extend([
                ("resolved", resolved),
                ("resolved_at", _now() if resolved else None),
            ])
        if values:
            con.execute(
                "UPDATE vehicle_damages SET "
                + ",".join(f"{key}=?" for key, _ in values)
                + " WHERE id=?",
                [value for _, value in values] + [damage_id],
            )
        item = _damage(con, damage_id)
        audit_change(
            con,
            user,
            "изменение повреждения",
            "vehicle_damage",
            damage_id,
            old=old,
            new=item,
        )
        con.commit()
        return item
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


@router.post("/incidents/{incident_id}/cancel")
def cancel_incident(
    incident_id: int,
    payload: dict = Body(default={}),
    user=Depends(current_user),
):
    require_repair_action(user, "manage_incident")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "Укажите причину отмены")
    con = db.connect()
    try:
        old = incident_details(con, incident_id)
        if old.get("cancelled_at"):
            raise HTTPException(409, "Событие уже отменено")
        now = _now()
        con.execute(
            "UPDATE vehicle_incidents SET status='отменено',cancelled_at=?,"
            "cancel_reason=?,updated_at=? WHERE id=?",
            (now, reason, now, incident_id),
        )
        item = incident_details(con, incident_id)
        audit_change(
            con,
            user,
            "отмена события автобуса",
            "vehicle_incident",
            incident_id,
            old=old,
            new=item,
            comment=reason,
        )
        con.commit()
        return item
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
