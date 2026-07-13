# -*- coding: utf-8 -*-
"""АТП: система планирования, нарядов, путевых листов и учёта. Точка входа FastAPI."""
import os
from fastapi import FastAPI, Body, Request, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from . import db
from .auth import login as do_login, ensure_admin, current_user
from .api_refs import router as refs_router
from .api_planning import router as planning_router
from .api_waybills import router as waybills_router
from .api_time import router as time_router
from .api_summary import router as summary_router
from .api_repairs import router as repairs_router
from .api_repair_orders import router as repair_orders_router
from .api_repair_work import router as repair_work_router
from .api_repair_control import router as repair_control_router
from .api_repair_stock import router as repair_stock_router
from .api_repair_stock_extra import router as repair_stock_extra_router
from .api_repair_maintenance import router as repair_maintenance_router
from .api_repair_dashboard import router as repair_dashboard_router
from .api_vehicle_card import router as vehicle_card_router
from .repair_reports import router as repair_reports_router
from .api_repair_attachments import router as repair_attachments_router
from .api_repair_workers import router as repair_workers_router
from .repair_print import router as repair_print_router
from .api_repair_repeats import router as repair_repeats_router
from .api_repair_alerts import router as repair_alerts_router
from .api_vehicle_incidents import router as vehicle_incidents_router

app = FastAPI(title="АТП — планирование и путевые листы", version="1.0")

@app.on_event("startup")
def startup():
    db.init_db()
    con = db.connect()
    ensure_admin(con)
    con.close()

@app.post("/api/login")
def login(payload: dict = Body(...), request: Request = None):
    con = db.connect()
    try:
        res = do_login(con, payload.get("username", ""), payload.get("password", ""),
                       ip=request.client.host if request and request.client else "")
        if not res:
            raise HTTPException(401, "Неверное имя пользователя или пароль")
        return res
    finally:
        con.close()

@app.post("/api/logout")
def logout(user=Depends(current_user), request: Request = None):
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    con = db.connect()
    try:
        con.execute("DELETE FROM sessions WHERE token=?", (token,))
        con.commit()
        return {"ok": True}
    finally:
        con.close()

@app.get("/api/me")
def me(user=Depends(current_user)):
    return user

app.include_router(refs_router)
app.include_router(planning_router)
app.include_router(waybills_router)
app.include_router(time_router)
app.include_router(summary_router)
app.include_router(repairs_router)
app.include_router(repair_orders_router)
app.include_router(repair_work_router)
app.include_router(repair_control_router)
app.include_router(repair_stock_router)
app.include_router(repair_stock_extra_router)
app.include_router(repair_maintenance_router)
app.include_router(vehicle_card_router)
app.include_router(repair_dashboard_router)
app.include_router(repair_reports_router)
app.include_router(repair_attachments_router)
app.include_router(repair_workers_router)
app.include_router(repair_print_router)
app.include_router(repair_repeats_router)
app.include_router(repair_alerts_router)
app.include_router(vehicle_incidents_router)

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))
