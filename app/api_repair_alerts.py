# -*- coding: utf-8 -*-
"""Детерминированные уведомления ремонта и склада."""
import datetime
from fastapi import APIRouter, Body, Depends
from . import db
from .auth import current_user
from .repair_service import require_repair_action

router = APIRouter(prefix="/api/repairs/alerts", tags=["repair-alerts"])
ACTIVE = ("черновик", "диагностика", "ожидает запчасти", "готов к работе", "в работе", "приостановлен", "контроль")


def _notify_once(con, level, category, message, source_key):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    cur = con.execute(
        "INSERT OR IGNORE INTO notifications(ts,level,category,message,source_key) VALUES(?,?,?,?,?)",
        (now, level, category, message, source_key),
    )
    return cur.rowcount == 1


@router.post("/evaluate")
def evaluate_alerts(payload: dict = Body(default={}), user=Depends(current_user)):
    require_repair_action(user, "manage_order")
    check_date = datetime.date.fromisoformat(payload.get("date") or datetime.date.today().isoformat())
    cutoff = check_date.isoformat() + "T23:59:59"
    con = db.connect()
    overdue_created = low_stock_created = 0
    try:
        marks = ",".join("?" for _ in ACTIVE)
        orders = db.rows(con.execute(
            f"SELECT ro.id,ro.number,b.garage_number,ro.planned_end FROM repair_orders ro JOIN buses b ON b.id=ro.bus_id WHERE ro.status IN ({marks}) AND ro.planned_end IS NOT NULL AND ro.planned_end<?",
            (*ACTIVE, cutoff),
        ))
        for order in orders:
            message = f"Заказ-наряд {order['number']} по автобусу {order['garage_number']} просрочен: план {order['planned_end']}"
            if _notify_once(con, "warning", "просрочка ремонта", message, f"repair-overdue:{order['id']}"):
                overdue_created += 1
        parts = db.rows(con.execute(
            "SELECT id,code,name,stock_qty,min_qty FROM parts WHERE active=1 AND min_qty>0 AND stock_qty<min_qty ORDER BY name,id"
        ))
        for part in parts:
            message = f"Дефицит запчасти {part['code']} — {part['name']}: остаток {part['stock_qty']}, минимум {part['min_qty']}"
            if _notify_once(con, "warning", "дефицит запчастей", message, f"repair-low-stock:{part['id']}"):
                low_stock_created += 1
        con.commit()
        return {
            "orders_checked": len(orders), "parts_checked": len(parts),
            "overdue_created": overdue_created, "low_stock_created": low_stock_created,
            "notifications_created": overdue_created + low_stock_created,
        }
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()