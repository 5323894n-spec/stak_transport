# -*- coding: utf-8 -*-
"""API модуля диспетчерского контроля."""
from fastapi import APIRouter, Body, Depends, HTTPException

from . import db
from .auth import current_user, require_write
from . import dispatch_service as ds
from .dispatch_reports import build_dispatch_report, dispatch_report_filename
from .route_document_xlsx import _xlsx_download_response

router = APIRouter(prefix="/api/dispatch")


def _guard(user):
    require_write(user, "dispatch")


def _handle(exc):
    message = str(exc)
    if "только в режиме GPS" in message:
        raise HTTPException(409, message) from exc
    not_found = message.endswith("не найден") or message.endswith("не найдена")
    raise HTTPException(404 if not_found else 400, message) from exc


@router.get("/board")
def dispatch_board(date: str, user=Depends(current_user)):
    con = db.connect()
    try:
        board = ds.build_board(con, date)
        con.commit()
        return board
    finally:
        con.close()


@router.get("/adherence")
def dispatch_adherence(date: str, order_line_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        try:
            return {"items": ds.list_trip_facts(con, date, order_line_id)}
        except ValueError as exc:
            _handle(exc)
    finally:
        con.close()


@router.get("/summary")
def dispatch_summary(date: str, user=Depends(current_user)):
    con = db.connect()
    try:
        summary = ds.day_summary(con, date)
        con.commit()
        return summary
    finally:
        con.close()


@router.put("/source-mode")
def dispatch_source_mode(payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    con = db.connect()
    try:
        try:
            result = ds.set_source_mode(
                con, payload.get("date"), payload.get("mode"), user=user["username"]
            )
            db.audit(con, user["username"], "источник диспетчеризации", "dispatch", None, new=payload)
            con.commit()
            return result
        except ValueError as exc:
            con.rollback(); _handle(exc)
    finally:
        con.close()


@router.post("/outputs/{output_id}/status")
def dispatch_output_status(output_id: int, payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    con = db.connect()
    try:
        try:
            result = ds.set_output_status(
                con, output_id, payload.get("status"),
                at=payload.get("at"), reason=payload.get("reason"),
                note=payload.get("note"), user=user["username"],
            )
            db.audit(con, user["username"], "статус выхода", "dispatch", output_id, new=payload)
            con.commit()
            return result
        except ValueError as exc:
            con.rollback(); _handle(exc)
    finally:
        con.close()


@router.put("/trips/{order_line_id}/{trip_number}")
def dispatch_trip_fact(order_line_id: int, trip_number: int, payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    con = db.connect()
    try:
        try:
            date = payload.get("date")
            if not date:
                row = con.execute(
                    "SELECT date FROM dispatch_outputs WHERE order_line_id=?",
                    (order_line_id,),
                ).fetchone()
                if row is None:
                    raise ds.DispatchError("Выход не найден")
                date = row["date"]
            result = ds.set_trip_fact(
                con, order_line_id, trip_number, payload.get("actual_dep"),
                date=date, user=user["username"],
            )
            db.audit(con, user["username"], "факт рейса", "dispatch", order_line_id, new=payload)
            con.commit()
            return result
        except ValueError as exc:
            con.rollback(); _handle(exc)
    finally:
        con.close()


@router.post("/telemetry")
def dispatch_telemetry(payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    con = db.connect()
    try:
        try:
            result = ds.apply_telemetry(con, payload, user=user["username"])
            db.audit(con, user["username"], "телеметрия диспетчеризации", "dispatch", None, new=payload)
            con.commit()
            return result
        except ValueError as exc:
            con.rollback(); _handle(exc)
    finally:
        con.close()


@router.get("/report.xlsx")
def dispatch_report(date: str, user=Depends(current_user)):
    con = db.connect()
    try:
        workbook = build_dispatch_report(con, date)
        con.commit()
    finally:
        con.close()
    return _xlsx_download_response(workbook, dispatch_report_filename(date))
