# -*- coding: utf-8 -*-
"""Учёт рабочего времени, табель, выгрузка в 1С, топливо, отчёты, главная панель."""
import datetime, json, io, calendar as pycal
from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import Response
from . import db, norms as N
from .auth import current_user, require_write
from .xl import xlsx_response

router = APIRouter(prefix="/api")

def month_range(month):
    y, m = int(month[:4]), int(month[5:7])
    days = pycal.monthrange(y, m)[1]
    return f"{month}-01", f"{month}-{days:02d}", y, m, days

def timesheet_data(con, month, division=""):
    """Табель: по каждому водителю дни месяца с кодом и часами."""
    d_from, d_to, y, m, days = month_range(month)
    nrm = db.get_active_norms(con, d_from)
    norm_h = N.month_norm_hours(con, y, m, float(nrm["week_norm_hours"]))
    q = "SELECT * FROM drivers WHERE status!='уволен'"
    args = []
    if division: q += " AND division=?"; args.append(division)
    result = []
    for drv in con.execute(q + " ORDER BY fio", args):
        entries = {r["date"]: dict(r) for r in con.execute(
            "SELECT * FROM roster WHERE driver_id=? AND date>=? AND date<=?", (drv["id"], d_from, d_to))}
        wb = {r["date"]: dict(r) for r in con.execute(
            "SELECT date, depart_fact, return_fact, number FROM waybills "
            "WHERE driver_id=? AND date>=? AND date<=? AND status='выполнен'", (drv["id"], d_from, d_to))}
        day_cells, tot_h, tot_n, tot_hol, days_worked = [], 0.0, 0.0, 0.0, 0
        acnt = {}
        for dd in range(1, days + 1):
            iso = f"{month}-{dd:02d}"
            e = entries.get(iso)
            cell = {"date": iso, "code": "В", "hours": 0, "night": 0}
            if e:
                if e["status"] == "работа" and (e["hours"] or e["start_time"]):
                    h = e["hours"] or N.work_hours(e["start_time"], e["end_time"])
                    # факт из путевого листа
                    w = wb.get(iso)
                    if w and w["depart_fact"] and w["return_fact"]:
                        hf = N.work_hours(w["depart_fact"], w["return_fact"]) + \
                             (float(nrm["prep_final_minutes"]) + float(nrm["med_check_minutes"])) / 60.0
                        h = round(hf, 2)
                    nh = e["night_hours"] or 0
                    dtp = db.one(con.execute("SELECT day_type FROM calendar WHERE date=?", (iso,)))
                    holiday = (dtp and dtp["day_type"] == "праздник")
                    cell = {"date": iso, "code": "РВ" if holiday else "Я", "hours": round(h, 2), "night": nh,
                            "waybill": (wb.get(iso) or {}).get("number")}
                    tot_h += h; tot_n += nh; days_worked += 1
                    if holiday: tot_hol += h
                elif e["status"] == "РЗ":
                    cell = {"date": iso, "code": "РЗ", "hours": 0, "night": 0}
                    acnt["РЗ"] = acnt.get("РЗ", 0) + 1
                elif e["status"] not in ("выходной",):
                    cell = {"date": iso, "code": e["status"], "hours": 0, "night": 0}
                    acnt[e["status"]] = acnt.get(e["status"], 0) + 1
            day_cells.append(cell)
        overtime = round(max(0.0, tot_h - norm_h), 2)
        undertime = round(max(0.0, norm_h - tot_h), 2) if days_worked else 0
        result.append({"driver_id": drv["id"], "fio": drv["fio"], "tab_number": drv["tab_number"],
                       "division": drv["division"], "position": drv["position"], "days": day_cells,
                       "days_worked": days_worked, "total_hours": round(tot_h, 2),
                       "night_hours": round(tot_n, 2), "holiday_hours": round(tot_hol, 2),
                       "overtime": overtime, "undertime": undertime, "absences": acnt})
    return {"month": month, "norm_hours": norm_h, "days_in_month": days, "rows": result}

@router.get("/timesheet")
def timesheet(month: str, division: str = "", user=Depends(current_user)):
    con = db.connect()
    try:
        return timesheet_data(con, month, division)
    finally:
        con.close()

@router.get("/timesheet/export.xlsx")
def timesheet_export(month: str, division: str = "", user=Depends(current_user)):
    con = db.connect()
    try:
        data = timesheet_data(con, month, division)
        days = data["days_in_month"]
        headers = ["Таб.№", "ФИО", "Подразделение"] + [str(i) for i in range(1, days + 1)] + \
                  ["Дней", "Часов", "Ночных", "Празд.", "Сверхур.", "Недораб."]
        rows_ = []
        for r in data["rows"]:
            cells = [(f"{c['code']}" + (f"/{c['hours']}" if c["hours"] else "")) for c in r["days"]]
            rows_.append([r["tab_number"], r["fio"], r["division"]] + cells +
                         [r["days_worked"], r["total_hours"], r["night_hours"], r["holiday_hours"],
                          r["overtime"], r["undertime"]])
        return xlsx_response(f"Табель за {month} (норма {data['norm_hours']} ч)", headers, rows_,
                             filename=f"tabel_{month}.xlsx",
                             col_widths=[8, 28, 14] + [6] * days + [7, 8, 8, 8, 8, 8])
    finally:
        con.close()

# ---------- Выгрузка в 1С ----------
def export_rows(con, d_from, d_to):
    month = d_from[:7]
    data = timesheet_data(con, month)
    codes = {r["code"]: r["code_1c"] for r in con.execute("SELECT * FROM time_codes")}
    out = []
    for r in data["rows"]:
        drv = db.one(con.execute("SELECT division, position FROM drivers WHERE id=?", (r["driver_id"],)))
        for c in r["days"]:
            if c["date"] < d_from or c["date"] > d_to: continue
            if c["code"] == "В" and not c["hours"]: continue
            wbn = c.get("waybill", "")
            rt = db.one(con.execute(
                "SELECT rt.number FROM roster ro LEFT JOIN routes rt ON rt.id=ro.route_id WHERE ro.driver_id=? AND ro.date=?",
                (r["driver_id"], c["date"])))
            out.append({
                "tab_number": r["tab_number"], "fio": r["fio"], "division": drv["division"] or "",
                "position": drv["position"] or "", "date": c["date"],
                "time_code": codes.get(c["code"], c["code"]), "hours": c["hours"],
                "night_hours": c["night"], "route": (rt or {}).get("number", "") or "",
                "waybill": wbn or "", "comment": ""})
        if r["overtime"]:
            out.append({"tab_number": r["tab_number"], "fio": r["fio"], "division": drv["division"] or "",
                        "position": drv["position"] or "", "date": d_to,
                        "time_code": codes.get("С", "С"), "hours": r["overtime"], "night_hours": 0,
                        "route": "", "waybill": "", "comment": f"сверхурочные за {month}"})
        if r["holiday_hours"]:
            out.append({"tab_number": r["tab_number"], "fio": r["fio"], "division": drv["division"] or "",
                        "position": drv["position"] or "", "date": d_to,
                        "time_code": codes.get("РВ", "РВ"), "hours": r["holiday_hours"], "night_hours": 0,
                        "route": "", "waybill": "", "comment": f"работа в праздничные дни за {month}"})
    return out

HEAD_1C = ["ТабельныйНомер", "ФИО", "Подразделение", "Должность", "Дата", "КодВремени",
           "Часы", "НочныеЧасы", "Маршрут", "ПутевойЛист", "Комментарий"]

@router.get("/export1c")
def export1c(date_from: str, date_to: str, fmt: str = "csv", user=Depends(current_user)):
    if user["role"] not in ("админ", "бухгалтер", "руководитель"):
        raise HTTPException(403, "Выгрузку выполняет бухгалтер или администратор")
    con = db.connect()
    try:
        rows_ = export_rows(con, date_from, date_to)
        ver = (db.one(con.execute("SELECT COUNT(*) c FROM exports_1c WHERE period_from=? AND period_to=?",
                                  (date_from, date_to))) or {}).get("c", 0) + 1
        fn = f"1c_{date_from}_{date_to}_v{ver}.{fmt if fmt != 'xlsx' else 'xlsx'}"
        emp = len({r["tab_number"] for r in rows_})
        con.execute("INSERT INTO exports_1c(created_by,created_at,period_from,period_to,fmt,employees,version,file_name,protocol) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (user["username"], datetime.datetime.now().isoformat(timespec="seconds"),
                     date_from, date_to, fmt, emp, ver, fn, f"строк: {len(rows_)}"))
        db.audit(con, user["username"], "выгрузка в 1С", "exports_1c", fn,
                 comment=f"{date_from}—{date_to}, формат {fmt}, сотрудников {emp}, версия {ver}")
        con.commit()
        keys = ["tab_number", "fio", "division", "position", "date", "time_code", "hours",
                "night_hours", "route", "waybill", "comment"]
        if fmt == "json":
            body = json.dumps({"period": [date_from, date_to], "version": ver, "rows": rows_},
                              ensure_ascii=False, indent=1)
            return Response(body, media_type="application/json",
                            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fn}"})
        if fmt == "xml":
            items = "".join(
                "<Строка>" + "".join(f"<{h}>{str(r[k])}</{h}>" for h, k in zip(HEAD_1C, keys)) + "</Строка>"
                for r in rows_)
            body = (f'<?xml version="1.0" encoding="UTF-8"?><ВыгрузкаТабеля ПериодС="{date_from}" '
                    f'ПериодПо="{date_to}" Версия="{ver}">{items}</ВыгрузкаТабеля>')
            return Response(body, media_type="application/xml",
                            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fn}"})
        if fmt == "xlsx":
            return xlsx_response(f"Выгрузка в 1С {date_from}—{date_to} (версия {ver})", HEAD_1C,
                                 [[r[k] for k in keys] for r in rows_], filename=fn)
        # csv (с BOM, разделитель ';' — для 1С/Excel)
        lines = [";".join(HEAD_1C)]
        for r in rows_:
            lines.append(";".join(str(r[k]).replace(";", ",") for k in keys))
        return Response("﻿" + "\r\n".join(lines), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fn}"})
    finally:
        con.close()

@router.get("/export1c/history")
def export1c_history(user=Depends(current_user)):
    con = db.connect()
    try:
        return {"items": db.rows(con.execute("SELECT * FROM exports_1c ORDER BY id DESC LIMIT 100"))}
    finally:
        con.close()

# ---------- Топливо ----------
@router.get("/fuel")
def fuel_list(date_from: str = "", date_to: str = "", bus_id: int = 0, user=Depends(current_user)):
    con = db.connect()
    try:
        q = ("SELECT f.*, b.garage_number, b.plate, d.fio, r.number AS route_number, w.number AS waybill_number "
             "FROM fuel_records f LEFT JOIN buses b ON b.id=f.bus_id LEFT JOIN drivers d ON d.id=f.driver_id "
             "LEFT JOIN routes r ON r.id=f.route_id LEFT JOIN waybills w ON w.id=f.waybill_id WHERE 1=1")
        args = []
        if date_from: q += " AND f.date>=?"; args.append(date_from)
        if date_to: q += " AND f.date<=?"; args.append(date_to)
        if bus_id: q += " AND f.bus_id=?"; args.append(bus_id)
        items = db.rows(con.execute(q + " ORDER BY f.date DESC, f.id DESC LIMIT 1000", args))
        tot = {"plan": round(sum(i["plan_litres"] or 0 for i in items), 1),
               "fact": round(sum(i["fact_litres"] or 0 for i in items), 1),
               "given": round(sum(i["given_litres"] or 0 for i in items), 1),
               "saving": round(sum(i["saving"] or 0 for i in items), 1),
               "overrun": round(sum(i["overrun"] or 0 for i in items), 1)}
        return {"items": items, "totals": tot}
    finally:
        con.close()

@router.post("/fuel/refuel")
def fuel_refuel(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "fuel")
    con = db.connect()
    try:
        bus = db.one(con.execute("SELECT * FROM buses WHERE id=?", (payload["bus_id"],)))
        litres = float(payload["litres"])
        kind = payload.get("kind", "заправка")
        delta = litres if kind == "заправка" else -litres
        new_balance = round((bus["fuel_balance"] or 0) + delta, 1)
        con.execute("INSERT INTO fuel_records(date,bus_id,kind,given_litres,start_balance,end_balance,comment,responsible) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (payload.get("date") or datetime.date.today().isoformat(), payload["bus_id"], kind,
                     litres, bus["fuel_balance"], new_balance, payload.get("comment", ""), user["username"]))
        con.execute("UPDATE buses SET fuel_balance=? WHERE id=?", (new_balance, payload["bus_id"]))
        if kind != "заправка":
            db.notify(con, "warning", "топливо",
                      f"Корректировка топлива {bus['garage_number']}: −{litres} л ({payload.get('comment','без комментария')}) — {user['username']}")
        if new_balance < 0:
            db.notify(con, "error", "топливо", f"Отрицательный остаток топлива у {bus['garage_number']}!")
        if new_balance > (bus["tank_capacity"] or 999):
            db.notify(con, "warning", "топливо", f"Остаток топлива {bus['garage_number']} превышает объём бака")
        db.audit(con, user["username"], kind, "fuel_records", bus["garage_number"], new=payload)
        con.commit()
        return {"ok": True, "balance": new_balance}
    finally:
        con.close()

@router.get("/fuel/report")
def fuel_report(date_from: str, date_to: str, group: str = "bus", user=Depends(current_user)):
    con = db.connect()
    try:
        key = {"bus": ("b.garage_number || ' (' || IFNULL(b.plate,'') || ')'", "buses b ON b.id=f.bus_id"),
               "driver": ("d.fio", "drivers d ON d.id=f.driver_id"),
               "route": ("'№ ' || r.number", "routes r ON r.id=f.route_id")}[group if group in ("bus","driver","route") else "bus"]
        items = db.rows(con.execute(
            f"SELECT {key[0]} AS name, COUNT(*) trips, ROUND(SUM(f.distance),1) km, ROUND(SUM(f.plan_litres),1) plan, "
            f"ROUND(SUM(f.fact_litres),1) fact, ROUND(SUM(f.saving),1) saving, ROUND(SUM(f.overrun),1) overrun "
            f"FROM fuel_records f LEFT JOIN {key[1]} WHERE f.date>=? AND f.date<=? AND f.kind='рейс' "
            f"GROUP BY name ORDER BY overrun DESC", (date_from, date_to)))
        return {"items": items}
    finally:
        con.close()

@router.get("/fuel/export.xlsx")
def fuel_export(date_from: str, date_to: str, user=Depends(current_user)):
    con = db.connect()
    try:
        items = con.execute(
            "SELECT f.date, b.garage_number, b.plate, d.fio, r.number, w.number, f.kind, f.distance, f.rate, "
            "f.plan_litres, f.fact_litres, f.given_litres, f.start_balance, f.end_balance, f.saving, f.overrun, f.responsible "
            "FROM fuel_records f LEFT JOIN buses b ON b.id=f.bus_id LEFT JOIN drivers d ON d.id=f.driver_id "
            "LEFT JOIN routes r ON r.id=f.route_id LEFT JOIN waybills w ON w.id=f.waybill_id "
            "WHERE f.date>=? AND f.date<=? ORDER BY f.date, b.garage_number", (date_from, date_to)).fetchall()
        headers = ["Дата","Гар.№","Госномер","Водитель","Маршрут","№ ПЛ","Операция","Пробег, км","Норма л/100км",
                   "План, л","Факт, л","Выдано, л","Остаток нач.","Остаток кон.","Экономия","Перерасход","Ответственный"]
        return xlsx_response(f"Топливная ведомость {date_from}—{date_to}", headers, [list(r) for r in items],
                             filename=f"fuel_{date_from}_{date_to}.xlsx")
    finally:
        con.close()

# ---------- Отчёты ----------
@router.get("/reports/overtime")
def report_overtime(month: str, user=Depends(current_user)):
    con = db.connect()
    try:
        data = timesheet_data(con, month)
        items = [{"fio": r["fio"], "tab_number": r["tab_number"], "division": r["division"],
                  "total": r["total_hours"], "norm": data["norm_hours"], "overtime": r["overtime"],
                  "undertime": r["undertime"], "night": r["night_hours"], "holiday": r["holiday_hours"]}
                 for r in data["rows"] if r["overtime"] or r["undertime"] or r["night_hours"] or r["holiday_hours"]]
        items.sort(key=lambda x: -x["overtime"])
        return {"items": items, "norm": data["norm_hours"]}
    finally:
        con.close()

@router.get("/reports/release")
def report_release(date: str, user=Depends(current_user)):
    """Отчёт по выпуску: план/факт, срывы."""
    con = db.connect()
    try:
        o = db.one(con.execute("SELECT * FROM orders WHERE date=?", (date,)))
        if not o: return {"items": [], "summary": {}}
        items = db.rows(con.execute(
            "SELECT l.*, r.number rn, d.fio, b.garage_number, w.number wn, w.status wstatus, "
            "w.depart_fact, w.return_fact, w.distance wdist FROM order_lines l "
            "LEFT JOIN routes r ON r.id=l.route_id LEFT JOIN drivers d ON d.id=l.driver_id "
            "LEFT JOIN buses b ON b.id=l.bus_id "
            "LEFT JOIN waybills w ON w.order_line_id=l.id AND w.status!='аннулирован' "
            "WHERE l.order_id=? ORDER BY r.number, l.output_number", (o["id"],)))
        plan = len(items)
        out = sum(1 for i in items if i["wn"])
        done = sum(1 for i in items if i["wstatus"] == "выполнен")
        return {"items": items, "summary": {"план": plan, "выдано ПЛ": out, "выполнено": done,
                                            "срывы": plan - out}}
    finally:
        con.close()

@router.get("/reports/transport-work")
def report_twork(date_from: str, date_to: str, user=Depends(current_user)):
    con = db.connect()
    try:
        items = db.rows(con.execute(
            "SELECT r.number, r.name, COUNT(DISTINCT w.id) waybills, ROUND(SUM(w.distance),1) km_fact, "
            "ROUND(SUM(l.distance_km),1) km_plan, SUM(l.trips_count) trips_plan "
            "FROM order_lines l JOIN orders o ON o.id=l.order_id AND o.date>=? AND o.date<=? "
            "LEFT JOIN routes r ON r.id=l.route_id "
            "LEFT JOIN waybills w ON w.order_line_id=l.id AND w.status='выполнен' "
            "GROUP BY r.number ORDER BY r.number", (date_from, date_to)))
        return {"items": items}
    finally:
        con.close()

@router.get("/reports/summary")
def report_summary(date_from: str, date_to: str, user=Depends(current_user)):
    con = db.connect()
    try:
        month = date_from[:7]
        ts = timesheet_data(con, month)
        vio = N.check_period(con, date_from, date_to)
        fuel = db.one(con.execute(
            "SELECT ROUND(SUM(plan_litres),1) plan, ROUND(SUM(fact_litres),1) fact, ROUND(SUM(saving),1) sv, "
            "ROUND(SUM(overrun),1) ov, ROUND(SUM(distance),1) km FROM fuel_records WHERE date>=? AND date<=? AND kind='рейс'",
            (date_from, date_to)))
        wb = db.one(con.execute(
            "SELECT COUNT(*) c, SUM(status='выполнен') done, SUM(status='аннулирован') cancelled "
            "FROM waybills WHERE date>=? AND date<=?", (date_from, date_to)))
        return {
            "период": [date_from, date_to],
            "водителей": len(ts["rows"]),
            "часов_всего": round(sum(r["total_hours"] for r in ts["rows"]), 1),
            "ночных": round(sum(r["night_hours"] for r in ts["rows"]), 1),
            "сверхурочных": round(sum(r["overtime"] for r in ts["rows"]), 1),
            "нарушений_424": len(vio),
            "критических": len([v for v in vio if v["severity"] == "критично"]),
            "путевых_листов": wb["c"], "выполнено": wb["done"] or 0, "аннулировано": wb["cancelled"] or 0,
            "пробег_км": fuel["km"] or 0, "топливо_план": fuel["plan"] or 0, "топливо_факт": fuel["fact"] or 0,
            "экономия": fuel["sv"] or 0, "перерасход": fuel["ov"] or 0,
        }
    finally:
        con.close()

# ---------- Главная панель ----------
@router.get("/dashboard")
def dashboard(date: str = "", user=Depends(current_user)):
    con = db.connect()
    try:
        d = date or datetime.date.today().isoformat()
        month = d[:7]
        o = db.one(con.execute("SELECT * FROM orders WHERE date=?", (d,)))
        lines = db.rows(con.execute("SELECT * FROM order_lines WHERE order_id=?", (o["id"],))) if o else []
        wb_issued = db.one(con.execute("SELECT COUNT(*) c FROM waybills WHERE date=? AND status!='аннулирован'", (d,)))["c"]
        absent = db.one(con.execute(
            "SELECT COUNT(DISTINCT driver_id) c FROM roster WHERE date=? AND status NOT IN ('работа','выходной','РЗ')", (d,)))["c"]
        repair = db.one(con.execute("SELECT COUNT(*) c FROM buses WHERE status='в ремонте'"))["c"]
        vio = N.check_period(con, d, d)
        # истекающие документы
        horizon = (datetime.date.fromisoformat(d) + datetime.timedelta(days=30)).isoformat()
        exp = []
        for r in con.execute("SELECT fio, license_expires FROM drivers WHERE status='работает' AND license_expires<=? AND license_expires>''", (horizon,)):
            exp.append(f"ВУ: {r['fio']} — до {r['license_expires']}")
        for r in con.execute("SELECT garage_number, osago_expires FROM buses WHERE status!='списан' AND osago_expires<=? AND osago_expires>''", (horizon,)):
            exp.append(f"ОСАГО: {r['garage_number']} — до {r['osago_expires']}")
        for r in con.execute("SELECT garage_number, diag_card_expires FROM buses WHERE status!='списан' AND diag_card_expires<=? AND diag_card_expires>''", (horizon,)):
            exp.append(f"Диагностическая карта: {r['garage_number']} — до {r['diag_card_expires']}")
        for r in con.execute("SELECT garage_number, next_to_date FROM buses WHERE status!='списан' AND next_to_date<=? AND next_to_date>''", (horizon,)):
            exp.append(f"ТО: {r['garage_number']} — {r['next_to_date']}")
        ts = timesheet_data(con, month)
        overtime_cnt = len([r for r in ts["rows"] if r["overtime"] > 0])
        fuel = db.one(con.execute("SELECT ROUND(SUM(overrun),1) ov FROM fuel_records WHERE date LIKE ?", (month + "%",)))
        notif = db.one(con.execute("SELECT COUNT(*) c FROM notifications WHERE seen=0"))["c"]
        return {
            "date": d, "order_status": o["status"] if o else "не сформирован",
            "lines_total": len(lines),
            "drivers_assigned": len([l for l in lines if l["driver_id"]]),
            "buses_assigned": len([l for l in lines if l["bus_id"]]),
            "lines_without_driver": len([l for l in lines if not l["driver_id"]]),
            "waybills_issued": wb_issued,
            "waybills_missing": max(0, len([l for l in lines if l["driver_id"] and l["bus_id"]]) - wb_issued),
            "absent_drivers": absent, "buses_in_repair": repair,
            "violations_today": len(vio),
            "violations_critical": len([v for v in vio if v["severity"] == "критично"]),
            "overtime_drivers_month": overtime_cnt,
            "fuel_overrun_month": fuel["ov"] or 0,
            "expiring_docs": exp[:15], "unseen_notifications": notif,
        }
    finally:
        con.close()
