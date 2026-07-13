# -*- coding: utf-8 -*-
"""Безопасные вложения заявок и заказ-нарядов ремонта."""
import os
import secrets
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from . import db
from .auth import current_user
from .repair_service import audit_change, require_repair_action

router = APIRouter(prefix="/api/repairs", tags=["repair-attachments"])
ALLOWED = {".pdf": {"application/pdf"}, ".jpg": {"image/jpeg"}, ".jpeg": {"image/jpeg"}, ".png": {"image/png"},
           ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
           ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}}
MAX_BYTES = 10 * 1024 * 1024

def upload_root():
    default = Path(__file__).resolve().parents[1] / "repair_uploads"
    root = Path(os.environ.get("ATP_REPAIR_UPLOADS") or default).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root

@router.get("/orders/{order_id}/attachments")
def list_attachments(order_id: int, user=Depends(current_user)):
    con = db.connect()
    try: return {"items": db.rows(con.execute("SELECT * FROM repair_attachments WHERE order_id=? ORDER BY uploaded_at DESC,id DESC", (order_id,)))}
    finally: con.close()

@router.post("/orders/{order_id}/attachments", status_code=201)
async def upload_attachment(order_id: int, file: UploadFile = File(...), category: str = Form(""), user=Depends(current_user)):
    require_repair_action(user, "manage_order")
    original = Path(file.filename or "").name
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED or file.content_type not in ALLOWED[suffix]: raise HTTPException(400, "Недопустимый тип файла")
    con = db.connect(); target = None
    try:
        order = db.one(con.execute("SELECT * FROM repair_orders WHERE id=?", (order_id,)))
        if not order: raise HTTPException(404, "Заказ-наряд не найден")
        stored = secrets.token_hex(16) + suffix
        root = upload_root(); target = (root / stored).resolve()
        if target.parent != root: raise HTTPException(400, "Недопустимый путь файла")
        size = 0
        with target.open("wb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES: raise HTTPException(413, "Файл превышает 10 МБ")
                stream.write(chunk)
        attachment_id = con.execute(
            "INSERT INTO repair_attachments(request_id,order_id,bus_id,category,original_name,stored_name,mime_type,size_bytes,uploaded_by) VALUES(?,?,?,?,?,?,?,?,?)",
            (order.get("request_id"), order_id, order["bus_id"], category, original, stored, file.content_type, size, user["id"])).lastrowid
        item = db.one(con.execute("SELECT * FROM repair_attachments WHERE id=?", (attachment_id,)))
        audit_change(con, user, "добавление вложения ремонта", "repair_attachment", attachment_id, new=item)
        con.commit(); return item
    except Exception:
        con.rollback()
        if target and target.exists(): target.unlink()
        raise
    finally:
        await file.close(); con.close()

@router.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: int, user=Depends(current_user)):
    con = db.connect()
    try: item = db.one(con.execute("SELECT * FROM repair_attachments WHERE id=?", (attachment_id,)))
    finally: con.close()
    if not item: raise HTTPException(404, "Вложение не найдено")
    root = upload_root(); path = (root / item["stored_name"]).resolve()
    if path.parent != root or not path.is_file(): raise HTTPException(404, "Файл вложения не найден")
    return FileResponse(path, media_type=item["mime_type"], filename=item["original_name"])
