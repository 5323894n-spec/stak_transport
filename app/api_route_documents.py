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
        return {"ok": True, "route_id": route_id, "direction": direction, "items": saved}
    finally:
        con.close()
