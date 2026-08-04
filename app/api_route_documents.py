# -*- coding: utf-8 -*-
"""API данных маршрутных документов."""

import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response

from . import db
from .auth import current_user, require_write
from .route_depot import (
    DepotNotFoundError,
    get_depot_rows,
    replace_depot_rows,
    validate_direction,
)
from .route_document_data import (
    load_route_document_data,
    parse_document_options,
    parse_passport_options,
)
from .route_document_xlsx import (
    _xlsx_download_response,
    build_erm_workbook,
    build_schedule_workbook,
    erm_filename,
    schedule_filename,
)
from .route_passport_docx import (
    DOCX_MIME,
    build_route_passport,
    passport_filename,
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



@router.get("/routes/{route_id}/erm-export.xlsx")
def route_erm_export(
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
        workbook = build_erm_workbook(data, options)
        return _xlsx_download_response(workbook, erm_filename(data, options))
    finally:
        con.close()


@router.get("/routes/{route_id}/passport-document.docx")
def route_passport_document(
    route_id: int,
    style: str,
    effective_date: str,
    user=Depends(current_user),
):
    normalized_style = str(style).upper()
    try:
        if normalized_style not in ("D", "F"):
            raise ValueError("Оформление паспорта должно быть D или F")
        options = parse_passport_options(effective_date)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    con = db.connect()
    try:
        try:
            data = load_route_document_data(con, route_id)
        except ValueError as exc:
            status = 404 if str(exc) == "Маршрут не найден" else 400
            raise HTTPException(status, str(exc)) from exc
    finally:
        con.close()
    try:
        payload = build_route_passport(data, options, style=normalized_style)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    filename = passport_filename(data, options, normalized_style)
    disposition = (
        f"attachment; filename=route-passport-{normalized_style}.docx; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return Response(
        content=payload,
        media_type=DOCX_MIME,
        headers={"Content-Disposition": disposition},
    )
