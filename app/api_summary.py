# -*- coding: utf-8 -*-
"""Сводное расписание: версии, проверки, Excel и формирование наряда."""
import datetime
import json

from fastapi import APIRouter, Body, Depends, HTTPException

from . import db, norms as N
from .api_planning import planned_fuel, sched_day_type
from .auth import current_user, require_write
from .xl import summary_schedule_xlsx_response

router = APIRouter(prefix="/api/summary-schedules", tags=["summary-schedules"])

CRITICAL = "Критическая ошибка"
ERROR = "Ошибка"
WARNING = "Предупреждение"
INFO = "Информация"

LINE_COLUMNS = [
    "summary_schedule_id", "service_date", "route_id", "route_number", "route_name", "direction",
    "run_number", "shift_number", "trip_number", "vehicle_id", "vehicle_number", "garage_number",
    "driver_id", "driver_tab_number", "driver_name", "departure_time", "arrival_time", "trip_duration",
    "depot_departure_time", "depot_return_time", "distance_km", "day_type", "schedule_version",
    "status", "error_flag", "comment",
]


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _date_range(date_from, date_to):
    start = datetime.date.fromisoformat(date_from)
    end = datetime.date.fromisoformat(date_to)
    if end < start:
        raise HTTPException(400, "Дата окончания меньше даты начала")
    if (end - start).days > 62:
        raise HTTPException(400, "Период сводного расписания не должен превышать 63 дня")
    day = start
    while day <= end:
        yield day.isoformat()
        day += datetime.timedelta(days=1)


def _duration_minutes(dep, arr):
    if not dep or not arr:
        return 0
    return N.shift_minutes(dep, arr)


def _time_value(value):
    minute = N.tmin(value or "")
    return minute if minute is not None else 0


def _depot_times(con, service_date, line_start, line_end):
    nrm = db.get_active_norms(con, service_date)
    prep = int(nrm.get("prep_final_minutes") or 0) + int(nrm.get("med_check_minutes") or 0)
    start = N.tmin(line_start)
    end = N.tmin(line_end)
    if start is None or end is None:
        return "", ""
    if end < start:
        end += 1440
    return N.tstr(start - prep - 10), N.tstr(end + 10)


def _fetch_assignment(con, service_date, route_id, output_number, shift_number, trip_number):
    assignment = db.one(con.execute(
        "SELECT ra.*, d.fio, d.tab_number, d.status AS driver_status, d.assigned_bus_id "
        "FROM roster_assignments ra JOIN drivers d ON d.id=ra.driver_id "
        "WHERE ra.date=? AND ra.route_id=? AND ra.output_number=? AND ra.shift_number=? "
        "AND (ra.trip_from IS NULL OR ra.trip_from<=?) AND (ra.trip_to IS NULL OR ra.trip_to>=?) "
        "ORDER BY ra.id LIMIT 1",
        (service_date, route_id, output_number, shift_number, trip_number, trip_number),
    ))
    if assignment:
        return assignment
    return db.one(con.execute(
        "SELECT r.*, d.fio, d.tab_number, d.status AS driver_status, d.assigned_bus_id "
        "FROM roster r JOIN drivers d ON d.id=r.driver_id "
        "WHERE r.date=? AND r.route_id=? AND r.output_number=? AND r.shift_number=? AND r.status='работа' "
        "ORDER BY r.id LIMIT 1",
        (service_date, route_id, output_number, shift_number),
    ))


def _fetch_bus(con, assignment):
    if not assignment:
        return None
    if assignment["assigned_bus_id"]:
        bus = db.one(con.execute("SELECT * FROM buses WHERE id=?", (assignment["assigned_bus_id"],)))
        if bus:
            return bus
    return db.one(con.execute(
        "SELECT * FROM buses WHERE assigned_driver_id=? ORDER BY garage_number LIMIT 1",
        (assignment["driver_id"],),
    ))


def _line_from_trip(con, summary_id, service_date, route, trip):
    assignment = _fetch_assignment(
        con, service_date, route["id"], trip["output_number"], trip["shift_number"], trip["trip_number"]
    )
    bus = _fetch_bus(con, assignment)
    depot_departure, depot_return = _depot_times(con, service_date, trip["dep_time"], trip["arr_time"])
    return {
        "summary_schedule_id": summary_id,
        "service_date": service_date,
        "route_id": route["id"],
        "route_number": route["number"] or "",
        "route_name": route["name"] or "",
        "direction": trip["direction"] or "",
        "run_number": trip["output_number"],
        "shift_number": trip["shift_number"],
        "trip_number": trip["trip_number"],
        "vehicle_id": bus["id"] if bus else None,
        "vehicle_number": bus["plate"] if bus else "",
        "garage_number": bus["garage_number"] if bus else "",
        "driver_id": assignment["driver_id"] if assignment else None,
        "driver_tab_number": assignment["tab_number"] if assignment else "",
        "driver_name": assignment["fio"] if assignment else "",
        "departure_time": trip["dep_time"] or "",
        "arrival_time": trip["arr_time"] or "",
        "trip_duration": _duration_minutes(trip["dep_time"], trip["arr_time"]),
        "depot_departure_time": depot_departure,
        "depot_return_time": depot_return,
        "distance_km": trip["distance_km"] or 0,
        "day_type": trip["day_type"] or "",
        "schedule_version": str(route["version"] or 1),
        "status": "действует" if route["active"] else "архив",
        "error_flag": 0,
        "comment": "",
    }


def _insert_line(con, line):
    cols = ",".join(LINE_COLUMNS)
    marks = ",".join("?" for _ in LINE_COLUMNS)
    line_id = con.execute(
        f"INSERT INTO summary_schedule_lines({cols}) VALUES({marks})",
        [line.get(col) for col in LINE_COLUMNS],
    ).lastrowid
    line["id"] = line_id
    return line


def _load_summary(con, summary_id):
    summary = db.one(con.execute("SELECT * FROM summary_schedules WHERE id=?", (summary_id,)))
    if not summary:
        raise HTTPException(404, "Сводное расписание не найдено")
    lines = db.rows(con.execute(
        "SELECT * FROM summary_schedule_lines WHERE summary_schedule_id=? "
        "ORDER BY service_date, route_number, run_number, shift_number, departure_time, trip_number",
        (summary_id,),
    ))
    errors = db.rows(con.execute(
        "SELECT * FROM summary_schedule_errors WHERE summary_schedule_id=? ORDER BY id",
        (summary_id,),
    ))
    return summary, lines, errors


def _recount_summary(con, summary_id):
    stats = db.one(con.execute(
        "SELECT COUNT(DISTINCT route_id) AS routes_count, COUNT(*) AS trips_count, "
        "COUNT(DISTINCT service_date || ':' || route_id || ':' || run_number || ':' || shift_number) AS runs_count, "
        "COUNT(DISTINCT vehicle_id) AS vehicles_count, COUNT(DISTINCT driver_id) AS drivers_count "
        "FROM summary_schedule_lines WHERE summary_schedule_id=?",
        (summary_id,),
    ))
    errs = db.one(con.execute(
        "SELECT SUM(CASE WHEN level IN ('Ошибка','Критическая ошибка') THEN 1 ELSE 0 END) AS errors_count, "
        "SUM(CASE WHEN level='Предупреждение' THEN 1 ELSE 0 END) AS warnings_count "
        "FROM summary_schedule_errors WHERE summary_schedule_id=?",
        (summary_id,),
    ))
    con.execute(
        "UPDATE summary_schedules SET routes_count=?, trips_count=?, runs_count=?, vehicles_count=?, drivers_count=?, "
        "errors_count=?, warnings_count=?, updated_at=? WHERE id=?",
        (stats["routes_count"] or 0, stats["trips_count"] or 0, stats["runs_count"] or 0,
         stats["vehicles_count"] or 0, stats["drivers_count"] or 0,
         errs["errors_count"] or 0, errs["warnings_count"] or 0, _now(), summary_id),
    )

def _group_key(line):
    return (line["service_date"], line["route_id"], line["run_number"], line["shift_number"])


def _build_views(lines, errors):
    by_routes = [dict(line) for line in lines]
    groups = {}
    for line in lines:
        key = _group_key(line)
        item = groups.setdefault(key, {
            "service_date": line["service_date"],
            "route_id": line["route_id"],
            "route_number": line["route_number"],
            "route_name": line["route_name"],
            "run_number": line["run_number"],
            "shift_number": line["shift_number"],
            "garage_number": line["garage_number"],
            "vehicle_number": line["vehicle_number"],
            "driver_id": line["driver_id"],
            "driver_name": line["driver_name"],
            "driver_tab_number": line["driver_tab_number"],
            "start_time": line["departure_time"],
            "end_time": line["arrival_time"],
            "trips_count": 0,
            "distance_km": 0.0,
            "line_minutes": 0,
            "error_flag": 0,
        })
        item["trips_count"] += 1
        item["distance_km"] = round(item["distance_km"] + float(line["distance_km"] or 0), 1)
        if line["error_flag"]:
            item["error_flag"] = 1
        if line["departure_time"] and (not item["start_time"] or _time_value(line["departure_time"]) < _time_value(item["start_time"])):
            item["start_time"] = line["departure_time"]
        if line["arrival_time"] and (not item["end_time"] or _time_value(line["arrival_time"]) >= _time_value(item["end_time"])):
            item["end_time"] = line["arrival_time"]
    by_outputs = []
    for item in groups.values():
        item["line_minutes"] = N.shift_minutes(item["start_time"], item["end_time"])
        by_outputs.append(item)
    by_outputs.sort(key=lambda x: (x["service_date"], x["route_number"] or "", x["run_number"] or 0, x["shift_number"] or 0))
    by_drivers = [x for x in by_outputs if x["driver_name"]]
    by_buses = [x for x in by_outputs if x["garage_number"] or x["vehicle_number"]]
    by_time = []
    for line in lines:
        base = dict(line)
        if line["depot_departure_time"]:
            by_time.append({**base, "time": line["depot_departure_time"], "event": "выезд из парка"})
        if line["departure_time"]:
            by_time.append({**base, "time": line["departure_time"], "event": "начало рейса"})
        if line["arrival_time"]:
            by_time.append({**base, "time": line["arrival_time"], "event": "прибытие"})
        if line["depot_return_time"]:
            by_time.append({**base, "time": line["depot_return_time"], "event": "заезд в парк"})
    by_time.sort(key=lambda x: (x.get("service_date") or "", _time_value(x.get("time"))))
    return {
        "by_routes": by_routes,
        "by_outputs": by_outputs,
        "by_drivers": by_drivers,
        "by_buses": by_buses,
        "by_time": by_time,
        "errors": errors,
    }


def _add_error(con, summary_id, line_id, level, line, object_type, object_label, message, recommendation):
    con.execute(
        "INSERT INTO summary_schedule_errors(summary_schedule_id,line_id,level,route_number,run_number,trip_number,"
        "object_type,object_label,message,recommendation,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (summary_id, line_id, level, line.get("route_number") if line else "", line.get("run_number") if line else None,
         line.get("trip_number") if line else None, object_type, object_label, message, recommendation, _now()),
    )
    if line_id:
        con.execute("UPDATE summary_schedule_lines SET error_flag=1 WHERE id=?", (line_id,))


def _interval(line):
    start = N.tmin(line.get("departure_time") or "")
    end = N.tmin(line.get("arrival_time") or "")
    if start is None or end is None:
        return None
    if end < start:
        end += 1440
    return start, end


def _overlaps(left, right):
    a = _interval(left)
    b = _interval(right)
    return bool(a and b and a[0] < b[1] and b[0] < a[1])


def _build_and_store_errors(con, summary_id, date_from, date_to):
    con.execute("DELETE FROM summary_schedule_errors WHERE summary_schedule_id=?", (summary_id,))
    con.execute("UPDATE summary_schedule_lines SET error_flag=0 WHERE summary_schedule_id=?", (summary_id,))
    lines = db.rows(con.execute("SELECT * FROM summary_schedule_lines WHERE summary_schedule_id=?", (summary_id,)))
    for line in lines:
        if not line["driver_id"]:
            _add_error(con, summary_id, line["id"], CRITICAL, line, "driver", "", "Не назначен водитель", "Назначьте водителя в графике или наряде")
        if not line["vehicle_id"]:
            _add_error(con, summary_id, line["id"], CRITICAL, line, "bus", "", "Не назначен автобус", "Закрепите автобус за водителем или назначьте вручную")
        if not line["departure_time"]:
            _add_error(con, summary_id, line["id"], ERROR, line, "trip", str(line["trip_number"] or ""), "Не указано время отправления", "Заполните время отправления в расписании")
        if not line["arrival_time"]:
            _add_error(con, summary_id, line["id"], ERROR, line, "trip", str(line["trip_number"] or ""), "Не указано время прибытия", "Заполните время прибытия в расписании")
        if line["departure_time"] and line["arrival_time"] and N.tmin(line["arrival_time"]) < N.tmin(line["departure_time"]):
            _add_error(con, summary_id, line["id"], WARNING, line, "trip", str(line["trip_number"] or ""), "Время прибытия раньше отправления", "Проверьте переход рейса через полночь")
        if not line["route_number"]:
            _add_error(con, summary_id, line["id"], ERROR, line, "route", "", "Не указан номер маршрута", "Заполните номер маршрута")
        if not line["run_number"]:
            _add_error(con, summary_id, line["id"], ERROR, line, "run", "", "Не указан номер выхода", "Укажите выход")
        if not line["trip_number"]:
            _add_error(con, summary_id, line["id"], ERROR, line, "trip", "", "Не указан номер рейса", "Укажите номер рейса")
        if line["driver_id"]:
            absence = db.one(con.execute(
                "SELECT * FROM absences WHERE driver_id=? AND status='утверждено' AND date_from<=? AND date_to>=? LIMIT 1",
                (line["driver_id"], line["service_date"], line["service_date"]),
            ))
            if absence:
                _add_error(con, summary_id, line["id"], CRITICAL, line, "driver", line["driver_name"], "Водитель находится в отпуске или на больничном", "Назначьте другого водителя")
        if line["vehicle_id"]:
            bus = db.one(con.execute("SELECT * FROM buses WHERE id=?", (line["vehicle_id"],)))
            if bus and bus["status"] not in ("исправен", "на линии"):
                _add_error(con, summary_id, line["id"], CRITICAL, line, "bus", line["garage_number"] or line["vehicle_number"], "Автобус находится в ремонте или недоступен", "Назначьте исправный автобус")
            if bus and not bus["plate"]:
                _add_error(con, summary_id, line["id"], WARNING, line, "bus", line["garage_number"], "Не указан госномер автобуса", "Заполните госномер автобуса")
            if bus and not bus["garage_number"]:
                _add_error(con, summary_id, line["id"], WARNING, line, "bus", line["vehicle_number"], "Не указан гаражный номер автобуса", "Заполните гаражный номер")
    for i, left in enumerate(lines):
        for right in lines[i + 1:]:
            if left["service_date"] != right["service_date"]:
                continue
            if left["driver_id"] and left["driver_id"] == right["driver_id"] and _overlaps(left, right):
                _add_error(con, summary_id, left["id"], CRITICAL, left, "driver", left["driver_name"], "Один водитель назначен одновременно на два выхода", "Разведите рейсы по времени или назначьте другого водителя")
            if left["vehicle_id"] and left["vehicle_id"] == right["vehicle_id"] and _overlaps(left, right):
                _add_error(con, summary_id, left["id"], CRITICAL, left, "bus", left["garage_number"] or left["vehicle_number"], "Один автобус назначен одновременно на два выхода", "Назначьте другой автобус")
            same_trip = (left["route_id"] == right["route_id"] and left["run_number"] == right["run_number"] and
                         left["shift_number"] == right["shift_number"] and left["trip_number"] == right["trip_number"])
            if same_trip:
                _add_error(con, summary_id, left["id"], ERROR, left, "trip", str(left["trip_number"]), "Дублируется номер рейса", "Перенумеруйте рейсы")
    for warn in N.check_period(con, date_from, date_to):
        line = next((x for x in lines if x["driver_tab_number"] == warn.get("tab_number") or x["driver_name"] == warn.get("driver")), None)
        _add_error(con, summary_id, line["id"] if line else None, WARNING, line or {}, "driver", warn.get("driver") or "",
                   "Нарушен режим труда и отдыха водителя",
                   f"Приказ 424: {warn.get('type', '')}. Норма: {warn.get('norm_value', '')}; факт: {warn.get('fact_value', '')}. {warn.get('recommendation', '')}")


def _route_query(route_ids, include_inactive):
    sql = "SELECT * FROM routes"
    params = []
    where = []
    if route_ids:
        where.append("id IN (%s)" % ",".join("?" for _ in route_ids))
        params.extend(route_ids)
    if not include_inactive:
        where.append("active=1")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY number, name"
    return sql, params


def _create_order_from_summary(con, summary, payload):
    if summary["period_start"] != summary["period_end"]:
        raise HTTPException(400, "Наряд можно сформировать только из сводного расписания за один день")
    critical = db.rows(con.execute(
        "SELECT * FROM summary_schedule_errors WHERE summary_schedule_id=? AND level='Критическая ошибка' ORDER BY id",
        (summary["id"],),
    ))
    if critical:
        text = "; ".join(e["message"] for e in critical[:5])
        raise HTTPException(409, f"Критические ошибки блокируют формирование наряда: {text}")
    date = summary["period_start"]
    existing = db.one(con.execute("SELECT * FROM orders WHERE date=?", (date,)))
    if existing and not payload.get("regenerate"):
        raise HTTPException(409, "Наряд на эту дату уже существует")
    if existing:
        if existing["status"] in ("утвержден", "выдан", "выполнен") and not payload.get("force"):
            raise HTTPException(409, "Наряд уже утверждён. Отмените утверждение для пересоздания.")
        con.execute("DELETE FROM order_lines WHERE order_id=?", (existing["id"],))
        order_id = existing["id"]
        con.execute("UPDATE orders SET status='черновик' WHERE id=?", (order_id,))
    else:
        order_id = con.execute("INSERT INTO orders(date,status) VALUES(?, 'черновик')", (date,)).lastrowid
    groups = db.rows(con.execute(
        "SELECT service_date, route_id, run_number, shift_number, driver_id, vehicle_id, "
        "MIN(departure_time) AS start_line, MAX(arrival_time) AS end_line, "
        "MIN(depot_departure_time) AS depart_depot, MAX(depot_return_time) AS return_depot, "
        "COUNT(*) AS trips_count, SUM(distance_km) AS distance_km "
        "FROM summary_schedule_lines WHERE summary_schedule_id=? "
        "GROUP BY service_date, route_id, run_number, shift_number, driver_id, vehicle_id "
        "ORDER BY route_id, run_number, shift_number",
        (summary["id"],),
    ))
    lines = 0
    for group in groups:
        bus = db.one(con.execute("SELECT * FROM buses WHERE id=?", (group["vehicle_id"],))) if group["vehicle_id"] else None
        start_min = N.tmin(group["start_line"] or "")
        end_min = N.tmin(group["end_line"] or "")
        hours = 0
        if start_min is not None and end_min is not None:
            if end_min < start_min:
                end_min += 1440
            hours = round((end_min - start_min) / 60.0, 2)
        con.execute(
            "INSERT INTO order_lines(order_id,route_id,output_number,shift_number,driver_id,bus_id,"
            "report_time,depart_depot,start_line,end_line,return_depot,shift_hours,trips_count,distance_km,planned_fuel,status,dispatcher_note) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, group["route_id"], group["run_number"], group["shift_number"], group["driver_id"], group["vehicle_id"],
             group["depart_depot"], group["depart_depot"], group["start_line"], group["end_line"], group["return_depot"],
             hours, group["trips_count"], round(group["distance_km"] or 0, 1),
             planned_fuel(bus, group["distance_km"] or 0, date), "план", "создано из сводного расписания"),
        )
        lines += 1
    return order_id, lines


@router.get("")
def summary_history(date_from: str = "", date_to: str = "", user=Depends(current_user)):
    con = db.connect()
    try:
        where = []
        params = []
        if date_from:
            where.append("period_end>=?")
            params.append(date_from)
        if date_to:
            where.append("period_start<=?")
            params.append(date_to)
        sql = "SELECT * FROM summary_schedules"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, id DESC LIMIT 100"
        return {"items": db.rows(con.execute(sql, params))}
    finally:
        con.close()


@router.post("/generate")
def generate_summary(payload: dict = Body(default={}), user=Depends(current_user)):
    require_write(user, "summary")
    date_from = payload.get("date_from") or payload.get("schedule_date")
    date_to = payload.get("date_to") or date_from
    if not date_from or not date_to:
        raise HTTPException(400, "Укажите дату или период")
    route_ids = [int(x) for x in payload.get("route_ids") or []]
    include_inactive = bool(payload.get("include_inactive"))
    con = db.connect()
    try:
        now = _now()
        summary_id = con.execute(
            "INSERT INTO summary_schedules(schedule_date,period_start,period_end,day_type,status,created_by,created_at,updated_at,filters_json,comment) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (date_from if date_from == date_to else None, date_from, date_to, payload.get("day_type") or "", "сформировано",
             user["username"], now, now,
             json.dumps({"route_ids": route_ids, "include_inactive": include_inactive}, ensure_ascii=False),
             payload.get("comment") or ""),
        ).lastrowid
        route_sql, route_params = _route_query(route_ids, include_inactive)
        routes = db.rows(con.execute(route_sql, route_params))
        for service_date in _date_range(date_from, date_to):
            day_type = payload.get("day_type") or sched_day_type(con, service_date)
            for route in routes:
                trips = db.rows(con.execute(
                    "SELECT * FROM route_trips WHERE route_id=? AND day_type=? ORDER BY output_number, shift_number, dep_time, trip_number",
                    (route["id"], day_type),
                ))
                for trip in trips:
                    _insert_line(con, _line_from_trip(con, summary_id, service_date, route, trip))
        _build_and_store_errors(con, summary_id, date_from, date_to)
        _recount_summary(con, summary_id)
        db.audit(con, user["username"], "формирование сводного расписания", "summary_schedules", summary_id)
        con.commit()
        summary, lines, errors = _load_summary(con, summary_id)
        return {"summary": summary, "lines": lines, "errors": errors, "views": _build_views(lines, errors)}
    finally:
        con.close()


@router.get("/{summary_id}")
def summary_detail(summary_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        summary, lines, errors = _load_summary(con, summary_id)
        return {"summary": summary, "lines": lines, "errors": errors, "views": _build_views(lines, errors)}
    finally:
        con.close()


@router.post("/{summary_id}/check")
def summary_recheck(summary_id: int, user=Depends(current_user)):
    require_write(user, "summary")
    con = db.connect()
    try:
        summary = db.one(con.execute("SELECT * FROM summary_schedules WHERE id=?", (summary_id,)))
        if not summary:
            raise HTTPException(404, "Сводное расписание не найдено")
        _build_and_store_errors(con, summary_id, summary["period_start"], summary["period_end"])
        _recount_summary(con, summary_id)
        con.commit()
        summary, lines, errors = _load_summary(con, summary_id)
        return {"summary": summary, "lines": lines, "errors": errors, "views": _build_views(lines, errors)}
    finally:
        con.close()


@router.get("/{summary_id}/export.xlsx")
def summary_export(summary_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        summary, lines, errors = _load_summary(con, summary_id)
        views = _build_views(lines, errors)
        settings = db.get_settings(con)
        con.execute(
            "UPDATE summary_schedules SET status='выгружено', excel_file_path=?, updated_at=? WHERE id=?",
            ("Сводное_расписание_за_период.xlsx", _now(), summary_id),
        )
        con.commit()
        return summary_schedule_xlsx_response(settings, summary, views, lines, errors)
    finally:
        con.close()


@router.post("/{summary_id}/order")
def summary_to_order(summary_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_write(user, "orders")
    con = db.connect()
    try:
        summary = db.one(con.execute("SELECT * FROM summary_schedules WHERE id=?", (summary_id,)))
        if not summary:
            raise HTTPException(404, "Сводное расписание не найдено")
        order_id, lines = _create_order_from_summary(con, summary, payload or {})
        con.execute("UPDATE summary_schedules SET status='передано в наряд', updated_at=? WHERE id=?", (_now(), summary_id))
        db.audit(con, user["username"], "формирование наряда из сводного расписания", "orders", order_id)
        con.commit()
        return {"order_id": order_id, "lines": lines}
    finally:
        con.close()
