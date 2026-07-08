# -*- coding: utf-8 -*-
"""Расписания и выходы, графики работы водителей, наряды."""
import datetime, json
from fastapi import APIRouter, Depends, HTTPException, Body
from . import db, norms as N
from .auth import current_user, require_write
from .xl import xlsx_response, order_xlsx_response

router = APIRouter(prefix="/api")

DAY_TYPES = ["будни", "суббота", "воскресенье"]
BREAK_LUNCH = "\u043e\u0431\u0435\u0434"
BREAK_SPLIT = "\u0440\u0430\u0437\u0440\u044b\u0432"
BREAK_TECH = "\u0442\u0435\u0445\u043d\u043e\u043b\u043e\u0433\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u043f\u0435\u0440\u0435\u0440\u044b\u0432"
BREAK_TYPE_ALIASES = {
    "\u043e\u0431\u0435\u0434/\u043f\u0435\u0440\u0435\u0441\u043c\u0435\u043d\u043a\u0430": BREAK_LUNCH,
    "\u0442\u0435\u0445\u043d\u043e\u043b\u043e\u0433\u0438\u0447\u0435\u0441\u043a\u0438\u0439": BREAK_TECH,
}
SCHEDULED_BREAK_TYPES = (BREAK_LUNCH, BREAK_SPLIT, BREAK_TECH)
UNPAID_BREAK_TYPES = (BREAK_LUNCH, BREAK_SPLIT, "\u043e\u0431\u0435\u0434/\u043f\u0435\u0440\u0435\u0441\u043c\u0435\u043d\u043a\u0430")


def normalize_break_type(value):
    value = (value or "").strip()
    return BREAK_TYPE_ALIASES.get(value, value)


def scheduled_break_minutes(trip):
    if not trip:
        return 0
    minutes = int(trip.get("break_after_min") or 0)
    btype = normalize_break_type(trip.get("break_type"))
    return minutes if minutes > 0 and btype in SCHEDULED_BREAK_TYPES else 0


def _time_after(base_min, time_str):
    value = N.tmin(time_str)
    if value is None:
        return None
    if base_min is not None and value < base_min:
        value += 1440
    return value

def sched_day_type(con, iso_date):
    dt = db.one(con.execute("SELECT day_type FROM calendar WHERE date=?", (iso_date,)))
    d = datetime.date.fromisoformat(iso_date)
    cal = dt["day_type"] if dt else ("выходной" if d.weekday() >= 5 else "рабочий")
    if cal in ("рабочий", "предпраздничный"):
        return "суббота" if d.weekday() == 5 else ("воскресенье" if d.weekday() == 6 else "будни")
    return "суббота" if d.weekday() == 5 else "воскресенье"

def is_holiday(con, iso_date):
    dt = db.one(con.execute("SELECT day_type FROM calendar WHERE date=?", (iso_date,)))
    if dt: return dt["day_type"] == "праздник"
    d = datetime.date.fromisoformat(iso_date)
    return (d.month, d.day) in {(1,1),(1,2),(1,3),(1,4),(1,5),(1,6),(1,7),(1,8),(2,23),(3,8),(5,1),(5,9),(6,12),(11,4)}

def is_weekend(con, iso_date):
    dt = db.one(con.execute("SELECT day_type FROM calendar WHERE date=?", (iso_date,)))
    if dt: return dt["day_type"] in ("выходной", "праздник")
    return datetime.date.fromisoformat(iso_date).weekday() >= 5

# ================= РАСПИСАНИЯ =================
@router.get("/trips")
def trips_list(route_id: int, day_type: str = "будни", user=Depends(current_user)):
    con = db.connect()
    try:
        items = db.rows(con.execute(
            "SELECT * FROM route_trips WHERE route_id=? AND day_type=? ORDER BY output_number, dep_time",
            (route_id, day_type)))
        return {"items": items}
    finally:
        con.close()

def _shift_following_trips_after_break(con, trip_id):
    current = db.one(con.execute("SELECT * FROM route_trips WHERE id=?", (trip_id,)))
    if not current or not current.get("dep_time") or not current.get("arr_time"):
        return 0
    break_min = scheduled_break_minutes(current)
    if break_min <= 0:
        return 0

    chain = db.rows(con.execute(
        "SELECT * FROM route_trips WHERE route_id=? AND day_type=? AND output_number=? AND shift_number=? "
        "ORDER BY dep_time, id",
        (current["route_id"], current["day_type"], current["output_number"], current["shift_number"])))
    later, found = [], False
    for trip in chain:
        if trip["id"] == trip_id:
            found = True
            continue
        if found:
            later.append(trip)
    if not later:
        return 0

    cur_dep = N.tmin(current["dep_time"])
    cur_arr = _time_after(cur_dep, current["arr_time"])
    required_start = cur_arr + break_min
    next_dep = _time_after(cur_dep, later[0].get("dep_time"))
    if next_dep is None:
        return 0
    delta = required_start - next_dep
    if delta <= 0:
        return 0

    updated = 0
    for trip in later:
        if not trip.get("dep_time") or not trip.get("arr_time"):
            continue
        dep = _time_after(cur_dep, trip["dep_time"])
        arr = _time_after(dep, trip["arr_time"])
        con.execute(
            "UPDATE route_trips SET dep_time=?, arr_time=? WHERE id=?",
            (N.tstr(dep + delta), N.tstr(arr + delta), trip["id"]))
        updated += 1
    return updated


@router.post("/trips")
def trip_save(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "trips")
    payload = dict(payload)
    payload["break_type"] = normalize_break_type(payload.get("break_type"))
    con = db.connect()
    try:
        f = ["route_id","day_type","output_number","shift_number","trip_number","direction",
             "dep_time","arr_time","distance_km","break_after_min","break_type"]
        if payload.get("id"):
            trip_id = int(payload["id"])
            con.execute("UPDATE route_trips SET " + ",".join(x + "=?" for x in f) + " WHERE id=?",
                        [payload.get(x) for x in f] + [trip_id])
            db.audit(con, user["username"], "изменение рейса", "route_trips", trip_id, new=payload)
        else:
            cur = con.execute("INSERT INTO route_trips(" + ",".join(f) + ") VALUES(" + ",".join("?"*len(f)) + ")",
                        [payload.get(x) for x in f])
            trip_id = cur.lastrowid
            db.audit(con, user["username"], "создание рейса", "route_trips", trip_id, new=payload)
        shifted = _shift_following_trips_after_break(con, trip_id)
        con.commit()
        return {"ok": True, "id": trip_id, "shifted": shifted}
    finally:
        con.close()


@router.post("/trips/bulk-shift")
def trips_bulk_shift(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "trips")
    con = db.connect()
    try:
        rid = int(payload["route_id"])
        day_type = payload.get("day_type", "\u0431\u0443\u0434\u043d\u0438")
        minutes = int(payload.get("minutes") or 0)
        if minutes == 0:
            raise HTTPException(400, "\u0421\u0434\u0432\u0438\u0433 \u0434\u043e\u043b\u0436\u0435\u043d \u0431\u044b\u0442\u044c \u043e\u0442\u043b\u0438\u0447\u0435\u043d \u043e\u0442 0")
        if abs(minutes) > 720:
            raise HTTPException(400, "\u0421\u0434\u0432\u0438\u0433 \u043d\u0435 \u043c\u043e\u0436\u0435\u0442 \u0431\u044b\u0442\u044c \u0431\u043e\u043b\u044c\u0448\u0435 12 \u0447\u0430\u0441\u043e\u0432")
        output_number = int(payload.get("output_number") or 0)
        q = "SELECT * FROM route_trips WHERE route_id=? AND day_type=?"
        args = [rid, day_type]
        if output_number:
            q += " AND output_number=?"
            args.append(output_number)
        items = db.rows(con.execute(q, args))
        for t in items:
            if not t.get("dep_time") or not t.get("arr_time"):
                continue
            con.execute(
                "UPDATE route_trips SET dep_time=?, arr_time=? WHERE id=?",
                (N.tstr(N.tmin(t["dep_time"]) + minutes), N.tstr(N.tmin(t["arr_time"]) + minutes), t["id"]))
        db.audit(con, user["username"], "\u043c\u0430\u0441\u0441\u043e\u0432\u044b\u0439 \u0441\u0434\u0432\u0438\u0433 \u0440\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u044f", "route_trips", rid,
                 comment=f"{day_type}, output {output_number or 'all'}, {minutes} min")
        con.commit()
        return {"updated": len(items)}
    finally:
        con.close()

@router.post("/trips/renumber")
def trips_renumber(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "trips")
    con = db.connect()
    try:
        rid = int(payload["route_id"])
        day_type = payload.get("day_type", "\u0431\u0443\u0434\u043d\u0438")
        output_number = int(payload.get("output_number") or 0)
        q = "SELECT id, output_number FROM route_trips WHERE route_id=? AND day_type=?"
        args = [rid, day_type]
        if output_number:
            q += " AND output_number=?"
            args.append(output_number)
        q += " ORDER BY output_number, dep_time, arr_time, id"
        counters, updated = {}, 0
        for t in con.execute(q, args):
            out = t["output_number"]
            counters[out] = counters.get(out, 0) + 1
            con.execute("UPDATE route_trips SET trip_number=? WHERE id=?", (counters[out], t["id"]))
            updated += 1
        db.audit(con, user["username"], "\u043f\u0435\u0440\u0435\u043d\u0443\u043c\u0435\u0440\u0430\u0446\u0438\u044f \u0440\u0435\u0439\u0441\u043e\u0432", "route_trips", rid,
                 comment=f"{day_type}, output {output_number or 'all'}, trips {updated}")
        con.commit()
        return {"updated": updated}
    finally:
        con.close()

@router.delete("/trips/{tid}")
def trip_delete(tid: int, user=Depends(current_user)):
    require_write(user, "trips")
    con = db.connect()
    try:
        con.execute("DELETE FROM route_trips WHERE id=?", (tid,))
        db.audit(con, user["username"], "удаление рейса", "route_trips", tid)
        con.commit()
        return {"ok": True}
    finally:
        con.close()

@router.post("/trips/generate")
def trips_generate(payload: dict = Body(...), user=Depends(current_user)):
    """Автогенерация расписания: выходы, рейсы, смены, обеды."""
    require_write(user, "trips")
    con = db.connect()
    try:
        rid = payload["route_id"]
        day_type = payload.get("day_type", "будни")
        route = db.one(con.execute("SELECT * FROM routes WHERE id=?", (rid,)))
        if not route: raise HTTPException(404, "Маршрут не найден")
        outputs = int(payload.get("outputs", route["outputs_count"] or 1))
        first = N.tmin(payload.get("first_dep", "05:30"))
        last = N.tmin(payload.get("last_dep", "22:00"))
        trip_time = int(payload.get("trip_time", route["trip_time_min"] or 60))
        trip_time_back = int(payload.get("trip_time_back") or route["trip_time_back_min"] or trip_time)
        rest = int(payload.get("rest_min", 6))
        lunch = int(payload.get("lunch_min", 40))
        dist = float(payload.get("distance", route["length_km"] or 10))
        dist_back = float(payload.get("distance_back") or route["length_back_km"] or 0) or dist
        mode = payload.get("mode", "interval")
        if mode == "outputs":
            cycle = trip_time + trip_time_back + rest * 2
            interval = max(5, cycle // max(1, outputs))
        else:
            interval = int(payload.get("interval", route["interval_min"] or max(5, (trip_time + rest) // max(1, outputs))))
        con.execute("DELETE FROM route_trips WHERE route_id=? AND day_type=?", (rid, day_type))
        total = 0
        for k in range(outputs):
            t = first + k * interval
            trip_no, direction = 1, "прямое"
            mid = first + (last - first) // 2
            lunch_done = False
            while t <= last:
                arr = t + (trip_time if direction == "прямое" else trip_time_back)
                shift = 1 if t < mid else 2
                brk, btype = rest, ""
                if not lunch_done and t >= mid:
                    brk, btype, lunch_done = lunch, BREAK_LUNCH, True
                con.execute(
                    "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,trip_number,"
                    "direction,dep_time,arr_time,distance_km,break_after_min,break_type) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, day_type, k + 1, shift, trip_no, direction, N.tstr(t), N.tstr(arr),
                     dist if direction == "прямое" else dist_back, brk, btype))
                t = arr + brk
                trip_no += 1
                direction = "обратное" if direction == "прямое" else "прямое"
                total += 1
        db.audit(con, user["username"], "генерация расписания", "routes", rid,
                 comment=f"{day_type}: {outputs} вых., {total} рейсов")
        con.commit()
        return {"trips": total}
    finally:
        con.close()

def outputs_summary(con, route_id, day_type):
    """Сводка по выходам и сменам."""
    trips = db.rows(con.execute(
        "SELECT * FROM route_trips WHERE route_id=? AND day_type=? ORDER BY output_number, dep_time",
        (route_id, day_type)))
    UNPAID = UNPAID_BREAK_TYPES
    out = {}
    for t in trips:
        key = (t["output_number"], t["shift_number"])
        o = out.setdefault(key, {"output_number": t["output_number"], "shift_number": t["shift_number"],
                                 "start": t["dep_time"], "end": t["arr_time"], "trips": 0, "distance": 0.0,
                                 "break_min": 0, "_last": t["arr_time"]})
        o["trips"] += 1
        o["distance"] = round(o["distance"] + (t["distance_km"] or 0), 1)
        if N.tmin(t["dep_time"]) < N.tmin(o["start"]): o["start"] = t["dep_time"]
        if N.tmin(t["arr_time"]) >= N.tmin(o["_last"]):
            o["end"] = t["arr_time"]; o["_last"] = t["arr_time"]
    # неоплачиваемые перерывы (обед, разрыв) внутри смены — между рейсами, не после последнего
    by_key = {}
    for t in trips:
        by_key.setdefault((t["output_number"], t["shift_number"]), []).append(t)
    for key, ts in by_key.items():
        for t in ts[:-1]:
            if (t["break_type"] or "") in UNPAID:
                out[key]["break_min"] += int(t["break_after_min"] or 0)
    res = []
    for (on, sn), o in sorted(out.items()):
        o.pop("_last", None)
        span = N.shift_minutes(o["start"], o["end"])
        unpaid = o["break_min"] if o["break_min"] else N.lunch_minutes(span)
        o["hours"] = round(max(0, span - unpaid) / 60.0, 2)
        o["night_hours"] = round(N.night_minutes(o["start"], o["end"]) / 60.0, 2)
        res.append(o)
    return res


def _trip_duration(t):
    if not t.get("dep_time") or not t.get("arr_time"):
        return 0
    return N.shift_minutes(t.get("dep_time"), t.get("arr_time"))

def _problem(severity, kind, message, output=None, trip_id=None, trip_number=None, recommendation=""):
    return {
        "severity": severity,
        "kind": kind,
        "output": output or "",
        "trip_id": trip_id or "",
        "trip_number": trip_number or "",
        "message": message,
        "recommendation": recommendation,
    }

def schedule_problems(con, route_id, day_type):
    nrm = db.get_active_norms(con)
    route = db.one(con.execute("SELECT * FROM routes WHERE id=?", (route_id,)))
    problems = []
    trips = db.rows(con.execute(
        "SELECT * FROM route_trips WHERE route_id=? AND day_type=? ORDER BY output_number, dep_time, id",
        (route_id, day_type)))
    by_out = {}
    for t in trips:
        by_out.setdefault(t["output_number"], []).append(t)
        missing = [k for k in ("dep_time", "arr_time", "direction") if not t.get(k)]
        if missing:
            problems.append(_problem(
                "\u043e\u0448\u0438\u0431\u043a\u0430", "missing_fields",
                f"\u0420\u0435\u0439\u0441 {t.get('trip_number') or t['id']}: \u043d\u0435 \u0437\u0430\u043f\u043e\u043b\u043d\u0435\u043d\u044b \u043f\u043e\u043b\u044f {', '.join(missing)}",
                t["output_number"], t["id"], t.get("trip_number"),
                "\u041e\u0442\u043a\u0440\u043e\u0439\u0442\u0435 \u0440\u0435\u0439\u0441 \u0438 \u0437\u0430\u043f\u043e\u043b\u043d\u0438\u0442\u0435 \u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u044b\u0435 \u043f\u043e\u043b\u044f."))
        elif _trip_duration(t) <= 0:
            problems.append(_problem(
                "\u043e\u0448\u0438\u0431\u043a\u0430", "invalid_arrival",
                f"\u0420\u0435\u0439\u0441 {t.get('trip_number')}: \u043d\u0435\u0432\u043e\u0437\u043c\u043e\u0436\u043d\u043e\u0435 \u0432\u0440\u0435\u043c\u044f \u043f\u0440\u0438\u0431\u044b\u0442\u0438\u044f",
                t["output_number"], t["id"], t.get("trip_number"),
                "\u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u0432\u0440\u0435\u043c\u044f \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f \u0438 \u043f\u0440\u0438\u0431\u044b\u0442\u0438\u044f."))

    for on, ts in by_out.items():
        seen_numbers = {}
        for t in ts:
            tn = t.get("trip_number")
            if tn:
                seen_numbers.setdefault(tn, []).append(t)
        for tn, dupes in seen_numbers.items():
            if len(dupes) > 1:
                problems.append(_problem(
                    "\u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435", "duplicate_trip_number",
                    f"\u0412\u044b\u0445\u043e\u0434 {on}: \u043d\u043e\u043c\u0435\u0440 \u0440\u0435\u0439\u0441\u0430 {tn} \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0435\u0442\u0441\u044f {len(dupes)} \u0440\u0430\u0437\u0430",
                    on, dupes[0]["id"], tn,
                    "\u0417\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u0435 \u043f\u0435\u0440\u0435\u043d\u0443\u043c\u0435\u0440\u0430\u0446\u0438\u044e \u0440\u0435\u0439\u0441\u043e\u0432 \u0438\u043b\u0438 \u0438\u0441\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u043d\u043e\u043c\u0435\u0440\u0430 \u0432\u0440\u0443\u0447\u043d\u0443\u044e."))
        for a, b in zip(ts, ts[1:]):
            if not a.get("arr_time") or not b.get("dep_time"):
                continue
            gap = N.tmin(b["dep_time"]) - N.tmin(a["arr_time"])
            required_break = scheduled_break_minutes(a)
            if required_break:
                required_start = N.tmin(a["arr_time"]) + required_break
                if N.tmin(b["dep_time"]) < required_start:
                    break_label = normalize_break_type(a.get("break_type"))
                    problems.append(_problem(
                        "\u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435", "break_gap",
                        f"\u0412\u044b\u0445\u043e\u0434 {on}: \u0440\u0435\u0439\u0441 {b['trip_number']} \u043d\u0430\u0447\u0438\u043d\u0430\u0435\u0442\u0441\u044f {b['dep_time']}, \u043d\u043e \u043f\u043e\u0441\u043b\u0435 \u0440\u0435\u0439\u0441\u0430 {a['trip_number']} \u0443\u043a\u0430\u0437\u0430\u043d {break_label} {required_break} \u043c\u0438\u043d: \u043d\u0443\u0436\u043d\u043e \u043d\u0435 \u0440\u0430\u043d\u044c\u0448\u0435 {N.tstr(required_start)}",
                        on, b["id"], b.get("trip_number"),
                        f"\u0421\u0434\u0432\u0438\u043d\u044c\u0442\u0435 \u0440\u0435\u0439\u0441 {b['trip_number']} \u0434\u043e {N.tstr(required_start)} \u0438\u043b\u0438 \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u0435 \u0434\u043b\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c \u043f\u0435\u0440\u0435\u0440\u044b\u0432\u0430."))
            if gap < 0:
                problems.append(_problem(
                    "\u043a\u0440\u0438\u0442\u0438\u0447\u043d\u043e", "overlap",
                    f"\u0412\u044b\u0445\u043e\u0434 {on}: \u0440\u0435\u0439\u0441 {b['trip_number']} ({b['dep_time']}) \u043d\u0430\u0447\u0438\u043d\u0430\u0435\u0442\u0441\u044f \u0434\u043e \u043f\u0440\u0438\u0431\u044b\u0442\u0438\u044f \u0440\u0435\u0439\u0441\u0430 {a['trip_number']} ({a['arr_time']})",
                    on, b["id"], b.get("trip_number"),
                    "\u0421\u0434\u0432\u0438\u043d\u044c\u0442\u0435 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435, \u043f\u0435\u0440\u0435\u043d\u0435\u0441\u0438\u0442\u0435 \u0440\u0435\u0439\u0441 \u043d\u0430 \u0434\u0440\u0443\u0433\u043e\u0439 \u0432\u044b\u0445\u043e\u0434 \u0438\u043b\u0438 \u0443\u0432\u0435\u043b\u0438\u0447\u044c\u0442\u0435 \u043a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e \u0432\u044b\u0445\u043e\u0434\u043e\u0432."))
            elif gap < 3:
                problems.append(_problem(
                    "\u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435", "short_rest",
                    f"\u0412\u044b\u0445\u043e\u0434 {on}: \u043c\u0435\u0436\u0434\u0443 \u0440\u0435\u0439\u0441\u0430\u043c\u0438 {a['trip_number']} \u0438 {b['trip_number']} \u043c\u0435\u043d\u0435\u0435 3 \u043c\u0438\u043d\u0443\u0442 \u043e\u0442\u0441\u0442\u043e\u044f",
                    on, b["id"], b.get("trip_number"),
                    "\u0423\u0432\u0435\u043b\u0438\u0447\u044c\u0442\u0435 \u043c\u0435\u0436\u0440\u0435\u0439\u0441\u043e\u0432\u044b\u0439 \u043e\u0442\u0441\u0442\u043e\u0439 \u043c\u0438\u043d\u0438\u043c\u0443\u043c \u0434\u043e 3 \u043c\u0438\u043d\u0443\u0442."))
        if ts and ts[0].get("dep_time") and ts[-1].get("arr_time"):
            total_min = N.shift_minutes(ts[0]["dep_time"], ts[-1]["arr_time"])
            if total_min / 60.0 > float(nrm["max_shift_hours_summed"]) and len({t["shift_number"] for t in ts}) < 2:
                problems.append(_problem(
                    "\u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435", "long_output_without_shift_split",
                    f"\u0412\u044b\u0445\u043e\u0434 {on}: \u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c {round(total_min / 60, 1)} \u0447 \u0431\u0435\u0437 \u0434\u0435\u043b\u0435\u043d\u0438\u044f \u043d\u0430 \u0441\u043c\u0435\u043d\u044b",
                    on, ts[-1]["id"], ts[-1].get("trip_number"),
                    "\u0420\u0430\u0437\u0434\u0435\u043b\u0438\u0442\u0435 \u0432\u044b\u0445\u043e\u0434 \u043d\u0430 \u0434\u0432\u0435 \u0441\u043c\u0435\u043d\u044b \u0438\u043b\u0438 \u043d\u0430\u0437\u043d\u0430\u0447\u044c\u0442\u0435 \u043f\u0435\u0440\u0435\u0441\u043c\u0435\u043d\u043a\u0443."))
            if not any((t["break_after_min"] or 0) >= int(nrm["break_min_minutes"]) for t in ts) and total_min > 300:
                problems.append(_problem(
                    "\u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435", "missing_lunch",
                    f"\u0412\u044b\u0445\u043e\u0434 {on}: \u043d\u0435\u0442 \u043f\u0435\u0440\u0435\u0440\u044b\u0432\u0430 \u043d\u0435 \u043c\u0435\u043d\u0435\u0435 {nrm['break_min_minutes']} \u043c\u0438\u043d",
                    on, ts[0]["id"], ts[0].get("trip_number"),
                    "\u0414\u043e\u0431\u0430\u0432\u044c\u0442\u0435 \u043e\u0431\u0435\u0434, \u043f\u0435\u0440\u0435\u0441\u043c\u0435\u043d\u043a\u0443 \u0438\u043b\u0438 \u0442\u0435\u0445\u043d\u043e\u043b\u043e\u0433\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u043f\u0435\u0440\u0435\u0440\u044b\u0432."))

    if route and route["interval_min"] and len(trips) > 1:
        starts = sorted([N.tmin(t["dep_time"]) for t in trips if t.get("dep_time")])
        expected = int(route["interval_min"])
        for prev, cur in zip(starts, starts[1:]):
            gap = cur - prev
            if gap > expected * 2:
                problems.append(_problem(
                    "\u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435", "large_interval_gap",
                    f"\u041c\u0435\u0436\u0434\u0443 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f\u043c\u0438 {N.tstr(prev)} \u0438 {N.tstr(cur)} \u0438\u043d\u0442\u0435\u0440\u0432\u0430\u043b {gap} \u043c\u0438\u043d \u043f\u0440\u0438 \u043d\u043e\u0440\u043c\u0430\u0442\u0438\u0432\u0435 {expected} \u043c\u0438\u043d",
                    recommendation="\u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u0440\u0430\u0432\u043d\u043e\u043c\u0435\u0440\u043d\u043e\u0441\u0442\u044c \u0432\u044b\u043f\u0443\u0441\u043a\u0430 \u0438\u043b\u0438 \u0434\u043e\u0431\u0430\u0432\u044c\u0442\u0435 \u0440\u0435\u0439\u0441."))
    return problems

def schedule_summary(con, route_id, day_type):
    trips = db.rows(con.execute(
        "SELECT * FROM route_trips WHERE route_id=? AND day_type=? ORDER BY output_number, dep_time, id",
        (route_id, day_type)))
    outputs = outputs_summary(con, route_id, day_type)
    problems = schedule_problems(con, route_id, day_type)
    counts = {"\u043a\u0440\u0438\u0442\u0438\u0447\u043d\u043e": 0, "\u043e\u0448\u0438\u0431\u043a\u0430": 0, "\u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435": 0}
    for p in problems:
        counts[p["severity"]] = counts.get(p["severity"], 0) + 1
    first_dep = min([t["dep_time"] for t in trips if t.get("dep_time")], default="")
    last_arr = max([t["arr_time"] for t in trips if t.get("arr_time")], default="")
    distance = round(sum((t["distance_km"] or 0) for t in trips), 1)
    outputs_count = len({t["output_number"] for t in trips})
    return {
        "route_id": route_id,
        "day_type": day_type,
        "trips_count": len(trips),
        "outputs_count": outputs_count,
        "shift_count": len(outputs),
        "bus_need": outputs_count,
        "driver_need": len(outputs),
        "distance_km": distance,
        "first_dep": first_dep,
        "last_arr": last_arr,
        "problems_count": len(problems),
        "critical_count": counts.get("\u043a\u0440\u0438\u0442\u0438\u0447\u043d\u043e", 0),
        "error_count": counts.get("\u043e\u0448\u0438\u0431\u043a\u0430", 0),
        "warning_count": counts.get("\u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435", 0),
    }

@router.get("/routes/{rid}/outputs")
def route_outputs(rid: int, day_type: str = "будни", user=Depends(current_user)):
    con = db.connect()
    try:
        return {"items": outputs_summary(con, rid, day_type)}
    finally:
        con.close()

@router.get("/routes/{rid}/check")
def route_check(rid: int, day_type: str = "\u0431\u0443\u0434\u043d\u0438", user=Depends(current_user)):
    con = db.connect()
    try:
        return {"problems": schedule_problems(con, rid, day_type), "outputs": outputs_summary(con, rid, day_type)}
    finally:
        con.close()

@router.get("/routes/{rid}/schedule-summary")
def route_schedule_summary(rid: int, day_type: str = "\u0431\u0443\u0434\u043d\u0438", user=Depends(current_user)):
    con = db.connect()
    try:
        route = db.one(con.execute("SELECT id FROM routes WHERE id=?", (rid,)))
        if not route:
            raise HTTPException(404, "\u041c\u0430\u0440\u0448\u0440\u0443\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
        return schedule_summary(con, rid, day_type)
    finally:
        con.close()

@router.get("/routes/{rid}/schedule-export.xlsx")
def route_schedule_export(rid: int, day_type: str = "\u0431\u0443\u0434\u043d\u0438", user=Depends(current_user)):
    con = db.connect()
    try:
        route = db.one(con.execute("SELECT * FROM routes WHERE id=?", (rid,)))
        if not route:
            raise HTTPException(404, "\u041c\u0430\u0440\u0448\u0440\u0443\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
        trips = db.rows(con.execute(
            "SELECT * FROM route_trips WHERE route_id=? AND day_type=? ORDER BY output_number, dep_time, id",
            (rid, day_type)))
        title = f"\u0420\u0430\u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435 \u043c\u0430\u0440\u0448\u0440\u0443\u0442\u0430 \u2116 {route['number']} ({day_type})"
        headers = ["\u041c\u0430\u0440\u0448\u0440\u0443\u0442", "\u0422\u0438\u043f \u0434\u043d\u044f", "\u0412\u044b\u0445\u043e\u0434", "\u0421\u043c\u0435\u043d\u0430", "\u0420\u0435\u0439\u0441", "\u041d\u0430\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435",
                   "\u041e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435", "\u041f\u0440\u0438\u0431\u044b\u0442\u0438\u0435", "\u0414\u043b\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0441\u0442\u044c, \u043c\u0438\u043d", "\u041f\u0440\u043e\u0431\u0435\u0433, \u043a\u043c",
                   "\u041e\u0442\u0441\u0442\u043e\u0439, \u043c\u0438\u043d", "\u0422\u0438\u043f \u043f\u0435\u0440\u0435\u0440\u044b\u0432\u0430"]
        rows_ = [[
            route["number"], day_type, t["output_number"], t["shift_number"], t["trip_number"],
            t["direction"], t["dep_time"], t["arr_time"], _trip_duration(t),
            t["distance_km"], t["break_after_min"] or 0, t["break_type"] or ""
        ] for t in trips]
        return xlsx_response(title, headers, rows_, filename=f"schedule_route_{route['number']}_{day_type}.xlsx")
    finally:
        con.close()

# ================= ГРАФИКИ =================
PATTERNS = {
    "2/2": ["Р", "Р", "В", "В"],
    "5/2": None,  # по производственному календарю
    "6/1": ["Р", "Р", "Р", "Р", "Р", "Р", "В"],
    "3/1": ["Р", "Р", "Р", "В"],
    "4/2": ["Р", "Р", "Р", "Р", "В", "В"],
    "1/1": ["Р", "В"],
}


UNPAID_BREAK_TYPES = (BREAK_LUNCH, BREAK_SPLIT, "\u043e\u0431\u0435\u0434/\u043f\u0435\u0440\u0435\u0441\u043c\u0435\u043d\u043a\u0430")


def _assignment_metrics(trips, start_time=None, end_time=None, break_min=None):
    trips = [dict(t) for t in trips]
    if not trips and not (start_time and end_time):
        return {
            "trip_from": None, "trip_to": None, "start_time": "", "end_time": "",
            "hours": 0, "night_hours": 0, "break_min": 0, "distance_km": 0, "trips_count": 0,
        }
    start = start_time or trips[0].get("dep_time")
    end = end_time or trips[-1].get("arr_time")
    auto_break = 0
    for t in trips[:-1]:
        if (t.get("break_type") or "") in UNPAID_BREAK_TYPES:
            auto_break += int(t.get("break_after_min") or 0)
    brk = auto_break if break_min is None else int(break_min or 0)
    span = N.shift_minutes(start, end)
    unpaid = brk if brk else N.lunch_minutes(span)
    return {
        "trip_from": trips[0].get("trip_number") if trips else None,
        "trip_to": trips[-1].get("trip_number") if trips else None,
        "start_time": start,
        "end_time": end,
        "hours": round(max(0, span - unpaid) / 60.0, 2),
        "night_hours": round(N.night_minutes(start, end) / 60.0, 2),
        "break_min": brk,
        "distance_km": round(sum(float(t.get("distance_km") or 0) for t in trips), 1),
        "trips_count": len(trips),
    }


def _assignment_trips(con, route_id, day_type, output_number, shift_number, trip_from=0, trip_to=0):
    trips = db.rows(con.execute(
        "SELECT * FROM route_trips WHERE route_id=? AND day_type=? AND output_number=? AND shift_number=? "
        "ORDER BY trip_number, dep_time, id",
        (route_id, day_type, output_number, shift_number)))
    if trip_from:
        trips = [t for t in trips if (t.get("trip_number") or 0) >= trip_from]
    if trip_to:
        trips = [t for t in trips if (t.get("trip_number") or 0) <= trip_to]
    return trips


@router.get("/roster/schedule-options")
def roster_schedule_options(route_id: int, date: str, output_number: int = 0, shift_number: int = 0,
                            trip_from: int = 0, trip_to: int = 0, user=Depends(current_user)):
    con = db.connect()
    try:
        route = db.one(con.execute("SELECT * FROM routes WHERE id=?", (route_id,)))
        if not route:
            raise HTTPException(404, "Маршрут не найден")
        day_type = sched_day_type(con, date)
        outputs = outputs_summary(con, route_id, day_type)
        trips = []
        suggestion = _assignment_metrics([])
        if output_number and shift_number:
            trips = _assignment_trips(con, route_id, day_type, output_number, shift_number, trip_from, trip_to)
            suggestion = _assignment_metrics(trips)
        return {"day_type": day_type, "outputs": outputs, "trips": trips, "suggestion": suggestion}
    finally:
        con.close()

@router.post("/roster/generate")
def roster_generate(payload: dict = Body(...), user=Depends(current_user)):
    """Автоформирование графика на период по шаблону."""
    require_write(user, "roster")
    con = db.connect()
    try:
        date_from = payload["date_from"]; date_to = payload["date_to"]
        template = payload.get("template", "")
        driver_ids = payload.get("driver_ids") or [d["id"] for d in
            con.execute("SELECT id FROM drivers WHERE status='работает'")]
        route_id = payload.get("route_id")
        overwrite = bool(payload.get("overwrite", False))
        d0 = datetime.date.fromisoformat(date_from); d1 = datetime.date.fromisoformat(date_to)
        made = 0
        slot_taken = {}   # (дата, маршрут) -> занятые индексы слотов
        driver_pref = {}  # (водитель, маршрут) -> предпочтительная (стабильная) смена
        for idx, did in enumerate(driver_ids):
            drv = db.one(con.execute("SELECT * FROM drivers WHERE id=?", (did,)))
            if not drv: continue
            tpl = template or drv["default_schedule"] or "2/2"
            rid = route_id or drv["assigned_route_id"]
            outs = []
            if rid:
                # выходы будних как база
                outs = outputs_summary(con, rid, "будни")
            d = d0; day_i = 0
            while d <= d1:
                iso = d.isoformat()
                exist = db.one(con.execute("SELECT * FROM roster WHERE driver_id=? AND date=?", (did, iso)))
                if exist and not overwrite and exist["status"] not in ("работа", "выходной"):
                    d += datetime.timedelta(days=1); day_i += 1; continue
                if exist and not overwrite and exist["approved"]:
                    d += datetime.timedelta(days=1); day_i += 1; continue
                pat = PATTERNS.get(tpl)
                if pat is None and tpl == "5/2":
                    workday = not is_weekend(con, iso)
                else:
                    pat = pat or PATTERNS["2/2"]
                    workday = pat[(day_i + idx * 2) % len(pat)] == "Р"
                if workday and outs:
                    dt_sched = sched_day_type(con, iso)
                    outs_day = outputs_summary(con, rid, dt_sched) or outs
                    k = (iso, rid)
                    taken = slot_taken.setdefault(k, set())
                    pref_shift = driver_pref.get((did, rid))  # закреплённая смена водителя
                    slot_idx = None
                    if pref_shift is not None:  # сначала ищем свободный слот той же смены
                        for cand in range(len(outs_day)):
                            if cand not in taken and outs_day[cand]["shift_number"] == pref_shift:
                                slot_idx = cand
                                break
                    if slot_idx is None:
                        for cand in range(len(outs_day)):
                            if cand not in taken:
                                slot_idx = cand
                                break
                    if slot_idx is None:
                        con.execute("INSERT INTO roster(driver_id,date,status,comment) VALUES(?,?, 'РЗ', 'резерв (все выходы заняты)') "
                                    "ON CONFLICT(driver_id,date) DO UPDATE SET status='РЗ', route_id=NULL,"
                                    "output_number=NULL, shift_number=NULL, start_time=NULL, end_time=NULL, hours=0, night_hours=0",
                                    (did, iso))
                        made += 1
                        d += datetime.timedelta(days=1); day_i += 1
                        continue
                    taken.add(slot_idx)
                    driver_pref.setdefault((did, rid), outs_day[slot_idx]["shift_number"])
                    slot = outs_day[slot_idx]
                    con.execute(
                        "INSERT INTO roster(driver_id,date,status,route_id,output_number,shift_number,"
                        "start_time,end_time,hours,night_hours,break_min) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(driver_id,date) DO UPDATE SET status=excluded.status, route_id=excluded.route_id,"
                        "output_number=excluded.output_number, shift_number=excluded.shift_number,"
                        "start_time=excluded.start_time, end_time=excluded.end_time, hours=excluded.hours,"
                        "night_hours=excluded.night_hours, break_min=excluded.break_min",
                        (did, iso, "работа", rid, slot["output_number"], slot["shift_number"],
                         slot["start"], slot["end"], slot["hours"], slot["night_hours"], slot.get("break_min", 0)))
                elif workday:
                    con.execute("INSERT INTO roster(driver_id,date,status,comment) VALUES(?,?,?,?) "
                                "ON CONFLICT(driver_id,date) DO UPDATE SET status=excluded.status",
                                (did, iso, "РЗ", "резерв (маршрут не закреплён)"))
                else:
                    con.execute("INSERT INTO roster(driver_id,date,status) VALUES(?,?, 'выходной') "
                                "ON CONFLICT(driver_id,date) DO UPDATE SET status='выходной', route_id=NULL,"
                                "output_number=NULL, shift_number=NULL, start_time=NULL, end_time=NULL, hours=0, night_hours=0",
                                (did, iso))
                made += 1
                d += datetime.timedelta(days=1); day_i += 1
        db.audit(con, user["username"], "формирование графика", "roster", None,
                 comment=f"{date_from}—{date_to}, шаблон {template or 'индивидуальный'}, водителей {len(driver_ids)}")
        con.commit()
        return {"made": made}
    finally:
        con.close()



def _refresh_roster_from_assignments(con, driver_id, date):
    items = db.rows(con.execute(
        "SELECT ra.*, rt.number AS route_number FROM roster_assignments ra "
        "LEFT JOIN routes rt ON rt.id=ra.route_id WHERE ra.driver_id=? AND ra.date=? "
        "ORDER BY start_time, id", (driver_id, date)))
    if not items:
        old = db.one(con.execute("SELECT * FROM roster WHERE driver_id=? AND date=?", (driver_id, date)))
        if old and old.get("status") == "работа":
            con.execute(
                "UPDATE roster SET route_id=NULL, output_number=NULL, shift_number=NULL, start_time=NULL, end_time=NULL, "
                "hours=0, night_hours=0, break_min=0, comment='назначений нет' WHERE driver_id=? AND date=?",
                (driver_id, date))
        return None
    first = items[0]
    starts = [i for i in items if i.get("start_time")]
    ends = [i for i in items if i.get("end_time")]
    start = min(starts, key=lambda x: N.tmin(x["start_time"]))["start_time"] if starts else None
    end = max(ends, key=lambda x: N.tmin(x["end_time"]))["end_time"] if ends else None
    hours = round(sum(float(i.get("hours") or 0) for i in items), 2)
    night = round(sum(float(i.get("night_hours") or 0) for i in items), 2)
    brk = sum(int(i.get("break_min") or 0) for i in items)
    labels = [f"№{i.get('route_number') or ''} {i.get('output_number')}/{i.get('shift_number')}" for i in items]
    comment = "назначений: " + str(len(items)) + " (" + ", ".join(labels) + ")"
    con.execute(
        "INSERT INTO roster(driver_id,date,status,route_id,output_number,shift_number,start_time,end_time,hours,night_hours,break_min,comment) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(driver_id,date) DO UPDATE SET "
        "status='работа', route_id=excluded.route_id, output_number=excluded.output_number, shift_number=excluded.shift_number, "
        "start_time=excluded.start_time, end_time=excluded.end_time, hours=excluded.hours, night_hours=excluded.night_hours, "
        "break_min=excluded.break_min, comment=excluded.comment",
        (driver_id, date, "работа", first["route_id"], first["output_number"], first["shift_number"],
         start, end, hours, night, brk, comment))
    return db.one(con.execute("SELECT * FROM roster WHERE driver_id=? AND date=?", (driver_id, date)))


@router.get("/roster/assignments")
def roster_assignments(driver_id: int, date: str, user=Depends(current_user)):
    con = db.connect()
    try:
        items = db.rows(con.execute(
            "SELECT ra.*, rt.number AS route_number, rt.name AS route_name FROM roster_assignments ra "
            "LEFT JOIN routes rt ON rt.id=ra.route_id WHERE ra.driver_id=? AND ra.date=? ORDER BY start_time, id",
            (driver_id, date)))
        return {"items": items}
    finally:
        con.close()


@router.post("/roster/assignment")
def roster_assignment_save(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "roster")
    con = db.connect()
    try:
        driver_id = int(payload["driver_id"])
        date = payload["date"]
        route_id = int(payload["route_id"])
        output_number = int(payload.get("output_number") or 1)
        shift_number = int(payload.get("shift_number") or 1)
        trip_from = int(payload.get("trip_from") or 0) or None
        trip_to = int(payload.get("trip_to") or 0) or None
        if not db.one(con.execute("SELECT id FROM drivers WHERE id=?", (driver_id,))):
            raise HTTPException(404, "Водитель не найден")
        if not db.one(con.execute("SELECT id FROM routes WHERE id=?", (route_id,))):
            raise HTTPException(404, "Маршрут не найден")
        day_type = payload.get("day_type") or sched_day_type(con, date)
        trips = _assignment_trips(con, route_id, day_type, output_number, shift_number, trip_from or 0, trip_to or 0)
        if not trips and not (payload.get("start_time") and payload.get("end_time")):
            raise HTTPException(400, "В расписании не найдены рейсы для выбранного выхода и смены")
        metrics = _assignment_metrics(
            trips,
            payload.get("start_time") or None,
            payload.get("end_time") or None,
            payload.get("break_min") if "break_min" in payload else None)
        fields = ["driver_id", "date", "route_id", "day_type", "output_number", "shift_number", "trip_from", "trip_to",
                  "start_time", "end_time", "hours", "night_hours", "break_min", "distance_km", "trips_count", "comment"]
        rec = {
            "driver_id": driver_id, "date": date, "route_id": route_id, "day_type": day_type,
            "output_number": output_number, "shift_number": shift_number,
            "trip_from": trip_from or metrics.get("trip_from"), "trip_to": trip_to or metrics.get("trip_to"),
            "start_time": metrics["start_time"], "end_time": metrics["end_time"],
            "hours": metrics["hours"], "night_hours": metrics["night_hours"], "break_min": metrics["break_min"],
            "distance_km": metrics["distance_km"], "trips_count": metrics["trips_count"],
            "comment": payload.get("comment", ""),
        }
        if payload.get("id"):
            aid = int(payload["id"])
            old = db.one(con.execute("SELECT * FROM roster_assignments WHERE id=?", (aid,)))
            if not old:
                raise HTTPException(404, "Назначение не найдено")
            con.execute("UPDATE roster_assignments SET " + ",".join(f + "=?" for f in fields) + " WHERE id=?",
                        [rec[f] for f in fields] + [aid])
            db.audit(con, user["username"], "изменение назначения графика", "roster_assignments", aid, old=old, new=rec)
        else:
            cur = con.execute("INSERT INTO roster_assignments(" + ",".join(fields) + ") VALUES(" + ",".join("?" * len(fields)) + ")",
                              [rec[f] for f in fields])
            aid = cur.lastrowid
            db.audit(con, user["username"], "создание назначения графика", "roster_assignments", aid, new=rec)
        _refresh_roster_from_assignments(con, driver_id, date)
        dd = datetime.date.fromisoformat(date)
        vio = [v for v in N.check_period(con, (dd - datetime.timedelta(days=7)).isoformat(),
                                         (dd + datetime.timedelta(days=7)).isoformat(), driver_id)
               if v["date"] == date]
        con.commit()
        assignment = db.one(con.execute("SELECT * FROM roster_assignments WHERE id=?", (aid,)))
        return {"ok": True, "assignment": assignment, "violations": vio}
    finally:
        con.close()


@router.delete("/roster/assignment/{assignment_id}")
def roster_assignment_delete(assignment_id: int, user=Depends(current_user)):
    require_write(user, "roster")
    con = db.connect()
    try:
        old = db.one(con.execute("SELECT * FROM roster_assignments WHERE id=?", (assignment_id,)))
        if not old:
            raise HTTPException(404, "Назначение не найдено")
        con.execute("DELETE FROM roster_assignments WHERE id=?", (assignment_id,))
        _refresh_roster_from_assignments(con, old["driver_id"], old["date"])
        db.audit(con, user["username"], "удаление назначения графика", "roster_assignments", assignment_id, old=old)
        con.commit()
        return {"ok": True}
    finally:
        con.close()

@router.get("/roster")
def roster_get(date_from: str, date_to: str, driver_id: int = 0, user=Depends(current_user)):
    con = db.connect()
    try:
        q = ("SELECT r.*, d.fio, d.tab_number, rt.number AS route_number, "
             "(SELECT COUNT(*) FROM roster_assignments ra WHERE ra.driver_id=r.driver_id AND ra.date=r.date) AS assignment_count, "
             "(SELECT GROUP_CONCAT(COALESCE(rr.number,'') || ' ' || ra.output_number || '/' || ra.shift_number, ', ') "
             " FROM roster_assignments ra LEFT JOIN routes rr ON rr.id=ra.route_id "
             " WHERE ra.driver_id=r.driver_id AND ra.date=r.date) AS assignment_label "
             "FROM roster r JOIN drivers d ON d.id=r.driver_id LEFT JOIN routes rt ON rt.id=r.route_id "
             "WHERE r.date>=? AND r.date<=?")
        args = [date_from, date_to]
        if driver_id: q += " AND r.driver_id=?"; args.append(driver_id)
        items = db.rows(con.execute(q + " ORDER BY d.fio, r.date", args))
        return {"items": items}
    finally:
        con.close()

@router.post("/roster/entry")
def roster_entry(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "roster")
    con = db.connect()
    try:
        did, date = payload["driver_id"], payload["date"]
        old = db.one(con.execute("SELECT * FROM roster WHERE driver_id=? AND date=?", (did, date)))
        break_min = int(payload.get("break_min") or 0)
        h = payload.get("hours")
        if payload.get("start_time") and payload.get("end_time") and h in (None, ""):
            dur = N.shift_minutes(payload["start_time"], payload["end_time"])
            # обед/разрывы: заданные вручную минуты, иначе автооценка
            unpaid = break_min if break_min else N.lunch_minutes(dur)
            h = round(max(0, dur - unpaid) / 60.0, 2)
        nh = round(N.night_minutes(payload.get("start_time"), payload.get("end_time")) / 60.0, 2) \
             if payload.get("start_time") and payload.get("end_time") else 0
        con.execute(
            "INSERT INTO roster(driver_id,date,status,route_id,output_number,shift_number,start_time,end_time,hours,night_hours,break_min,comment) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(driver_id,date) DO UPDATE SET "
            "status=excluded.status, route_id=excluded.route_id, output_number=excluded.output_number, "
            "shift_number=excluded.shift_number, start_time=excluded.start_time, end_time=excluded.end_time, "
            "hours=excluded.hours, night_hours=excluded.night_hours, break_min=excluded.break_min, comment=excluded.comment",
            (did, date, payload.get("status", "работа"), payload.get("route_id"),
             payload.get("output_number"), payload.get("shift_number"),
             payload.get("start_time"), payload.get("end_time"), h or 0, nh, break_min, payload.get("comment", "")))
        db.audit(con, user["username"], "изменение графика", "roster", f"{did}/{date}", old=old, new=payload)
        con.commit()
        # проверка этого дня
        drv = db.one(con.execute("SELECT * FROM drivers WHERE id=?", (did,)))
        d = datetime.date.fromisoformat(date)
        vio = N.check_period(con, (d - datetime.timedelta(days=7)).isoformat(),
                             (d + datetime.timedelta(days=7)).isoformat(), driver_id=did)
        return {"ok": True, "violations": [v for v in vio if v["date"] == date]}
    finally:
        con.close()

@router.get("/roster/check")
def roster_check(date_from: str, date_to: str, driver_id: int = 0, user=Depends(current_user)):
    con = db.connect()
    try:
        return {"violations": N.check_period(con, date_from, date_to, driver_id or None)}
    finally:
        con.close()

@router.post("/roster/approve")
def roster_approve(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "roster")
    con = db.connect()
    try:
        vio = N.check_period(con, payload["date_from"], payload["date_to"])
        crit = [v for v in vio if v["severity"] == "критично"]
        if crit and not payload.get("force_comment"):
            raise HTTPException(409, f"Критических нарушений: {len(crit)}. Утверждение запрещено. "
                                     "Исправьте график или укажите обоснование допустимого исключения.")
        con.execute("UPDATE roster SET approved=1 WHERE date>=? AND date<=?",
                    (payload["date_from"], payload["date_to"]))
        db.audit(con, user["username"], "утверждение графика", "roster",
                 f"{payload['date_from']}—{payload['date_to']}",
                 comment=payload.get("force_comment", "") + f" (нарушений: {len(vio)}, критичных: {len(crit)})")
        con.commit()
        return {"ok": True, "violations": len(vio), "critical": len(crit)}
    finally:
        con.close()

# ================= НАРЯДЫ =================
def month_coeff(con, iso_date):
    m = datetime.date.fromisoformat(iso_date).month
    return "winter" if m in (11, 12, 1, 2, 3) else "summer"

def planned_fuel(bus, distance, iso_date):
    if not bus or not distance: return 0
    rate = bus["fuel_rate"] or 30
    k = bus["winter_coeff"] if datetime.date.fromisoformat(iso_date).month in (11, 12, 1, 2, 3) else 1.0
    return round(distance * rate * (k or 1.0) / 100.0, 1)

@router.post("/orders/generate")
def order_generate(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "orders")
    con = db.connect()
    try:
        date = payload["date"]
        nrm = db.get_active_norms(con, date)
        ex = db.one(con.execute("SELECT * FROM orders WHERE date=?", (date,)))
        if ex and not payload.get("regenerate"):
            raise HTTPException(409, "Наряд на эту дату уже существует")
        if ex:
            if ex["status"] in ("утвержден", "выдан", "выполнен") and not payload.get("force"):
                raise HTTPException(409, "Наряд уже утверждён. Отмените утверждение для пересоздания.")
            con.execute("DELETE FROM order_lines WHERE order_id=?", (ex["id"],))
            oid = ex["id"]
            con.execute("UPDATE orders SET status='черновик' WHERE id=?", (oid,))
        else:
            oid = con.execute("INSERT INTO orders(date,status) VALUES(?, 'черновик')", (date,)).lastrowid
        dt_sched = sched_day_type(con, date)
        used_drivers, used_buses, warns, lines = set(), set(), [], 0
        prep = int(nrm["prep_final_minutes"]) + int(nrm["med_check_minutes"])
        for route in con.execute("SELECT * FROM routes WHERE active=1 ORDER BY number"):
            for slot in outputs_summary(con, route["id"], dt_sched):
                assignment = db.one(con.execute(
                    "SELECT ra.*, d.status AS dstatus FROM roster_assignments ra JOIN drivers d ON d.id=ra.driver_id "
                    "WHERE ra.date=? AND ra.route_id=? AND ra.output_number=? AND ra.shift_number=? "
                    "AND d.status='работает' ORDER BY ra.id LIMIT 1",
                    (date, route["id"], slot["output_number"], slot["shift_number"])))
                assignment_based = bool(assignment)
                line_start = assignment["start_time"] if assignment and assignment["start_time"] else slot["start"]
                line_end = assignment["end_time"] if assignment and assignment["end_time"] else slot["end"]
                line_hours = assignment["hours"] if assignment else slot["hours"]
                line_trips = assignment["trips_count"] if assignment else slot["trips"]
                line_distance = assignment["distance_km"] if assignment else slot["distance"]
                did = assignment["driver_id"] if assignment else None
                if not did:
                    # водитель из старого графика
                    r = db.one(con.execute(
                        "SELECT r.*, d.status AS dstatus FROM roster r JOIN drivers d ON d.id=r.driver_id "
                        "WHERE r.date=? AND r.route_id=? AND r.output_number=? AND r.shift_number=? "
                        "AND r.status='работа' AND d.status='работает'",
                        (date, route["id"], slot["output_number"], slot["shift_number"])))
                    did = r["driver_id"] if r and r["driver_id"] not in used_drivers else None
                if not did:
                    # резерв: водитель в статусе РЗ на эту дату
                    rz = db.one(con.execute(
                        "SELECT r.driver_id FROM roster r JOIN drivers d ON d.id=r.driver_id "
                        "WHERE r.date=? AND r.status='РЗ' AND d.status='работает' AND r.driver_id NOT IN (%s) LIMIT 1"
                        % (",".join(str(x) for x in used_drivers) or "0"), (date,)))
                    did = rz["driver_id"] if rz else None
                    if did:
                        warns.append(f"Маршрут {route['number']} вых.{slot['output_number']} см.{slot['shift_number']}: назначен резервный водитель")
                bus_id = None
                if did:
                    used_drivers.add(did)
                    drv = db.one(con.execute("SELECT * FROM drivers WHERE id=?", (did,)))
                    b = None
                    if drv["assigned_bus_id"] and (assignment_based or drv["assigned_bus_id"] not in used_buses):
                        b = db.one(con.execute("SELECT * FROM buses WHERE id=? AND status IN ('исправен','на линии')",
                                               (drv["assigned_bus_id"],)))
                    if not b:
                        b = db.one(con.execute(
                            "SELECT * FROM buses WHERE status='исправен' AND id NOT IN (%s) ORDER BY garage_number LIMIT 1"
                            % (",".join(str(x) for x in used_buses) or "0")))
                    if b:
                        bus_id = b["id"]; used_buses.add(bus_id)
                    else:
                        warns.append(f"Маршрут {route['number']} вых.{slot['output_number']}: нет свободного исправного автобуса")
                else:
                    warns.append(f"Маршрут {route['number']} вых.{slot['output_number']} см.{slot['shift_number']}: не хватает водителей")
                bus = db.one(con.execute("SELECT * FROM buses WHERE id=?", (bus_id,))) if bus_id else None
                st, en = N.tmin(line_start), N.tmin(line_end)
                con.execute(
                    "INSERT INTO order_lines(order_id,route_id,output_number,shift_number,driver_id,bus_id,"
                    "report_time,depart_depot,start_line,end_line,return_depot,shift_hours,trips_count,distance_km,planned_fuel,status) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (oid, route["id"], slot["output_number"], slot["shift_number"], did, bus_id,
                     N.tstr(st - prep - 10), N.tstr(st - 10), line_start, line_end, N.tstr((en if en > st else en + 1440) + 10),
                     line_hours, line_trips, line_distance,
                     planned_fuel(bus, line_distance, date), "план"))
                lines += 1
        for w in warns:
            db.notify(con, "warning", "наряд", f"{date}: {w}")
        db.audit(con, user["username"], "формирование наряда", "orders", oid,
                 comment=f"{date}: строк {lines}, предупреждений {len(warns)}")
        con.commit()
        return {"order_id": oid, "lines": lines, "warnings": warns}
    finally:
        con.close()

@router.get("/orders")
def order_get(date: str, user=Depends(current_user)):
    con = db.connect()
    try:
        o = db.one(con.execute("SELECT * FROM orders WHERE date=?", (date,)))
        if not o: return {"order": None, "lines": []}
        lines = db.rows(con.execute(
            "SELECT l.*, r.number AS route_number, r.name AS route_name, d.fio, d.tab_number, "
            "b.garage_number, b.plate, b.brand, b.model, "
            "(SELECT w.id FROM waybills w WHERE w.order_line_id=l.id AND w.status!='аннулирован' LIMIT 1) AS waybill_id, "
            "(SELECT w.number FROM waybills w WHERE w.order_line_id=l.id AND w.status!='аннулирован' LIMIT 1) AS waybill_number "
            "FROM order_lines l LEFT JOIN routes r ON r.id=l.route_id LEFT JOIN drivers d ON d.id=l.driver_id "
            "LEFT JOIN buses b ON b.id=l.bus_id WHERE l.order_id=? ORDER BY r.number, l.output_number, l.shift_number",
            (o["id"],)))
        return {"order": o, "lines": lines}
    finally:
        con.close()

@router.get("/orders/candidates")
def order_candidates(date: str, line_id: int = 0, user=Depends(current_user)):
    """Доступные водители на дату (для замены)."""
    con = db.connect()
    try:
        o = db.one(con.execute("SELECT * FROM orders WHERE date=?", (date,)))
        busy = {r["driver_id"] for r in con.execute(
            "SELECT driver_id FROM order_lines WHERE order_id=? AND driver_id IS NOT NULL", (o["id"],))} if o else set()
        line = db.one(con.execute("SELECT * FROM order_lines WHERE id=?", (line_id,))) if line_id else None
        out = []
        for d in con.execute("SELECT * FROM drivers WHERE status='работает' ORDER BY fio"):
            if d["id"] in busy and (not line or line["driver_id"] != d["id"]): continue
            r = db.one(con.execute("SELECT * FROM roster WHERE driver_id=? AND date=?", (d["id"], date)))
            reason = ""
            if r and r["status"] not in ("работа", "выходной", "РЗ"):
                reason = f"отсутствие: {r['status']}"
            lic = d["license_expires"]
            if lic and lic < date:
                reason = (reason + "; " if reason else "") + "истёк срок водительского удостоверения"
            vio = []
            if not reason and line:
                # быстрая проверка 424 при подстановке
                con.execute("SAVEPOINT c")
                con.execute("INSERT INTO roster(driver_id,date,status,route_id,output_number,shift_number,start_time,end_time,hours) "
                            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(driver_id,date) DO UPDATE SET status='работа',"
                            "start_time=excluded.start_time, end_time=excluded.end_time, hours=excluded.hours",
                            (d["id"], date, "работа", line["route_id"], line["output_number"], line["shift_number"],
                             line["start_line"], line["end_line"], line["shift_hours"]))
                dd = datetime.date.fromisoformat(date)
                vio = [v for v in N.check_period(con, (dd - datetime.timedelta(days=6)).isoformat(),
                                                 (dd + datetime.timedelta(days=1)).isoformat(), d["id"])
                       if v["date"] >= date]
                con.execute("ROLLBACK TO c")
            out.append({"id": d["id"], "fio": d["fio"], "tab_number": d["tab_number"],
                        "roster_status": r["status"] if r else "нет в графике",
                        "blocked": bool(reason), "reason": reason,
                        "violations": vio})
        return {"items": out}
    finally:
        con.close()

@router.put("/orders/line/{lid}")
def order_line_update(lid: int, payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "orders")
    con = db.connect()
    try:
        old = db.one(con.execute("SELECT * FROM order_lines WHERE id=?", (lid,)))
        if not old: raise HTTPException(404, "Строка наряда не найдена")
        f = [x for x in ["driver_id","bus_id","report_time","depart_depot","start_line","end_line",
                         "return_depot","dispatcher_note","status"] if x in payload]
        con.execute("UPDATE order_lines SET " + ",".join(x + "=?" for x in f) + " WHERE id=?",
                    [payload[x] for x in f] + [lid])
        action = "корректировка наряда"
        if "driver_id" in payload and payload["driver_id"] != old["driver_id"]: action = "замена водителя"
        if "bus_id" in payload and payload["bus_id"] != old["bus_id"]: action = "замена автобуса"
        o = db.one(con.execute("SELECT * FROM orders WHERE id=?", (old["order_id"],)))
        if o["status"] in ("утвержден", "выдан"):
            con.execute("UPDATE orders SET status='скорректирован' WHERE id=?", (o["id"],))
        db.audit(con, user["username"], action, "order_lines", lid, old=old, new=payload)
        con.commit()
        return {"ok": True}
    finally:
        con.close()

@router.post("/orders/{oid}/status")
def order_status(oid: int, payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "orders")
    con = db.connect()
    try:
        o = db.one(con.execute("SELECT * FROM orders WHERE id=?", (oid,)))
        if not o: raise HTTPException(404, "Наряд не найден")
        new_status = payload["status"]
        if new_status == "утвержден":
            # блокирующие проверки
            problems = []
            for l in con.execute("SELECT l.*, r.number rn FROM order_lines l LEFT JOIN routes r ON r.id=l.route_id "
                                 "WHERE order_id=? AND status!='отменен'", (oid,)):
                if not l["driver_id"]: problems.append(f"Маршрут {l['rn']} вых.{l['output_number']} см.{l['shift_number']}: нет водителя")
                if not l["bus_id"]: problems.append(f"Маршрут {l['rn']} вых.{l['output_number']} см.{l['shift_number']}: нет автобуса")
            vio = [v for v in N.check_period(con, o["date"], o["date"]) if v["severity"] == "критично"]
            if problems and not payload.get("force_comment"):
                raise HTTPException(409, "Нельзя утвердить: " + "; ".join(problems[:8]))
            if vio and not payload.get("force_comment"):
                raise HTTPException(409, f"Критические нарушения режима труда и отдыха: {len(vio)}. Укажите обоснование или исправьте наряд.")
            con.execute("UPDATE orders SET status=?, approved_by=?, approved_at=? WHERE id=?",
                        (new_status, user["username"], datetime.datetime.now().isoformat(timespec="seconds"), oid))
        else:
            con.execute("UPDATE orders SET status=? WHERE id=?", (new_status, oid))
        db.audit(con, user["username"], f"наряд: {new_status}", "orders", oid,
                 comment=payload.get("force_comment", ""))
        con.commit()
        return {"ok": True}
    finally:
        con.close()

@router.get("/orders/export.xlsx")
def order_export(date: str, user=Depends(current_user)):
    con = db.connect()
    try:
        o = db.one(con.execute("SELECT * FROM orders WHERE date=?", (date,)))
        if not o: raise HTTPException(404, "Наряда нет")
        lines = db.rows(con.execute(
            "SELECT l.*, r.number rn, r.name route_name, d.fio, d.tab_number, b.garage_number, b.plate FROM order_lines l "
            "LEFT JOIN routes r ON r.id=l.route_id LEFT JOIN drivers d ON d.id=l.driver_id "
            "LEFT JOIN buses b ON b.id=l.bus_id WHERE l.order_id=? ORDER BY r.number, l.output_number, l.shift_number", (o["id"],)))
        settings = db.get_settings(con)
        return order_xlsx_response(o, settings, lines, filename=f"naryad_{date}.xlsx")
    finally:
        con.close()




