# -*- coding: utf-8 -*-
"""API данных маршрутных документов."""

import sqlite3

from fastapi import APIRouter, Body, Depends, HTTPException

from . import db
from .auth import current_user, require_write
from .route_depot import (
    DepotNotFoundError,
    get_depot_rows,
    replace_depot_rows,
    validate_direction,
)
from .route_document_data import load_route_document_data, parse_document_options
from .route_document_xlsx import (
    _xlsx_download_response,
    build_schedule_workbook,
    schedule_filename,
)


router = APIRouter(prefix="/api")


def _validation_status(exc):
    return 404 if isinstance(exc, DepotNotFoundError) else 400


@router.get("/routes/{route_id}/depot-stops")
def depot_stops_get(route_id: int, direction: str, user=Depends(current_user)):
    con = db.connect()
    try:
        try:
            validate_direction(direction)
            rows = get_depot_rows(con, route_id, direction)
        except ValueError as exc:
            raise HTTPException(_validation_status(exc), str(exc)) from exc
        return {"route_id": route_id, "direction": direction, "items": rows}
    finally:
        con.close()


@router.put("/routes/{route_id}/depot-stops/{direction}")
def depot_stops_replace(
    route_id: int,
    direction: str,
    payload: dict = Body(...),
    user=Depends(current_user),
):
    require_write(user, "routes")
    con = db.connect()
    try:
        try:
            validate_direction(direction)
            old = get_depot_rows(con, route_id, direction)
            replace_depot_rows(con, route_id, direction, payload.get("items"))
            db.audit(
                con,
                user["username"],
                "замена парковой трассы маршрута",
                "routes",
                route_id,
                old={direction: old},
                new={direction: get_depot_rows(
                    con, route_id, direction, legacy_fallback=False
                )},
            )
            con.commit()
            saved = get_depot_rows(con, route_id, direction, legacy_fallback=False)
        except ValueError as exc:
            con.rollback()
            raise HTTPException(_validation_status(exc), str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            con.rollback()
            raise HTTPException(
                409, "Не удалось сохранить парковую трассу: конфликт данных"
            ) from exc
        return {
            "ok": True, "route_id": route_id,
            "direction": direction, "items": saved,
        }
    finally:
        con.close()


@router.get("/routes/{route_id}/schedule-document.xlsx")
def route_schedule_document(
    route_id: int,
    season: str,
    effective_date: str,
    user=Depends(current_user),
):
    try:
        options = parse_document_options(season, effective_date)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    con = db.connect()
    try:
        try:
            data = load_route_document_data(con, route_id)
        except ValueError as exc:
            status = 404 if str(exc) == "Маршрут не найден" else 400
            raise HTTPException(status, str(exc)) from exc
        workbook = build_schedule_workbook(data, options)
        return _xlsx_download_response(workbook, schedule_filename(data, options))
    finally:
        con.close()
