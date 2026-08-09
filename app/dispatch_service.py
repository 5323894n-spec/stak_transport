# -*- coding: utf-8 -*-
"""Операционный контроль дня: табло выпуска и регулярность движения."""
import datetime

from . import db
from .api_planning import sched_day_type

STATUSES = ("план", "выпущен", "на_линии", "сошёл", "срыв", "замена")
_REASON_REQUIRED = {"сошёл", "срыв", "замена"}
_TELEMETRY_STATUS = {
    "release": "выпущен", "on_line": "на_линии",
    "off_line": "сошёл", "disruption": "срыв",
}


class DispatchError(ValueError):
    """Нарушение правил диспетчерского контроля."""


def _now_hm():
    return datetime.datetime.now().strftime("%H:%M")


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _minutes(value):
    parts = str(value).split(":")
    if len(parts) < 2:
        raise DispatchError("Время должно быть в формате ЧЧ:ММ")
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        raise DispatchError("Время должно быть в формате ЧЧ:ММ") from None


def _deviation(plan, actual):
    if not plan or not actual:
        return None
    return _minutes(actual) - _minutes(plan)


def _tolerance(con):
    row = con.execute(
        "SELECT value FROM settings WHERE key='dispatch_tolerance_min'"
    ).fetchone()
    try:
        return int(row["value"]) if row else 2
    except (TypeError, ValueError):
        return 2


def ensure_day(con, date):
    row = con.execute("SELECT * FROM dispatch_days WHERE date=?", (date,)).fetchone()
    if row is None:
        con.execute(
            "INSERT INTO dispatch_days(date, source_mode, created_at) VALUES(?, 'manual', ?)",
            (date, _now()),
        )
        row = con.execute("SELECT * FROM dispatch_days WHERE date=?", (date,)).fetchone()
    return dict(row)


def set_source_mode(con, date, mode, *, user):
    if mode not in ("manual", "gps"):
        raise DispatchError("Источник должен быть manual или gps")
    ensure_day(con, date)
    con.execute("UPDATE dispatch_days SET source_mode=? WHERE date=?", (mode, date))
    return {"date": date, "source_mode": mode}


def _approved_order(con, date):
    return con.execute(
        "SELECT * FROM orders WHERE date=? AND status='утверждён'", (date,)
    ).fetchone()


def _ensure_output(con, date, line):
    row = con.execute(
        "SELECT * FROM dispatch_outputs WHERE order_line_id=?", (line["id"],)
    ).fetchone()
    if row is None:
        plan = line["depart_depot"] or line["start_line"]
        con.execute(
            "INSERT INTO dispatch_outputs(date, order_line_id, plan_release, status, updated_at) "
            "VALUES(?,?,?, 'план', ?)",
            (date, line["id"], plan, _now()),
        )
        row = con.execute(
            "SELECT * FROM dispatch_outputs WHERE order_line_id=?", (line["id"],)
        ).fetchone()
    return dict(row)


def build_board(con, date):
    day = ensure_day(con, date)
    order = _approved_order(con, date)
    if order is None:
        return {
            "date": date, "source_mode": day["source_mode"], "has_order": False,
            "order_approved": False, "rows": [],
            "summary": day_summary_counts([], _tolerance(con)),
        }
    lines = con.execute(
        "SELECT l.*, r.number AS route_number, d.fio AS driver_fio, b.garage_number "
        "FROM order_lines l LEFT JOIN routes r ON r.id=l.route_id "
        "LEFT JOIN drivers d ON d.id=l.driver_id LEFT JOIN buses b ON b.id=l.bus_id "
        "WHERE l.order_id=? ORDER BY r.number, l.output_number, l.shift_number",
        (order["id"],),
    ).fetchall()
    rows = []
    for line in lines:
        out = _ensure_output(con, date, line)
        rows.append({
            "output_id": out["id"], "order_line_id": line["id"],
            "route_number": line["route_number"], "output_number": line["output_number"],
            "shift_number": line["shift_number"], "driver_fio": line["driver_fio"],
            "garage_number": line["garage_number"], "plan_release": out["plan_release"],
            "actual_release": out["actual_release"], "deviation_min": out["deviation_min"],
            "status": out["status"], "reason": out["reason"],
        })
    return {
        "date": date, "source_mode": day["source_mode"], "has_order": True,
        "order_approved": True, "rows": rows,
        "summary": day_summary_counts(rows, _tolerance(con)),
    }


def set_output_status(con, output_id, status, *, at=None, reason=None, note=None, user):
    row = con.execute(
        "SELECT * FROM dispatch_outputs WHERE id=?", (output_id,)
    ).fetchone()
    if row is None:
        raise DispatchError("Выход не найден")
    if status not in STATUSES:
        raise DispatchError("Недопустимый статус выхода")
    if status in _REASON_REQUIRED and not str(reason or "").strip():
        raise DispatchError("Укажите причину")
    actual_release = row["actual_release"]
    deviation = row["deviation_min"]
    if status == "выпущен":
        actual_release = at or _now_hm()
        deviation = _deviation(row["plan_release"], actual_release)
    con.execute(
        "UPDATE dispatch_outputs SET status=?, actual_release=?, deviation_min=?, reason=?, note=?, "
        "updated_by=?, updated_at=? WHERE id=?",
        (status, actual_release, deviation, reason, note, user, _now(), output_id),
    )
    return dict(con.execute(
        "SELECT * FROM dispatch_outputs WHERE id=?", (output_id,)
    ).fetchone())


def list_trip_facts(con, date, order_line_id):
    line = con.execute(
        "SELECT route_id, output_number, shift_number FROM order_lines WHERE id=?",
        (order_line_id,),
    ).fetchone()
    if line is None:
        raise DispatchError("Выход наряда не найден")
    day_type = sched_day_type(con, date)
    trips = con.execute(
        "SELECT trip_number, MIN(dep_time) AS dep FROM route_trips "
        "WHERE route_id=? AND day_type=? AND output_number=? "
        "AND (shift_number=? OR shift_number IS NULL) AND trip_number IS NOT NULL "
        "GROUP BY trip_number ORDER BY trip_number",
        (line["route_id"], day_type, line["output_number"], line["shift_number"]),
    ).fetchall()
    facts = {
        r["trip_number"]: dict(r)
        for r in con.execute(
            "SELECT trip_number, actual_dep, deviation_min, on_time "
            "FROM dispatch_trip_facts WHERE order_line_id=?",
            (order_line_id,),
        )
    }
    result = []
    for trip in trips:
        fact = facts.get(trip["trip_number"], {})
        result.append({
            "trip_number": trip["trip_number"], "plan_dep": trip["dep"],
            "actual_dep": fact.get("actual_dep"),
            "deviation_min": fact.get("deviation_min"), "on_time": fact.get("on_time"),
        })
    return result


def set_trip_fact(con, order_line_id, trip_number, actual_dep, *, date, user):
    plan = next(
        (f["plan_dep"] for f in list_trip_facts(con, date, order_line_id)
         if f["trip_number"] == trip_number),
        None,
    )
    if plan is None:
        raise DispatchError("Рейс не найден в плане выхода")
    deviation = _deviation(plan, actual_dep)
    on_time = 1 if deviation is not None and abs(deviation) <= _tolerance(con) else 0
    con.execute(
        "INSERT INTO dispatch_trip_facts(date, order_line_id, trip_number, plan_dep, actual_dep, "
        "deviation_min, on_time, updated_by, updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(order_line_id, trip_number) DO UPDATE SET actual_dep=excluded.actual_dep, "
        "plan_dep=excluded.plan_dep, deviation_min=excluded.deviation_min, on_time=excluded.on_time, "
        "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
        (date, order_line_id, trip_number, plan, actual_dep, deviation, on_time, user, _now()),
    )
    return {
        "trip_number": trip_number, "plan_dep": plan, "actual_dep": actual_dep,
        "deviation_min": deviation, "on_time": on_time,
    }


def day_summary(con, date):
    board = build_board(con, date)
    summary = dict(board["summary"])
    facts = con.execute(
        "SELECT on_time FROM dispatch_trip_facts WHERE date=? AND actual_dep IS NOT NULL",
        (date,),
    ).fetchall()
    total = len(facts)
    on_time = sum(1 for f in facts if f["on_time"] == 1)
    summary["trip_regularity"] = round(100 * on_time / total, 1) if total else 0.0
    summary["trips_recorded"] = total
    return summary


def day_summary_counts(rows, tolerance=2):
    summary = {"planned": len(rows)}
    for key, status in (
        ("released", "выпущен"), ("on_line", "на_линии"),
        ("off_line", "сошёл"), ("disrupted", "срыв"), ("replaced", "замена"),
    ):
        summary[key] = sum(1 for r in rows if r["status"] == status)
    active = [r for r in rows if r["status"] in ("выпущен", "на_линии", "сошёл")]
    on_time = sum(
        1 for r in active
        if r.get("deviation_min") is not None and abs(r["deviation_min"]) <= tolerance
    )
    summary["release_regularity"] = round(100 * on_time / len(rows), 1) if rows else 0.0
    return summary
