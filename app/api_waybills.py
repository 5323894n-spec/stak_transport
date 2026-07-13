# -*- coding: utf-8 -*-
"""Медосмотры, техконтроль, путевые листы (Приказ № 390), журнал, печатные формы."""
import datetime
from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import HTMLResponse
from . import db, norms as N
from .auth import current_user, require_write
from .xl import xlsx_response
from .repair_service import vehicle_release_block_reason

router = APIRouter(prefix="/api")

def now_time(): return datetime.datetime.now().strftime("%H:%M")
def now_iso(): return datetime.datetime.now().isoformat(timespec="seconds")

# ---------- Медосмотры ----------
@router.get("/medical")
def medical_list(date: str, user=Depends(current_user)):
    con = db.connect()
    try:
        return {"items": db.rows(con.execute(
            "SELECT m.*, d.fio, d.tab_number FROM medical_checks m JOIN drivers d ON d.id=m.driver_id "
            "WHERE m.date=? ORDER BY m.time DESC", (date,)))}
    finally:
        con.close()

@router.post("/medical")
def medical_create(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "medical")
    con = db.connect()
    try:
        cur = con.execute(
            "INSERT INTO medical_checks(driver_id,date,time,type,result,medic_name,org,comment) VALUES(?,?,?,?,?,?,?,?)",
            (payload["driver_id"], payload.get("date") or datetime.date.today().isoformat(),
             payload.get("time") or now_time(), payload.get("type", "предрейсовый"),
             payload.get("result", "допущен"), payload.get("medic_name") or user["full_name"],
             payload.get("org", ""), payload.get("comment", "")))
        if payload.get("result") == "не допущен":
            drv = db.one(con.execute("SELECT fio FROM drivers WHERE id=?", (payload["driver_id"],)))
            db.notify(con, "error", "медосмотр", f"{drv['fio']} НЕ ДОПУЩЕН по результатам медосмотра — выпуск путевого листа заблокирован, нужна замена")
        db.audit(con, user["username"], "медосмотр", "medical_checks", cur.lastrowid, new=payload)
        con.commit()
        return {"id": cur.lastrowid}
    finally:
        con.close()

# ---------- Техконтроль ----------
@router.get("/tech")
def tech_list(date: str, user=Depends(current_user)):
    con = db.connect()
    try:
        return {"items": db.rows(con.execute(
            "SELECT t.*, b.garage_number, b.plate FROM tech_checks t JOIN buses b ON b.id=t.bus_id "
            "WHERE t.date=? ORDER BY t.time DESC", (date,)))}
    finally:
        con.close()

@router.post("/tech")
def tech_create(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "tech")
    con = db.connect()
    try:
        if payload.get("result", "выпуск разрешен") == "выпуск разрешен":
            reason = vehicle_release_block_reason(con, payload["bus_id"])
            if reason: raise HTTPException(409, reason)
        cur = con.execute(
            "INSERT INTO tech_checks(bus_id,date,time,result,odometer,notes,mechanic_name,comment) VALUES(?,?,?,?,?,?,?,?)",
            (payload["bus_id"], payload.get("date") or datetime.date.today().isoformat(),
             payload.get("time") or now_time(), payload.get("result", "выпуск разрешен"),
             payload.get("odometer"), payload.get("notes", ""),
             payload.get("mechanic_name") or user["full_name"], payload.get("comment", "")))
        if payload.get("odometer"):
            con.execute("UPDATE buses SET odometer=? WHERE id=?", (payload["odometer"], payload["bus_id"]))
        if payload.get("result") == "выпуск запрещен":
            b = db.one(con.execute("SELECT garage_number, plate FROM buses WHERE id=?", (payload["bus_id"],)))
            con.execute("UPDATE buses SET status='в ремонте' WHERE id=?", (payload["bus_id"],))
            db.notify(con, "error", "техконтроль", f"Автобус {b['garage_number']} ({b['plate']}) — ВЫПУСК ЗАПРЕЩЁН, переведён в ремонт")
        db.audit(con, user["username"], "техконтроль", "tech_checks", cur.lastrowid, new=payload)
        con.commit()
        return {"id": cur.lastrowid}
    finally:
        con.close()

# ---------- Путевые листы ----------
def _line_full(con, lid):
    return db.one(con.execute(
        "SELECT l.*, o.date AS odate, o.status AS ostatus, r.number AS route_number, r.name AS route_name, "
        "r.comm_type, r.transport_type, d.fio, d.tab_number, d.license_number, d.license_issued, d.snils, "
        "b.garage_number, b.plate, b.brand, b.model, b.odometer, b.fuel_balance, b.fuel_type "
        "FROM order_lines l JOIN orders o ON o.id=l.order_id LEFT JOIN routes r ON r.id=l.route_id "
        "LEFT JOIN drivers d ON d.id=l.driver_id LEFT JOIN buses b ON b.id=l.bus_id WHERE l.id=?", (lid,)))

WAYBILL_MODE_STRICT = "strict_med_tech"
WAYBILL_MODE_MEDICAL_ONLY = "medical_only"
WAYBILL_MODE_ADVISORY = "advisory"
WAYBILL_MODES = {WAYBILL_MODE_STRICT, WAYBILL_MODE_MEDICAL_ONLY, WAYBILL_MODE_ADVISORY}


def waybill_issue_mode(con):
    mode = (db.get_settings(con).get("waybill_issue_mode") or WAYBILL_MODE_STRICT).strip()
    return mode if mode in WAYBILL_MODES else WAYBILL_MODE_STRICT


def _append_issue(mode, problems, warnings, message, *, control, result=None):
    if control == "medical":
        target = warnings if mode == WAYBILL_MODE_ADVISORY else problems
    elif mode == WAYBILL_MODE_STRICT:
        target = problems
    elif mode == WAYBILL_MODE_MEDICAL_ONLY and result == "выпуск запрещен":
        target = problems
    else:
        target = warnings
    target.append(message)


def waybill_check(con, line, mode=None):
    """Проверка возможности оформления ПЛ: problems блокируют, warnings только предупреждают."""
    mode = mode or waybill_issue_mode(con)
    problems, warnings = [], []
    if line and line["bus_id"]:
        repair_reason = vehicle_release_block_reason(con, line["bus_id"])
        if repair_reason: problems.append(repair_reason)
    med = tech = None
    if not line:
        return {"mode": mode, "problems": ["Строка наряда не найдена"], "warnings": [], "medical": None, "tech": None}
    if line["ostatus"] not in ("утвержден", "выдан", "скорректирован"):
        problems.append(f"Наряд не утверждён (статус: {line['ostatus']})")
    if not line["driver_id"]:
        problems.append("Не назначен водитель")
    if not line["bus_id"]:
        problems.append("Не назначен автобус")
    if line["driver_id"]:
        if not line["license_number"]:
            problems.append("В карточке водителя не заполнено водительское удостоверение")
        if not line["snils"]:
            problems.append("В карточке водителя не заполнен СНИЛС")
        drv = db.one(con.execute("SELECT license_expires FROM drivers WHERE id=?", (line["driver_id"],)))
        if drv and drv["license_expires"] and drv["license_expires"] < line["odate"]:
            problems.append("Истёк срок действия водительского удостоверения")
        med = db.one(con.execute(
            "SELECT * FROM medical_checks WHERE driver_id=? AND date=? AND type IN ('предрейсовый','предсменный') "
            "ORDER BY id DESC LIMIT 1", (line["driver_id"], line["odate"])))
        if not med:
            _append_issue(mode, problems, warnings, "Нет предрейсового медицинского осмотра", control="medical")
        elif med["result"] != "допущен":
            _append_issue(mode, problems, warnings, "Водитель не допущен по медосмотру", control="medical")
    if line["bus_id"]:
        if not line["plate"]:
            problems.append("У автобуса не заполнен госномер")
        tech = db.one(con.execute(
            "SELECT * FROM tech_checks WHERE bus_id=? AND date=? ORDER BY id DESC LIMIT 1",
            (line["bus_id"], line["odate"])))
        if not tech:
            _append_issue(mode, problems, warnings, "Нет предрейсового технического контроля", control="tech")
        elif tech["result"] != "выпуск разрешен":
            msg = "Техконтроль: выпуск запрещён" if tech["result"] == "выпуск запрещен" else f"Техконтроль: {tech['result']}"
            _append_issue(mode, problems, warnings, msg, control="tech", result=tech["result"])
    return {"mode": mode, "problems": problems, "warnings": warnings, "medical": med, "tech": tech}


def waybill_blockers(con, line):
    """Обратная совместимость: только блокирующие причины."""
    return waybill_check(con, line)["problems"]


def _valid_med_id(check):
    med = check.get("medical")
    return med["id"] if med and med["result"] == "допущен" else None


def _valid_tech_id(check):
    tech = check.get("tech")
    return tech["id"] if tech and tech["result"] == "выпуск разрешен" else None


@router.get("/waybills/precheck/{lid}")
def waybill_precheck(lid: int, user=Depends(current_user)):
    con = db.connect()
    try:
        check = waybill_check(con, _line_full(con, lid))
        return {"mode": check["mode"], "problems": check["problems"], "warnings": check["warnings"]}
    finally:
        con.close()


@router.post("/waybills/from-line/{lid}")
def waybill_create(lid: int, user=Depends(current_user)):
    require_write(user, "waybills")
    con = db.connect()
    try:
        line = _line_full(con, lid)
        check = waybill_check(con, line)
        if check["problems"]:
            raise HTTPException(409, "Путевой лист не оформлен. Не выполнено: " + "; ".join(check["problems"]))
        ex = db.one(con.execute("SELECT id, number FROM waybills WHERE order_line_id=? AND status!='аннулирован'", (lid,)))
        if ex:
            raise HTTPException(409, f"По этой строке уже оформлен путевой лист № {ex['number']}")
        num = (db.one(con.execute("SELECT MAX(number) m FROM waybills")) or {}).get("m") or 0
        num += 1
        cur = con.execute(
            "INSERT INTO waybills(number,order_line_id,date,valid_to,driver_id,bus_id,route_id,output_number,"
            "depart_plan,return_plan,odo_start,fuel_start,fuel_plan,medical_check_id,tech_check_id,"
            "status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (num, lid, line["odate"], line["odate"], line["driver_id"], line["bus_id"], line["route_id"],
             line["output_number"], line["depart_depot"], line["return_depot"],
             line["odometer"], line["fuel_balance"], line["planned_fuel"], _valid_med_id(check), _valid_tech_id(check),
             "оформлен", user["username"], now_iso()))
        con.execute("UPDATE order_lines SET status='выдан' WHERE id=?", (lid,))
        db.audit(con, user["username"], "оформление путевого листа", "waybills", num,
                 new={"строка наряда": lid, "дата": line["odate"], "водитель": line["fio"], "автобус": line["garage_number"],
                      "режим": check["mode"], "предупреждения": check["warnings"]})
        con.commit()
        return {"id": cur.lastrowid, "number": num, "mode": check["mode"], "warnings": check["warnings"]}
    finally:
        con.close()


@router.post("/waybills/from-order/{date}")
def waybills_from_order(date: str, user=Depends(current_user)):
    """Массовое оформление путевых листов по всему утверждённому наряду."""
    require_write(user, "waybills")
    con = db.connect()
    try:
        o = db.one(con.execute("SELECT * FROM orders WHERE date=?", (date,)))
        if not o:
            raise HTTPException(404, "Наряда на дату нет")
        mode = waybill_issue_mode(con)
        created, blocked, warnings = [], [], []
        for l in db.rows(con.execute("SELECT id FROM order_lines WHERE order_id=? AND status!='отменен'", (o["id"],))):
            line = _line_full(con, l["id"])
            if db.one(con.execute("SELECT 1 FROM waybills WHERE order_line_id=? AND status!='аннулирован'", (l["id"],))):
                continue
            check = waybill_check(con, line, mode)
            label = f"{line['route_number']}/вых.{line['output_number']}/см.{line['shift_number']}"
            if check["problems"]:
                blocked.append({"line": label, "fio": line["fio"], "problems": check["problems"]})
                continue
            if check["warnings"]:
                warnings.append({"line": label, "fio": line["fio"], "warnings": check["warnings"]})
            num = ((db.one(con.execute("SELECT MAX(number) m FROM waybills")) or {}).get("m") or 0) + 1
            con.execute(
                "INSERT INTO waybills(number,order_line_id,date,valid_to,driver_id,bus_id,route_id,output_number,"
                "depart_plan,return_plan,odo_start,fuel_start,fuel_plan,medical_check_id,tech_check_id,"
                "status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (num, l["id"], date, date, line["driver_id"], line["bus_id"], line["route_id"],
                 line["output_number"], line["depart_depot"], line["return_depot"],
                 line["odometer"], line["fuel_balance"], line["planned_fuel"], _valid_med_id(check), _valid_tech_id(check),
                 "оформлен", user["username"], now_iso()))
            con.execute("UPDATE order_lines SET status='выдан' WHERE id=?", (l["id"],))
            created.append({"line": label, "number": num, "fio": line["fio"], "warnings": check["warnings"]})
        if o["status"] == "утвержден" and created:
            con.execute("UPDATE orders SET status='выдан' WHERE id=?", (o["id"],))
        db.audit(con, user["username"], "массовое оформление ПЛ", "orders", o["id"],
                 comment=f"{date}: оформлено {len(created)}, заблокировано {len(blocked)}, предупреждений {len(warnings)}, режим {mode}")
        for b in blocked:
            db.notify(con, "warning", "путевой лист", f"{date} {b['line']} {b['fio'] or ''}: не оформлен ПЛ — {'; '.join(b['problems'])}")
        con.commit()
        return {"mode": mode, "created": created, "blocked": blocked, "warnings": warnings}
    finally:
        con.close()
@router.put("/waybills/{wid}/close")
def waybill_close(wid: int, payload: dict = Body(...), user=Depends(current_user)):
    """Закрытие путевого листа: фактические времена, одометр, топливо."""
    require_write(user, "waybills")
    con = db.connect()
    try:
        w = db.one(con.execute("SELECT * FROM waybills WHERE id=?", (wid,)))
        if not w: raise HTTPException(404, "Путевой лист не найден")
        if w["status"] == "аннулирован": raise HTTPException(409, "Путевой лист аннулирован")
        odo_end = float(payload.get("odo_end") or 0)
        if odo_end and w["odo_start"] and odo_end < w["odo_start"]:
            raise HTTPException(400, "Показание одометра при возвращении меньше, чем при выезде")
        distance = round(odo_end - (w["odo_start"] or 0), 1) if odo_end else (payload.get("distance") or 0)
        fuel_given = float(payload.get("fuel_given") or 0)
        fuel_end = payload.get("fuel_end")
        fuel_fact = None
        if fuel_end is not None and w["fuel_start"] is not None:
            fuel_fact = round((w["fuel_start"] or 0) + fuel_given - float(fuel_end), 1)
        con.execute(
            "UPDATE waybills SET depart_fact=?, return_fact=?, odo_end=?, distance=?, fuel_given=?, "
            "fuel_end=?, fuel_fact=?, status='выполнен', closed_at=?, comment=? WHERE id=?",
            (payload.get("depart_fact") or w["depart_plan"], payload.get("return_fact") or w["return_plan"],
             odo_end or None, distance, fuel_given, fuel_end, fuel_fact, now_iso(),
             payload.get("comment", ""), wid))
        # обновляем автобус
        if odo_end:
            con.execute("UPDATE buses SET odometer=? WHERE id=?", (odo_end, w["bus_id"]))
        if fuel_end is not None:
            con.execute("UPDATE buses SET fuel_balance=? WHERE id=?", (float(fuel_end), w["bus_id"]))
        # топливная запись
        bus = db.one(con.execute("SELECT * FROM buses WHERE id=?", (w["bus_id"],)))
        rate = bus["fuel_rate"] or 0
        k = bus["winter_coeff"] if datetime.date.fromisoformat(w["date"]).month in (11,12,1,2,3) else 1.0
        plan = round(distance * rate * (k or 1) / 100.0, 1) if distance else (w["fuel_plan"] or 0)
        saving = overrun = 0.0
        if fuel_fact is not None:
            diff = round(plan - fuel_fact, 1)
            saving, overrun = (diff, 0.0) if diff >= 0 else (0.0, -diff)
        con.execute(
            "INSERT INTO fuel_records(date,bus_id,driver_id,route_id,waybill_id,kind,distance,rate,plan_litres,"
            "fact_litres,given_litres,start_balance,end_balance,saving,overrun,responsible) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (w["date"], w["bus_id"], w["driver_id"], w["route_id"], wid, "рейс", distance, rate, plan,
             fuel_fact or 0, fuel_given, w["fuel_start"], fuel_end, saving, overrun, user["username"]))
        # контроль аномалий
        if fuel_fact is not None and plan and fuel_fact > plan * 1.15:
            d = db.one(con.execute("SELECT fio FROM drivers WHERE id=?", (w["driver_id"],)))
            db.notify(con, "error", "топливо",
                      f"ПЛ №{w['number']} {w['date']}: перерасход топлива {round(fuel_fact-plan,1)} л (факт {fuel_fact} при норме {plan}) — {d['fio']}, автобус {bus['garage_number']}")
        if fuel_end is not None and float(fuel_end) < 0:
            db.notify(con, "error", "топливо", f"ПЛ №{w['number']}: отрицательный остаток топлива!")
        line = db.one(con.execute("SELECT id FROM order_lines WHERE id=?", (w["order_line_id"],)))
        if line: con.execute("UPDATE order_lines SET status='выполнен' WHERE id=?", (w["order_line_id"],))
        db.audit(con, user["username"], "закрытие путевого листа", "waybills", w["number"], new=payload)
        con.commit()
        return {"ok": True, "distance": distance, "fuel_fact": fuel_fact, "plan": plan,
                "saving": saving, "overrun": overrun}
    finally:
        con.close()

@router.post("/waybills/{wid}/cancel")
def waybill_cancel(wid: int, payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "waybills")
    if not payload.get("reason"): raise HTTPException(400, "Укажите причину аннулирования")
    con = db.connect()
    try:
        w = db.one(con.execute("SELECT * FROM waybills WHERE id=?", (wid,)))
        con.execute("UPDATE waybills SET status='аннулирован', cancel_reason=? WHERE id=?", (payload["reason"], wid))
        if w["order_line_id"]:
            con.execute("UPDATE order_lines SET status='план' WHERE id=?", (w["order_line_id"],))
        db.audit(con, user["username"], "аннулирование путевого листа", "waybills", w["number"],
                 comment=payload["reason"])
        con.commit()
        return {"ok": True}
    finally:
        con.close()

@router.get("/waybills")
def waybills_journal(date_from: str = "", date_to: str = "", driver_id: int = 0, bus_id: int = 0,
                     route_id: int = 0, status: str = "", number: str = "", user=Depends(current_user)):
    con = db.connect()
    try:
        q = ("SELECT w.*, d.fio, d.tab_number, b.garage_number, b.plate, r.number AS route_number "
             "FROM waybills w LEFT JOIN drivers d ON d.id=w.driver_id LEFT JOIN buses b ON b.id=w.bus_id "
             "LEFT JOIN routes r ON r.id=w.route_id WHERE 1=1")
        args = []
        if date_from: q += " AND w.date>=?"; args.append(date_from)
        if date_to: q += " AND w.date<=?"; args.append(date_to)
        if driver_id: q += " AND w.driver_id=?"; args.append(driver_id)
        if bus_id: q += " AND w.bus_id=?"; args.append(bus_id)
        if route_id: q += " AND w.route_id=?"; args.append(route_id)
        if status: q += " AND w.status=?"; args.append(status)
        if number: q += " AND w.number=?"; args.append(number)
        items = db.rows(con.execute(q + " ORDER BY w.number DESC LIMIT 1000", args))
        # контроль сквозной нумерации
        nums = [r["number"] for r in con.execute("SELECT number FROM waybills ORDER BY number")]
        gaps = []
        for a, b in zip(nums, nums[1:]):
            if b - a > 1: gaps += list(range(a + 1, min(b, a + 4)))
        return {"items": items, "numbering_gaps": gaps[:20]}
    finally:
        con.close()

@router.get("/waybills/export.xlsx")
def waybills_export(date_from: str = "", date_to: str = "", user=Depends(current_user)):
    con = db.connect()
    try:
        q = ("SELECT w.number, w.date, d.fio, d.tab_number, b.garage_number, b.plate, r.number rn, w.output_number, "
             "w.depart_fact, w.return_fact, w.distance, w.fuel_fact, w.status, w.created_by, w.cancel_reason "
             "FROM waybills w LEFT JOIN drivers d ON d.id=w.driver_id LEFT JOIN buses b ON b.id=w.bus_id "
             "LEFT JOIN routes r ON r.id=w.route_id WHERE 1=1")
        args = []
        if date_from: q += " AND w.date>=?"; args.append(date_from)
        if date_to: q += " AND w.date<=?"; args.append(date_to)
        items = con.execute(q + " ORDER BY w.number", args).fetchall()
        headers = ["№ ПЛ","Дата","Водитель","Таб.№","Гар.№","Госномер","Маршрут","Выход","Выезд","Возврат",
                   "Пробег, км","Расход, л","Статус","Оформил","Причина аннулирования"]
        return xlsx_response("Журнал путевых листов", headers, [list(r) for r in items],
                             filename=f"waybills_{date_from}_{date_to}.xlsx")
    finally:
        con.close()

# ---------- Печатные формы ----------
PRINT_CSS = """<style>
@page { size: A4; margin: 8mm; }
body { font-family: 'Times New Roman', serif; font-size: 11px; margin: 12px; color: #000; }
.h { text-align: center; font-weight: bold; font-size: 15px; margin: 2px 0; }
.sub { text-align: center; font-size: 10px; }
table { border-collapse: collapse; width: 100%; margin: 4px 0; }
td, th { border: 1px solid #000; padding: 2px 4px; vertical-align: top; }
.nb td, .nb th { border: none; }
.small { font-size: 9px; color: #333; }
.sig { margin-top: 6px; }
.dup { color: #b00; border: 2px solid #b00; display: inline-block; padding: 2px 10px;
       font-weight: bold; transform: rotate(-4deg); position: absolute; right: 40px; top: 30px; font-size: 16px;}
.stamp { border: 2px solid #000; display: inline-block; padding: 3px 12px; font-weight: bold; }
@media print { .noprint { display: none; } }
</style>"""

def esc(x): return "" if x is None else str(x).replace("&", "&amp;").replace("<", "&lt;")

WB_CSS = """<style>
@page { size: A4 landscape; margin: 5mm; }
body { font-family: 'Times New Roman', serif; font-size: 9px; color: #000; margin: 8px; }
.page { page-break-after: always; position: relative; }
.page:last-child { page-break-after: auto; }
table { border-collapse: collapse; width: 100%; }
td, th { border: 1px solid #000; padding: 1px 3px; vertical-align: top; font-size: 9px; }
th { text-align: center; font-weight: normal; }
.nb, .nb td, .nb th { border: none !important; }
.grid3 { display: grid; grid-template-columns: 30% 39% 29%; gap: 4mm; align-items: start; }
.sec { margin-bottom: 3px; }
.u { border-bottom: 1px solid #000; display: inline-block; min-width: 40px; padding: 0 3px; }
.lbl { font-size: 7px; text-align: center; color: #000; }
.h1 { font-weight: bold; font-size: 13px; }
.c { text-align: center; }
.b { font-weight: bold; }
.small { font-size: 7.5px; }
.stamp { border: 1.5px solid #000; display: inline-block; padding: 2px 8px; font-weight: bold; font-size: 9px; letter-spacing: 1px; }
.dup { color: #b00; border: 2px solid #b00; display: inline-block; padding: 2px 10px; font-weight: bold;
  transform: rotate(-4deg); position: absolute; right: 60px; top: 14px; font-size: 14px; z-index: 9; background:#fff; }
@media print { .noprint { display: none; } }
</style>"""

@router.get("/waybills/{wid}/print", response_class=HTMLResponse)
def waybill_print(wid: int, duplicate: int = 0, user=Depends(current_user)):
    con = db.connect()
    try:
        w = db.one(con.execute(
            "SELECT w.*, d.fio, d.tab_number, d.snils, d.license_number, d.license_issued, "
            "b.garage_number, b.plate, b.brand, b.model, b.fuel_type, r.number AS rn, r.name AS route_name, "
            "r.comm_type, r.transport_type FROM waybills w LEFT JOIN drivers d ON d.id=w.driver_id "
            "LEFT JOIN buses b ON b.id=w.bus_id LEFT JOIN routes r ON r.id=w.route_id WHERE w.id=?", (wid,)))
        if not w: raise HTTPException(404, "Не найден")
        st = db.get_settings(con)
        med = db.one(con.execute("SELECT * FROM medical_checks WHERE id=?", (w["medical_check_id"],))) or {}
        tech = db.one(con.execute("SELECT * FROM tech_checks WHERE id=?", (w["tech_check_id"],))) or {}
        rost = db.one(con.execute("SELECT night_hours, shift_number FROM roster WHERE driver_id=? AND date=?",
                                  (w["driver_id"], w["date"]))) or {}
        line = db.one(con.execute("SELECT * FROM order_lines WHERE id=?", (w["order_line_id"],))) or {}
        con.execute("UPDATE waybills SET printed_at=?, print_count=print_count+1 WHERE id=?", (now_iso(), wid))
        db.audit(con, user["username"], "печать дубликата ПЛ" if duplicate else "печать ПЛ", "waybills", w["number"])
        con.commit()
        from .api_planning import sched_day_type
        trips = db.rows(con.execute(
            "SELECT * FROM route_trips WHERE route_id=? AND output_number=? AND day_type=? ORDER BY dep_time",
            (w["route_id"], w["output_number"], sched_day_type(con, w["date"])))) if w["route_id"] else []
        d = datetime.date.fromisoformat(w["date"]).strftime("%d.%m.%Y")
        def v(x, dash=""):
            return esc(x) if x not in (None, "") else dash
        shift1 = (rost.get("shift_number") or line.get("shift_number") or 1) == 1
        night_h = rost.get("night_hours") or ""
        saving = overrun = ""
        if w["fuel_fact"] is not None and w["fuel_plan"]:
            diff = round((w["fuel_plan"] or 0) - w["fuel_fact"], 1)
            saving, overrun = (diff, "") if diff >= 0 else ("", -diff)
        marks = ("<div class='dup'>ДУБЛИКАТ</div>" if duplicate else "") + \
                (f"<div class='dup' style='top:44px'>АННУЛИРОВАН: {esc(w['cancel_reason'])}</div>"
                 if w["status"] == "аннулирован" else "")

        col1 = f"""
<div class="sec" style="border:1px solid #000; padding:2px 4px; min-height:34px" >
  <span class="small">Место для штампа организации</span></div>
<div class="sec">Организация <span class="u" style="min-width:70%">{v(st.get('org_name'))}</span>
  <div class="lbl">(наименование)</div>
  <span class="u" style="min-width:99%">{v(st.get('org_address'))}, тел. {v(st.get('org_phone'))}</span>
  <div class="lbl">(адрес, номер телефона)</div>
  ОГРН <span class="u">{v(st.get('org_ogrn'))}</span> ИНН <span class="u">{v(st.get('org_inn'))}</span></div>
<div class="sec">Собственник, владелец <span class="u" style="min-width:60%">{v(st.get('org_owner') or st.get('org_name'))}</span></div>
<div class="sec">прошел предрейсовый контроль технического состояния —
  контролер технического состояния транспортных средств<br>
  <span class="u" style="min-width:26%">&nbsp;</span>
  <span class="u" style="min-width:30%">{d} {v(tech.get('time'))}</span>
  <span class="u" style="min-width:36%">{v(tech.get('mechanic_name'))}</span>
  <div class="lbl">(подпись) &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; (дата, время) &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; (расшифровка подписи)</div>
  <b>Результат: {v(tech.get('result'))}</b></div>
<div class="sec">Автобус, кассы, АСКП, переговорное устройство в исправном состоянии, указатели установлены.<br>
  Принял водитель <span class="u" style="min-width:24%">&nbsp;</span>
  <span class="u" style="min-width:40%">{v(w['fio'])}</span>
  <div class="lbl">(подпись) &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; (расшифровка подписи)</div></div>
<div class="sec">Топливная карта № <span class="u" style="min-width:50%">&nbsp;</span></div>
<div class="sec">Место проведения контроля технического состояния автобуса:<br>
  <span class="u" style="min-width:98%">{v(st.get('org_control_place') or st.get('org_address'))}</span></div>
<div class="sec b">Отметка о сдаче/приеме автобуса</div>
<div class="sec">Сдал водитель <span class="u" style="min-width:20%">&nbsp;</span>
  <span class="u" style="min-width:38%">{v(w['fio'])}</span><br>
  принял контролер технического состояния <span class="u" style="min-width:38%">&nbsp;</span></div>
<div class="sec"><table>
  <tr><th style="width:40%">Показание одометра</th><th>км</th><th style="width:30%">Подпись</th></tr>
  <tr><td>При выезде</td><td class="c">{v(w['odo_start'])}</td><td></td></tr>
  <tr><td>При возвращении</td><td class="c">{v(w['odo_end'])}</td><td></td></tr></table></div>
<div class="sec"><b>Отметка о состоянии здоровья водителя</b>
  <table><tr><th></th><th>I смена</th><th>II смена</th><th>Подпись</th></tr>
  <tr><td>При выезде</td><td class="c">{v(med.get('result')) if shift1 else ''}</td>
      <td class="c">{'' if shift1 else v(med.get('result'))}</td><td></td></tr>
  <tr><td>При возвращении</td><td></td><td></td><td></td></tr></table>
  <div class="small">Медработник: {v(med.get('medic_name'))} {('(' + esc(med.get('org')) + ')') if med.get('org') else ''}, {d} {v(med.get('time'))}</div></div>
<div class="sec">М.П. или штампа</div>"""

        col2 = f"""
<table class="sec"><tr><th>Марка автобуса</th><th>Государственный номерной знак</th><th>Гаражный номер</th></tr>
<tr><td class="c">{v(w['brand'])} {v(w['model'])}</td><td class="c b">{v(w['plate'])}</td><td class="c b">{v(w['garage_number'])}</td></tr></table>
<div class="sec"><b>Вид сообщения:</b> {v(w['comm_type'])}</div>
<table class="sec"><tr><th rowspan="2">Фамилия, имя, отчество</th><th rowspan="2">Табельный номер</th>
  <th colspan="2">Водительское удостоверение</th></tr>
  <tr><th>Номер</th><th>Дата выдачи</th></tr>
  <tr><td>{v(w['fio'])}<br><span class="small">СНИЛС: {v(w['snils'])}</span></td>
      <td class="c">{v(w['tab_number'])}</td><td class="c">{v(w['license_number'])}</td><td class="c">{v(w['license_issued'])}</td></tr>
  <tr><td><br><span class="small">СНИЛС:</span></td><td></td><td></td><td></td></tr></table>
<div class="sec"><b>Вид перевозки:</b> {v(w['transport_type'])}</div>
<div class="sec"><b>Наименование, номер маршрута:</b> № {v(w['rn'])} {v(w['route_name'])} (выход {v(w['output_number'])})</div>
<div class="sec">Лицензионная карточка: стандартная, ограниченная <span class="small">(ненужное зачеркнуть)</span><br>
  Регистрационный № <span class="u">{v(st.get('org_license_reg'))}</span>
  Серия <span class="u">{v(st.get('org_license_series'))}</span> № <span class="u">{v(st.get('org_license_number'))}</span></div>
<div class="sec">Подача по заказу: Заказчик <span class="u" style="min-width:30%">&nbsp;</span>
  место подачи <span class="u" style="min-width:24%">&nbsp;</span><br>
  с ___ ч. ___ мин. до ___ ч. ___ мин.</div>
<div class="sec b">Выезд и возвращение автобуса</div>
<table class="sec"><tr><th rowspan="2">Смена</th><th colspan="2">По расписанию</th><th colspan="2">Фактически</th></tr>
  <tr><th>выезд</th><th>возвращение</th><th>выезд</th><th>возвращение</th></tr>
  <tr><td>Первая</td><td class="c">{v(w['depart_plan']) if shift1 else ''}</td><td class="c">{v(w['return_plan']) if shift1 else ''}</td>
      <td class="c">{v(w['depart_fact']) if shift1 else ''}</td><td class="c">{v(w['return_fact']) if shift1 else ''}</td></tr>
  <tr><td>Вторая</td><td class="c">{'' if shift1 else v(w['depart_plan'])}</td><td class="c">{'' if shift1 else v(w['return_plan'])}</td>
      <td class="c">{'' if shift1 else v(w['depart_fact'])}</td><td class="c">{'' if shift1 else v(w['return_fact'])}</td></tr></table>
<div class="sec">Диспетчер <span class="u" style="min-width:22%">&nbsp;</span>
  <span class="u" style="min-width:40%">{v(w['created_by'])}</span>
  <div class="lbl">(подпись) &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; (расшифровка подписи)</div></div>
<div class="sec b">Простой по техническим и прочим причинам</div>
<div class="sec">В гараже: с ___ ч. ___ мин. до ___ ч. ___ мин. Причина <span class="u" style="min-width:30%">&nbsp;</span><br>
  На линии: с ___ ч. ___ мин. до ___ ч. ___ мин. Причина <span class="u" style="min-width:29%">&nbsp;</span><br>
  Перерыв (обед): с ___ ч. ___ мин. до ___ ч. ___ мин.</div>
<div class="sec"><b>Отметка линейного контроля</b><br>
  <span class="u" style="min-width:98%">&nbsp;</span></div>"""

        col3 = f"""
<div class="sec c b">Выдача топлива (горючего), л</div>
<table class="sec"><tr><th></th><th>л</th><th>Подпись</th></tr>
  <tr><td>Замер остатка при выезде</td><td class="c">{v(w['fuel_start'])}</td><td></td></tr>
  <tr><td>Выдано</td><td class="c">{v(w['fuel_given'])}</td><td></td></tr>
  <tr><td>Замер при смене водителя</td><td></td><td></td></tr>
  <tr><td>Выдано</td><td></td><td></td></tr>
  <tr><td>Замер остатка при возвращении</td><td class="c">{v(w['fuel_end'])}</td><td></td></tr>
  <tr><td>Выдача масла</td><td></td><td></td></tr></table>
<table class="sec"><tr><th></th><th>Первая смена</th><th>Вторая смена</th></tr>
  <tr><td>Расход по норме</td><td class="c">{v(w['fuel_plan']) if shift1 else ''}</td><td class="c">{'' if shift1 else v(w['fuel_plan'])}</td></tr>
  <tr><td>Фактический</td><td class="c">{v(w['fuel_fact']) if shift1 else ''}</td><td class="c">{'' if shift1 else v(w['fuel_fact'])}</td></tr>
  <tr><td>Экономия</td><td class="c">{saving if shift1 else ''}</td><td class="c">{'' if shift1 else saving}</td></tr>
  <tr><td>Перерасход</td><td class="c">{overrun if shift1 else ''}</td><td class="c">{'' if shift1 else overrun}</td></tr></table>
<table class="sec"><tr><th>Наименование показателей</th><th>Первая смена</th><th>Вторая смена</th><th>Всего</th></tr>
  <tr><td>Выручка, руб. коп.: по плану / фактически</td><td></td><td></td><td></td></tr>
  <tr><td>Количество часов работы</td><td class="c">{v(line.get('shift_hours')) if shift1 else ''}</td>
      <td class="c">{'' if shift1 else v(line.get('shift_hours'))}</td><td class="c">{v(line.get('shift_hours'))}</td></tr>
  <tr><td>в том числе в движении: а) на линии</td><td></td><td></td><td></td></tr>
  <tr><td>б) по заказу &nbsp; в) в простое</td><td></td><td></td><td></td></tr>
  <tr><td>г) плановый резерв &nbsp; д) неплановый резерв</td><td></td><td></td><td></td></tr>
  <tr><td>е) в простое по заказу &nbsp; ж) в ремонте</td><td></td><td></td><td></td></tr>
  <tr><td>Общий пробег, км</td><td></td><td></td><td class="c b">{v(w['distance'])}</td></tr>
  <tr><td>в том числе с пассажирами: а) на маршруте № {v(w['rn'])}</td><td></td><td></td><td class="c">{v(line.get('distance_km'))}</td></tr>
  <tr><td>б) на маршруте № ____ &nbsp; в) на заказе</td><td></td><td></td><td></td></tr>
  <tr><td>Ночные часы</td><td></td><td></td><td class="c">{v(night_h)}</td></tr>
  <tr><td>Нулевой пробег</td><td></td><td></td><td></td></tr>
  <tr><td>Плановое количество рейсов</td><td></td><td></td><td class="c">{v(line.get('trips_count'))}</td></tr>
  <tr><td>Фактически выполненное количество рейсов, в т.ч. из числа запланированных и регулярных</td><td></td><td></td><td></td></tr></table>"""

        # оборотная сторона: рейсы в две группы колонок
        half = (len(trips) + 1) // 2
        max_rows = max(half, 12)
        def trip_cells(t):
            if not t: return "<td>&nbsp;</td>" + "<td></td>" * 5
            return (f"<td class='c'>{v(w['rn'])}</td><td class='c'>{t['dep_time']}</td><td></td>"
                    f"<td class='c'>{t['arr_time']}</td><td class='c'>{t['distance_km']}</td><td></td>")
        back_rows = ""
        for i in range(max_rows):
            left = trips[i] if i < half and i < len(trips) else None
            right = trips[half + i] if half + i < len(trips) else None
            back_rows += f"<tr>{trip_cells(left)}{trip_cells(right)}</tr>"
        back_head = ("<th rowspan='2'>Наименование или номер маршрута</th><th>Время отправления, ч. мин.</th>"
                     "<th rowspan='2'>Подпись</th><th>Время прибытия, ч. мин.</th><th>Пробег, км</th><th rowspan='2'>Подпись</th>") * 2
        back = f"""<div class="c b" style="margin-bottom:4px">Оборотная сторона формы № 6</div>
<table><tr>{back_head}</tr>
<tr><th>по графику / фактически</th><th>по графику / фактически</th><th>с пассажирами / нулевой</th>
    <th>по графику / фактически</th><th>по графику / фактически</th><th>с пассажирами / нулевой</th></tr>
{back_rows}</table>
<div style="margin-top:6px" class="small">Путевой лист № {esc(st.get('waybill_prefix'))}{w['number']} от {d}.
Водитель: {v(w['fio'])} (таб. № {v(w['tab_number'])}). Автобус: гар. № {v(w['garage_number'])}, {v(w['plate'])}.</div>"""

        html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Путевой лист № {w['number']}</title>{WB_CSS}</head><body>
<div class="noprint" style="margin-bottom:6px"><button onclick="print()">🖨 Печать / сохранить в PDF</button>
<span class="small"> Лист 1 — лицевая сторона, лист 2 — оборотная. Печать: альбомная, двусторонняя.</span></div>
<div class="page">{marks}
<table class="nb sec"><tr>
<td style="width:30%"><span class="stamp">ВЫПУСК НА ЛИНИЮ РАЗРЕШЕН</span></td>
<td class="c" style="width:40%"><span class="h1">ПУТЕВОЙ ЛИСТ АВТОБУСА</span><br>
серия <span class="u">{v(st.get('waybill_series'))}</span> № <span class="u b">{esc(st.get('waybill_prefix'))}{w['number']}</span>
&nbsp; на {d}</td>
<td class="small" style="width:30%; text-align:right">Типовая межотраслевая форма № 6<br>
Утверждена постановлением Госкомстата России от 28.11.97 № 78<br>
Код формы по ОКУД {v(st.get('org_okud'))}</td></tr></table>
<div class="grid3"><div>{col1}</div><div>{col2}</div><div>{col3}</div></div>
</div>
<div class="page">{back}</div>
</body></html>"""
        return HTMLResponse(html)
    finally:
        con.close()



def _waybill_pages_from_html(html):
    start = html.find('<div class="page">')
    end = html.rfind('</body></html>')
    if start == -1 or end == -1:
        return html
    return html[start:end]


@router.get("/orders/waybills/print", response_class=HTMLResponse)
def order_waybills_print(date: str, user=Depends(current_user)):
    con = db.connect()
    try:
        o = db.one(con.execute("SELECT * FROM orders WHERE date=?", (date,)))
        if not o:
            raise HTTPException(404, "Наряда нет")
        rows = db.rows(con.execute(
            "SELECT w.id, w.number FROM waybills w JOIN order_lines l ON l.id=w.order_line_id "
            "LEFT JOIN routes r ON r.id=l.route_id WHERE l.order_id=? AND w.status!='аннулирован' "
            "ORDER BY r.number, l.output_number, l.shift_number, w.number", (o["id"],)))
        if not rows:
            raise HTTPException(404, "В наряде нет оформленных путевых листов")
        db.audit(con, user["username"], "печать всех ПЛ наряда", "orders", o["id"],
                 comment=f"{date}: {len(rows)} ПЛ")
        con.commit()
    finally:
        con.close()

    pages = []
    for row in rows:
        printed = waybill_print(row["id"], duplicate=0, user=user)
        html = printed.body.decode("utf-8") if isinstance(printed.body, bytes) else str(printed.body)
        pages.append(_waybill_pages_from_html(html))
    d = datetime.date.fromisoformat(date).strftime("%d.%m.%Y")
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Печать всех ПЛ {d}</title>{WB_CSS}</head><body>
<div class="noprint" style="margin-bottom:6px"><button onclick="print()">🖨 Печать всех ПЛ</button>
<span class="small"> Наряд на {d}, путевых листов: {len(rows)}. Печать: альбомная, двусторонняя.</span></div>
{''.join(pages)}
</body></html>"""
    return HTMLResponse(html)
@router.get("/orders/print", response_class=HTMLResponse)
def order_print(date: str, user=Depends(current_user)):
    con = db.connect()
    try:
        o = db.one(con.execute("SELECT * FROM orders WHERE date=?", (date,)))
        if not o: raise HTTPException(404, "Наряда нет")
        st = db.get_settings(con)
        lines = db.rows(con.execute(
            "SELECT l.*, r.number rn, d.fio, d.tab_number, b.garage_number, b.plate FROM order_lines l "
            "LEFT JOIN routes r ON r.id=l.route_id LEFT JOIN drivers d ON d.id=l.driver_id "
            "LEFT JOIN buses b ON b.id=l.bus_id WHERE l.order_id=? ORDER BY r.number, l.output_number, l.shift_number", (o["id"],)))
        d = datetime.date.fromisoformat(date).strftime("%d.%m.%Y")
        rows_html = "".join(
            f"<tr><td>{esc(l['rn'])}</td><td>{esc(l['output_number'])}</td><td>{esc(l['shift_number'])}</td>"
            f"<td style='text-align:left'>{esc(l['fio'] or '— НЕ НАЗНАЧЕН —')}</td><td>{esc(l['tab_number'])}</td>"
            f"<td>{esc(l['garage_number'])}</td><td>{esc(l['plate'])}</td><td>{esc(l['report_time'])}</td>"
            f"<td>{esc(l['depart_depot'])}</td><td>{esc(l['start_line'])}—{esc(l['end_line'])}</td>"
            f"<td>{esc(l['return_depot'])}</td><td>{esc(l['shift_hours'])}</td><td>{esc(l['trips_count'])}</td>"
            f"<td>{esc(l['distance_km'])}</td><td>{esc(l['dispatcher_note'])}</td></tr>" for l in lines)
        html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Наряд {d}</title>{PRINT_CSS}</head>
<body><div class="noprint"><button onclick="print()">🖨 Печать</button></div>
<div class="h">НАРЯД НА ВЫПУСК АВТОБУСОВ</div>
<div class="sub">{esc(st.get('org_name'))} — на {d}. Статус: {esc(o['status'])}{(' (утвердил: ' + esc(o['approved_by']) + ')') if o['approved_by'] else ''}</div>
<table style="font-size:10px"><tr><th>Маршрут</th><th>Выход</th><th>Смена</th><th>Водитель</th><th>Таб.№</th>
<th>Гар.№</th><th>Госномер</th><th>Явка</th><th>Выезд</th><th>На линии</th><th>Заезд</th><th>Часы</th>
<th>Рейсов</th><th>Км</th><th>Отметки</th></tr>{rows_html}</table>
<table class="nb sig"><tr><td>Диспетчер: ______________</td><td>Начальник отдела эксплуатации: ______________</td></tr></table>
</body></html>"""
        return HTMLResponse(html)
    finally:
        con.close()

