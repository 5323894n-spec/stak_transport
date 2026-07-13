# -*- coding: utf-8 -*-
"""Общие бизнес-правила ремонта и технического обслуживания."""
import datetime
from fastapi import HTTPException
from . import db

REPAIR_ACTIONS = {
    "create_request": {"админ", "диспетчер", "механик", "мастер ремонта"},
    "manage_order": {"админ", "мастер ремонта"},
    "close_order": {"админ", "мастер ремонта"},
    "work_assignment": {"админ", "мастер ремонта", "слесарь"},
    "inspect": {"админ", "механик контроля"},
    "stock": {"админ", "склад"},
    "read_reports": {"админ", "руководитель"},
}
ALLOWED_TRANSITIONS = {
    "черновик": {"диагностика", "отменен"},
    "диагностика": {"ожидает запчасти", "готов к работе", "отменен"},
    "ожидает запчасти": {"готов к работе", "отменен"},
    "готов к работе": {"в работе", "отменен"},
    "в работе": {"приостановлен", "контроль", "отменен"},
    "приостановлен": {"в работе", "отменен"},
    "контроль": {"в работе", "завершен"},
    "завершен": set(), "отменен": set(),
}
ACTIVE_REPAIR_STATUSES = tuple(s for s in ALLOWED_TRANSITIONS if s not in {"завершен", "отменен"})

def require_repair_action(user, action):
    if user.get("role") not in REPAIR_ACTIONS.get(action, set()):
        raise HTTPException(403, "Недостаточно прав для выполнения операции ремонта")

def validate_transition(current, target, *, release_allowed=False):
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise HTTPException(409, "Недопустимый переход статуса")
    if target == "завершен" and not release_allowed:
        raise HTTPException(409, "Нет положительного контрольного осмотра")

def next_document_number(con, document_type, prefix, *, year=None):
    year = int(year or datetime.date.today().year)
    con.execute("INSERT OR IGNORE INTO repair_sequences(document_type,year,last_number) VALUES(?,?,0)", (document_type, year))
    con.execute("UPDATE repair_sequences SET last_number=last_number+1 WHERE document_type=? AND year=?", (document_type, year))
    number = con.execute("SELECT last_number FROM repair_sequences WHERE document_type=? AND year=?", (document_type, year)).fetchone()[0]
    return f"{prefix}-{year}-{number:06d}"

def active_repair_for_vehicle(con, bus_id):
    marks = ",".join("?" for _ in ACTIVE_REPAIR_STATUSES)
    return con.execute(f"SELECT * FROM repair_orders WHERE bus_id=? AND status IN ({marks}) ORDER BY created_at DESC,id DESC LIMIT 1", (bus_id, *ACTIVE_REPAIR_STATUSES)).fetchone()

def vehicle_release_block_reason(con, bus_id):
    repair = active_repair_for_vehicle(con, bus_id)
    return f"Автобус находится в ремонте по заказ-наряду {repair['number']}" if repair else ""

def calculate_cost(*, labor=0, parts=0, external=0, other=0):
    return sum(float(v or 0) for v in (labor, parts, external, other))

def calculate_downtime(started_at, ended_at):
    if not started_at or not ended_at:
        return 0
    start = datetime.datetime.fromisoformat(started_at)
    end = datetime.datetime.fromisoformat(ended_at)
    return max(0, round((end - start).total_seconds() / 3600, 2))

def record_order_status_event(con, order_id, status, *, user_id=None, changed_at=None):
    changed_at = changed_at or datetime.datetime.now().isoformat(timespec="seconds")
    current = con.execute(
        "SELECT id,status FROM repair_order_status_events WHERE order_id=? AND left_at IS NULL ORDER BY id DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    if current and current["status"] == status:
        return current["id"]
    con.execute(
        "UPDATE repair_order_status_events SET left_at=? WHERE order_id=? AND left_at IS NULL",
        (changed_at, order_id),
    )
    return con.execute(
        "INSERT INTO repair_order_status_events(order_id,status,entered_at,changed_by) VALUES(?,?,?,?)",
        (order_id, status, changed_at, user_id),
    ).lastrowid

def audit_change(con, user, action, object_type, object_id, *, old=None, new=None, comment=""):
    db.audit(con, user.get("username", ""), action, object_type, object_id, old=old, new=new, comment=comment)
