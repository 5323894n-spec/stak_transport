# -*- coding: utf-8 -*-
"""Фотогалерея и документы технической карточки автобуса."""
import datetime
import secrets
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile

from . import db
from .api_repair_attachments import ALLOWED, MAX_BYTES, upload_root
from .auth import current_user
from .repair_service import audit_change, require_repair_action

router = APIRouter(prefix="/api/repairs", tags=["vehicle-media"])


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def media_row(con, media_id, *, include_cancelled=True):
    sql = "SELECT * FROM repair_attachments WHERE id=?"
    if not include_cancelled:
        sql += " AND cancelled_at IS NULL"
    item = db.one(con.execute(sql, (media_id,)))
    if not item:
        raise HTTPException(404, "Файл карточки автобуса не найден")
    item["download_url"] = f"/api/repairs/attachments/{media_id}/download"
    return item


def _linked_bus(con, table, object_id):
    if table == "vehicle_damages":
        row = con.execute(
            "SELECT vi.bus_id FROM vehicle_damages vd "
            "JOIN vehicle_incidents vi ON vi.id=vd.incident_id WHERE vd.id=?",
            (object_id,),
        ).fetchone()
    else:
        row = con.execute(
            f"SELECT bus_id FROM {table} WHERE id=?",
            (object_id,),
        ).fetchone()
    return row[0] if row else None


def validate_links(con, bus_id, *, request_id=0, order_id=0, incident_id=0, damage_id=0):
    if not con.execute("SELECT 1 FROM buses WHERE id=?", (bus_id,)).fetchone():
        raise HTTPException(404, "Автобус не найден")
    links = (
        ("repair_requests", request_id, "Заявка"),
        ("repair_orders", order_id, "Заказ-наряд"),
        ("vehicle_incidents", incident_id, "Событие"),
        ("vehicle_damages", damage_id, "Повреждение"),
    )
    for table, object_id, label in links:
        if not object_id:
            continue
        linked_bus = _linked_bus(con, table, object_id)
        if linked_bus is None:
            raise HTTPException(404, f"{label} не найдено")
        if linked_bus != bus_id:
            raise HTTPException(409, f"{label} относится к другому автобусу")


@router.get("/vehicles/{bus_id}/media")
def list_vehicle_media(
    bus_id: int,
    include_cancelled: bool = False,
    user=Depends(current_user),
):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM buses WHERE id=?", (bus_id,)).fetchone():
            raise HTTPException(404, "Автобус не найден")
        where = "bus_id=?"
        if not include_cancelled:
            where += " AND cancelled_at IS NULL"
        items = db.rows(con.execute(
            f"SELECT * FROM repair_attachments WHERE {where} "
            "ORDER BY is_cover DESC,COALESCE(captured_at,uploaded_at) DESC,id DESC",
            (bus_id,),
        ))
        for item in items:
            item["download_url"] = f"/api/repairs/attachments/{item['id']}/download"
        return {"items": items}
    finally:
        con.close()


@router.post("/vehicles/{bus_id}/media", status_code=201)
async def upload_vehicle_media(
    bus_id: int,
    file: UploadFile = File(...),
    category: str = Form(""),
    caption: str = Form(""),
    captured_at: str = Form(""),
    request_id: int = Form(0),
    order_id: int = Form(0),
    incident_id: int = Form(0),
    damage_id: int = Form(0),
    user=Depends(current_user),
):
    require_repair_action(user, "add_vehicle_media")
    original = Path(file.filename or "").name
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED or file.content_type not in ALLOWED[suffix]:
        raise HTTPException(400, "Недопустимый тип файла")

    con = db.connect()
    target = None
    try:
        validate_links(
            con,
            bus_id,
            request_id=request_id,
            order_id=order_id,
            incident_id=incident_id,
            damage_id=damage_id,
        )
        stored = secrets.token_hex(16) + suffix
        root = upload_root()
        target = (root / stored).resolve()
        if target.parent != root:
            raise HTTPException(400, "Недопустимый путь файла")
        size = 0
        with target.open("wb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Файл превышает 10 МБ")
                stream.write(chunk)
        media_id = con.execute(
            "INSERT INTO repair_attachments("
            "request_id,order_id,bus_id,incident_id,damage_id,category,caption,"
            "captured_at,original_name,stored_name,mime_type,size_bytes,uploaded_by"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                request_id or None,
                order_id or None,
                bus_id,
                incident_id or None,
                damage_id or None,
                category.strip(),
                caption.strip(),
                captured_at or None,
                original,
                stored,
                file.content_type,
                size,
                user["id"],
            ),
        ).lastrowid
        item = media_row(con, media_id)
        audit_change(
            con,
            user,
            "добавление файла карточки автобуса",
            "repair_attachment",
            media_id,
            new=item,
        )
        con.commit()
        return item
    except Exception:
        con.rollback()
        if target and target.exists():
            target.unlink()
        raise
    finally:
        await file.close()
        con.close()


@router.patch("/media/{media_id}")
def update_vehicle_media(
    media_id: int,
    payload: dict = Body(default={}),
    user=Depends(current_user),
):
    require_repair_action(user, "add_vehicle_media")
    con = db.connect()
    try:
        old = media_row(con, media_id, include_cancelled=False)
        values = [
            (key, str(payload[key] or "").strip() or None)
            for key in ("category", "caption", "captured_at")
            if key in payload
        ]
        if values:
            con.execute(
                "UPDATE repair_attachments SET "
                + ",".join(f"{key}=?" for key, _ in values)
                + " WHERE id=?",
                [value for _, value in values] + [media_id],
            )
        item = media_row(con, media_id)
        audit_change(
            con,
            user,
            "изменение файла карточки автобуса",
            "repair_attachment",
            media_id,
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


@router.post("/media/{media_id}/cover")
def set_vehicle_cover(media_id: int, user=Depends(current_user)):
    require_repair_action(user, "add_vehicle_media")
    con = db.connect()
    try:
        old = media_row(con, media_id, include_cancelled=False)
        if not str(old.get("mime_type") or "").startswith("image/"):
            raise HTTPException(400, "Обложкой может быть только изображение")
        con.execute(
            "UPDATE repair_attachments SET is_cover=0 WHERE bus_id=?",
            (old["bus_id"],),
        )
        con.execute(
            "UPDATE repair_attachments SET is_cover=1 WHERE id=?",
            (media_id,),
        )
        item = media_row(con, media_id)
        audit_change(
            con,
            user,
            "выбор обложки автобуса",
            "repair_attachment",
            media_id,
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


@router.post("/media/{media_id}/cancel")
def cancel_vehicle_media(
    media_id: int,
    payload: dict = Body(default={}),
    user=Depends(current_user),
):
    require_repair_action(user, "add_vehicle_media")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "Укажите причину отмены файла")
    con = db.connect()
    try:
        old = media_row(con, media_id, include_cancelled=False)
        con.execute(
            "UPDATE repair_attachments SET cancelled_at=?,cancel_reason=?,is_cover=0 "
            "WHERE id=?",
            (_now(), reason, media_id),
        )
        item = media_row(con, media_id)
        audit_change(
            con,
            user,
            "отмена файла карточки автобуса",
            "repair_attachment",
            media_id,
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
