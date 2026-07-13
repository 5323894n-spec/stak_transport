# -*- coding: utf-8 -*-
"""Анализ повторных неисправностей."""
from fastapi import APIRouter, Depends
from . import db
from .auth import current_user

router = APIRouter(prefix="/api/repairs", tags=["repair-repeats"])

@router.get("/repeats")
def repeats(user=Depends(current_user)):
    con = db.connect()
    try:
        return {"items": db.rows(con.execute(
            "SELECT rr.*,rr.number request_number,prev.number previous_number,b.garage_number,b.plate "
            "FROM repair_requests rr JOIN repair_requests prev ON prev.id=rr.repeated_from_id "
            "JOIN buses b ON b.id=rr.bus_id WHERE rr.repeated=1 ORDER BY rr.created_at DESC,rr.id DESC"))}
    finally: con.close()
