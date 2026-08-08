# -*- coding: utf-8 -*-
"""API модуля выручки."""
from fastapi import APIRouter, Body, Depends, HTTPException

from . import db
from .auth import current_user, require_write
from . import revenue_service as rs
from .revenue_reports import build_revenue_report, revenue_report_filename
from .route_document_xlsx import _xlsx_download_response

router = APIRouter(prefix="/api/revenue")


def _guard(user):
    require_write(user, "revenue")


def _handle(exc):
    message = str(exc)
    not_found = message.endswith("не найден") or message.endswith("не найдена")
    raise HTTPException(404 if not_found else 400, message) from exc


@router.get("/fare-types")
def fare_types_list(include_inactive: bool = False, user=Depends(current_user)):
    con = db.connect()
    try:
        return {"items": rs.list_fare_types(con, include_inactive=include_inactive)}
    finally:
        con.close()


@router.post("/fare-types")
def fare_types_upsert(payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    con = db.connect()
    try:
        try:
            ft_id = rs.upsert_fare_type(
                con, code=payload.get("code"), name=payload.get("name"),
                unit=payload.get("unit", "поездка"),
                fare_type_id=payload.get("id"),
            )
            db.audit(con, user["username"], "вид билета", "revenue", ft_id, new=payload)
            con.commit()
            return {
                "id": ft_id, "code": payload.get("code"),
                "name": payload.get("name"), "unit": payload.get("unit", "поездка"),
            }
        except ValueError as exc:
            con.rollback(); _handle(exc)
    finally:
        con.close()


@router.get("/tariffs")
def tariffs_list(fare_type_id: int | None = None, user=Depends(current_user)):
    con = db.connect()
    try:
        return {"items": rs.list_tariffs(con, fare_type_id)}
    finally:
        con.close()


@router.post("/tariffs")
def tariffs_add(payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    con = db.connect()
    try:
        try:
            tid = rs.add_tariff(
                con, fare_type_id=payload.get("fare_type_id"),
                valid_from=payload.get("valid_from"), price=payload.get("price"),
                valid_to=payload.get("valid_to"), comment=payload.get("comment"),
            )
            db.audit(con, user["username"], "тариф", "revenue", tid, new=payload)
            con.commit()
            return {"id": tid}
        except ValueError as exc:
            con.rollback(); _handle(exc)
    finally:
        con.close()


@router.get("/sheets")
def sheets_list(
    date_from: str | None = None, date_to: str | None = None,
    route_id: int | None = None, status: str | None = None,
    user=Depends(current_user),
):
    con = db.connect()
    try:
        return {"items": rs.list_sheets(
            con, date_from=date_from, date_to=date_to,
            route_id=route_id, status=status,
        )}
    finally:
        con.close()


@router.get("/sheets/{sheet_id}")
def sheet_get(sheet_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        try:
            return rs.get_sheet(con, sheet_id)
        except ValueError as exc:
            _handle(exc)
    finally:
        con.close()


@router.post("/sheets")
def sheet_create(payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    con = db.connect()
    try:
        try:
            sid = rs.create_sheet_from_waybill(
                con, payload.get("waybill_id"),
                conductor_id=payload.get("conductor_id"),
                created_by=user["username"],
            )
            db.audit(con, user["username"], "создание листа выручки", "revenue", sid)
            con.commit()
            return rs.get_sheet(con, sid)
        except ValueError as exc:
            con.rollback(); _handle(exc)
    finally:
        con.close()


@router.put("/sheets/{sheet_id}/lines")
def sheet_lines(sheet_id: int, payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    con = db.connect()
    try:
        try:
            lines = [
                (int(item["fare_type_id"]), int(item["tickets_count"]))
                for item in payload.get("lines", [])
            ]
            sheet = rs.set_sheet_lines(con, sheet_id, lines)
            db.audit(con, user["username"], "строки листа выручки", "revenue", sheet_id, new=payload)
            con.commit()
            return sheet
        except ValueError as exc:
            con.rollback(); _handle(exc)
    finally:
        con.close()


def _transition(sheet_id, user, fn):
    con = db.connect()
    try:
        try:
            sheet = fn(con)
            db.audit(
                con, user["username"], "переход статуса листа выручки",
                "revenue", sheet_id, new={"status": sheet["status"]},
            )
            con.commit()
            return sheet
        except ValueError as exc:
            con.rollback(); _handle(exc)
    finally:
        con.close()


@router.post("/sheets/{sheet_id}/submit")
def sheet_submit(sheet_id: int, payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    return _transition(sheet_id, user, lambda con: rs.submit_sheet(
        con, sheet_id, payload.get("submitted_amount"), user=user["username"],
    ))


@router.post("/sheets/{sheet_id}/reconcile")
def sheet_reconcile(sheet_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    _guard(user)
    return _transition(sheet_id, user, lambda con: rs.reconcile_sheet(
        con, sheet_id, user=user["username"],
    ))


@router.post("/sheets/{sheet_id}/cancel")
def sheet_cancel(sheet_id: int, payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    return _transition(sheet_id, user, lambda con: rs.cancel_sheet(
        con, sheet_id, payload.get("reason"), user=user["username"],
    ))


@router.get("/report.xlsx")
def revenue_report(
    date_from: str, date_to: str, group_by: str = "route",
    user=Depends(current_user),
):
    con = db.connect()
    try:
        wb = build_revenue_report(
            con, date_from=date_from, date_to=date_to, group_by=group_by
        )
    finally:
        con.close()
    return _xlsx_download_response(
        wb, revenue_report_filename(date_from, date_to, group_by)
    )
