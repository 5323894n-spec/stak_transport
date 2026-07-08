# -*- coding: utf-8 -*-
"""Нормативные предупреждения режима труда и отдыха (Приказ Минтранса РФ № 424) и расчёт часов."""
import datetime
from . import db

WARNING = "предупреждение"


def tmin(s):
    """'HH:MM' -> минуты."""
    if not s: return None
    h, m = s.split(":")[:2]
    return int(h) * 60 + int(m)


def tstr(minutes):
    minutes = int(round(minutes)) % 1440
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def shift_minutes(start, end):
    """Длительность смены в минутах, с переходом через полночь."""
    s, e = tmin(start), tmin(end)
    if s is None or e is None: return 0
    if e <= s: e += 1440
    return e - s


def night_minutes(start, end, night_start="22:00", night_end="06:00"):
    """Ночные минуты смены (окно 22:00-06:00), с переходом через полночь."""
    s, e = tmin(start), tmin(end)
    if s is None or e is None: return 0
    if e <= s: e += 1440
    ns, ne = tmin(night_start), tmin(night_end)
    total = 0
    for k in (0, 1, 2):
        for w0, w1 in ((ns + 1440 * k, 1440 * (k + 1)), (1440 * k, ne + 1440 * k)):
            total += max(0, min(e, w1) - max(s, w0))
    return total


def lunch_minutes(dur_min):
    """Оценка обеденного перерыва: смена >8ч — 60 мин, >4ч — 30 мин."""
    if dur_min > 480: return 60
    if dur_min > 240: return 30
    return 0


def work_hours(start, end):
    d = shift_minutes(start, end)
    return round((d - lunch_minutes(d)) / 60.0, 2)


def day_type_of(con, iso_date):
    row = con.execute("SELECT day_type FROM calendar WHERE date=?", (iso_date,)).fetchone()
    if row: return row["day_type"]
    return "выходной" if datetime.date.fromisoformat(iso_date).weekday() >= 5 else "рабочий"


def _daily_norm_hours(con, day, week_norm):
    day_type = day_type_of(con, day.isoformat())
    per_day = week_norm / 5.0
    if day_type == "рабочий": return per_day
    if day_type == "предпраздничный": return max(0.0, per_day - 1)
    return 0.0


def month_norm_hours(con, year, month, week_norm=40):
    """Норма часов месяца по производственному календарю (40-ч неделя)."""
    import calendar as pycal
    days = pycal.monthrange(year, month)[1]
    norm = 0.0
    for d in range(1, days + 1):
        norm += _daily_norm_hours(con, datetime.date(year, month, d), float(week_norm))
    return round(norm, 1)


def period_norm_hours(con, start_date, end_date, week_norm=40):
    """Норма часов за произвольный период по производственному календарю."""
    d, end = start_date, end_date
    total = 0.0
    while d <= end:
        total += _daily_norm_hours(con, d, float(week_norm))
        d += datetime.timedelta(days=1)
    return round(total, 1)


def _last_day_of_month(year, month):
    import calendar as pycal
    return pycal.monthrange(year, month)[1]


def _add_months(day, months):
    idx = day.month - 1 + months
    year = day.year + idx // 12
    month = idx % 12 + 1
    return datetime.date(year, month, min(day.day, _last_day_of_month(year, month)))


def _is_full_accounting_period(start_date, end_date, months):
    if months <= 0 or start_date.day != 1:
        return False
    expected_end_month = _add_months(start_date, months - 1)
    expected_end = datetime.date(
        expected_end_month.year,
        expected_end_month.month,
        _last_day_of_month(expected_end_month.year, expected_end_month.month),
    )
    return end_date == expected_end


def _dt(date_iso, time_str, plus_day_if_before=None):
    d = datetime.date.fromisoformat(date_iso)
    t = tmin(time_str) or 0
    base = datetime.datetime(d.year, d.month, d.day) + datetime.timedelta(minutes=t)
    if plus_day_if_before and base <= plus_day_if_before:
        base += datetime.timedelta(days=1)
    return base


def _schedule_day_type(con, iso_date):
    d = datetime.date.fromisoformat(iso_date)
    cal = day_type_of(con, iso_date)
    if cal in ("рабочий", "предпраздничный"):
        return "суббота" if d.weekday() == 5 else ("воскресенье" if d.weekday() == 6 else "будни")
    return "суббота" if d.weekday() == 5 else "воскресенье"


def _trip_minutes_for_slot(con, route_id, day_type, output_number, shift_number, trip_from=0, trip_to=0):
    if not route_id or not output_number or not shift_number:
        return 0, False
    trips = db.rows(con.execute(
        "SELECT * FROM route_trips WHERE route_id=? AND day_type=? AND output_number=? AND shift_number=? "
        "ORDER BY trip_number, dep_time, id",
        (route_id, day_type, output_number, shift_number)))
    if trip_from:
        trips = [t for t in trips if (t.get("trip_number") or 0) >= trip_from]
    if trip_to:
        trips = [t for t in trips if (t.get("trip_number") or 0) <= trip_to]
    minutes = sum(shift_minutes(t.get("dep_time"), t.get("arr_time")) for t in trips)
    return minutes, bool(trips)


def _driving_hours_for_entry(con, entry, norms):
    day_type = _schedule_day_type(con, entry["date"])
    assignments = db.rows(con.execute(
        "SELECT * FROM roster_assignments WHERE driver_id=? AND date=? ORDER BY start_time, id",
        (entry["driver_id"], entry["date"])))
    if assignments:
        total, precise = 0, False
        for a in assignments:
            minutes, found = _trip_minutes_for_slot(
                con, a.get("route_id"), a.get("day_type") or day_type,
                a.get("output_number"), a.get("shift_number"),
                a.get("trip_from") or 0, a.get("trip_to") or 0)
            if found:
                total += minutes
                precise = True
            else:
                total += int(round(float(a.get("hours") or 0) * 60))
        if precise or total:
            return round(total / 60.0, 2)

    minutes, found = _trip_minutes_for_slot(
        con, entry.get("route_id"), day_type, entry.get("output_number"), entry.get("shift_number"))
    if found:
        return round(minutes / 60.0, 2)

    hours = float(entry.get("hours") or work_hours(entry.get("start_time"), entry.get("end_time")) or 0)
    prep = float(norms.get("prep_final_minutes") or 0) / 60.0
    return round(max(0.0, hours - prep), 2)


def violation(drv, date, vtype, norm_val, fact_val, severity, recommendation, route="", output="", shift=""):
    return {"driver_id": drv["id"], "fio": drv["fio"], "tab_number": drv["tab_number"],
            "date": date, "route": route or "", "output": output or "", "shift": shift or "",
            "type": vtype, "norm_value": norm_val, "fact_value": fact_val,
            "severity": severity, "recommendation": recommendation}


def _warnings_only(items):
    for item in items:
        item["severity"] = WARNING
    return items


def check_driver_roster(con, driver, entries, norms, period_from, period_to):
    """Проверка графика одного водителя. Все нарушения Приказа 424 возвращаются как предупреждения."""
    V = []
    routes = {r["id"]: r["number"] for r in con.execute("SELECT id, number FROM routes")}
    work = [e for e in entries if e["status"] == "работа" and e["start_time"] and e["end_time"]]
    summed = int(norms.get("summed_accounting", 1))
    max_shift = float(norms["max_shift_hours_summed" if summed else "max_shift_hours"])
    base_shift = float(norms["max_shift_hours"])
    max_day = float(norms["max_driving_day"])
    max_ext = float(norms["max_driving_day_ext"])

    driving_by_week = {}
    driving_extensions = {}

    # 1. Продолжительность каждой смены, управление и перерывы
    for e in work:
        rt = routes.get(e["route_id"], "")
        h = e["hours"] or work_hours(e["start_time"], e["end_time"])
        if h > max_shift:
            V.append(violation(driver, e["date"], "Превышение продолжительности смены",
                     f"не более {max_shift} ч", f"{h} ч", WARNING,
                     "Проверьте график: при суммированном учёте это предупреждение, работа не блокируется",
                     rt, e["output_number"], e["shift_number"]))
        elif h > base_shift:
            V.append(violation(driver, e["date"], "Смена длиннее 10 ч (допустимо при суммированном учёте до 12 ч)",
                     f"{base_shift} ч", f"{h} ч", WARNING,
                     "Убедитесь, что применяется суммированный учёт рабочего времени",
                     rt, e["output_number"], e["shift_number"]))

        driving = _driving_hours_for_entry(con, e, norms)
        d = datetime.date.fromisoformat(e["date"])
        week = (d - datetime.timedelta(days=d.weekday())).isoformat()
        driving_by_week[week] = driving_by_week.get(week, 0.0) + driving
        if driving > max_day:
            driving_extensions[week] = driving_extensions.get(week, 0) + 1
        if driving > max_ext:
            V.append(violation(driver, e["date"], "Превышение времени управления автобусом",
                     f"не более {norms['max_driving_day']} ч (до {norms['max_driving_day_ext']} ч не чаще {norms['max_driving_ext_per_week']} раз в неделю)",
                     f"{round(driving,1)} ч", WARNING, "Сократить время на линии или добавить подмену",
                     rt, e["output_number"], e["shift_number"]))
        elif driving > max_day:
            V.append(violation(driver, e["date"], "Время управления более 9 ч",
                     f"{norms['max_driving_day']} ч", f"{round(driving,1)} ч", WARNING,
                     "Допустимо не более 2 раз в неделю; при превышении программа только предупреждает",
                     rt, e["output_number"], e["shift_number"]))

        dur = shift_minutes(e["start_time"], e["end_time"])
        eff_break = e.get("break_min") or lunch_minutes(dur)
        if driving > float(norms["driving_before_break_h"]) and eff_break < int(norms["break_min_minutes"]):
            V.append(violation(driver, e["date"], "Не обеспечен перерыв после управления",
                     f"перерыв не менее {norms['break_min_minutes']} мин после {norms['driving_before_break_h']} ч управления",
                     f"управление {round(driving,1)} ч, обед/разрывы {eff_break} мин", WARNING,
                     "Проверьте наличие обеда, разрыва или технологического перерыва",
                     rt, e["output_number"], e["shift_number"]))

    for week, count in driving_extensions.items():
        limit = int(norms.get("max_driving_ext_per_week") or 0)
        if limit and count > limit:
            V.append(violation(driver, week, "Слишком много продлений времени управления за неделю",
                     f"не более {limit} раз", f"{count} раз", WARNING,
                     "Перераспределить длинные смены внутри недели"))

    # 2. Междусменный отдых
    for a, b in zip(work, work[1:]):
        end_a = _dt(a["date"], a["end_time"])
        if tmin(a["end_time"]) <= tmin(a["start_time"]): end_a += datetime.timedelta(days=1)
        start_b = _dt(b["date"], b["start_time"])
        rest_h = (start_b - end_a).total_seconds() / 3600.0
        if rest_h <= 0: continue
        prev_h = a["hours"] or work_hours(a["start_time"], a["end_time"])
        required = float(norms["min_intershift_rest_summed_h"]) if summed else 2.0 * prev_h
        if rest_h < required:
            V.append(violation(driver, b["date"], "Недостаточный междусменный отдых",
                     f"не менее {round(required,1)} ч", f"{round(rest_h,1)} ч", WARNING,
                     "Сдвинуть начало смены или назначить выходной",
                     routes.get(b["route_id"], ""), b["output_number"], b["shift_number"]))

    # 3. Непрерывные рабочие дни
    maxrun, run, prev_date = int(norms["max_consecutive_workdays"]), 0, None
    for e in work:
        d = datetime.date.fromisoformat(e["date"])
        run = run + 1 if (prev_date and (d - prev_date).days == 1) else 1
        prev_date = d
        if run > maxrun:
            V.append(violation(driver, e["date"], "Слишком много рабочих дней подряд",
                     f"не более {maxrun}", f"{run}", WARNING,
                     "Предоставить еженедельный отдых не менее 42 ч"))

    # 4. Еженедельный отдых (42 ч в каждой календарной неделе)
    if work:
        d0 = datetime.date.fromisoformat(period_from)
        d1 = datetime.date.fromisoformat(period_to)
        wk = d0 - datetime.timedelta(days=d0.weekday())
        intervals = []
        for e in work:
            s = _dt(e["date"], e["start_time"])
            en = _dt(e["date"], e["end_time"])
            if en <= s: en += datetime.timedelta(days=1)
            intervals.append((s, en))
        intervals.sort()
        while wk <= d1:
            we = wk + datetime.timedelta(days=7)
            wk_start = datetime.datetime.combine(wk, datetime.time())
            wk_end = datetime.datetime.combine(we, datetime.time())
            pts = [(max(s, wk_start - datetime.timedelta(hours=48)), min(en, wk_end + datetime.timedelta(hours=48)))
                   for s, en in intervals if en > wk_start - datetime.timedelta(hours=48) and s < wk_end + datetime.timedelta(hours=48)]
            if pts:
                gaps, cur = [], wk_start - datetime.timedelta(hours=48)
                for s, en in pts:
                    if s > cur: gaps.append((cur, s))
                    cur = max(cur, en)
                gaps.append((cur, wk_end + datetime.timedelta(hours=48)))
                best = max((min(g1, wk_end) - max(g0, wk_start)).total_seconds() / 3600.0
                           for g0, g1 in gaps if min(g1, wk_end) > max(g0, wk_start)) if gaps else 0
                full_in_week = any((g1 - g0).total_seconds() / 3600.0 >= float(norms["weekly_rest_h"])
                                   and g0 < wk_end and g1 > wk_start for g0, g1 in gaps)
                if not full_in_week and we <= d1 + datetime.timedelta(days=1):
                    V.append(violation(driver, wk.isoformat(), "Не обеспечен еженедельный отдых",
                             f"не менее {norms['weekly_rest_h']} ч подряд",
                             f"макс. непрерывный отдых ~{round(best,1)} ч", WARNING,
                             f"Назначить выходной в неделе с {wk.strftime('%d.%m')}"))
            wk = we

    # 5. Недельное и двухнедельное время управления
    weeks = sorted(driving_by_week)
    for w in weeks:
        if driving_by_week[w] > float(norms["max_driving_week"]):
            V.append(violation(driver, w, "Превышение недельного времени управления",
                     f"не более {norms['max_driving_week']} ч", f"{round(driving_by_week[w],1)} ч", WARNING,
                     "Перераспределить смены внутри недели"))
    for w1, w2 in zip(weeks, weeks[1:]):
        if (datetime.date.fromisoformat(w2) - datetime.date.fromisoformat(w1)).days == 7:
            s = driving_by_week[w1] + driving_by_week[w2]
            if s > float(norms["max_driving_2weeks"]):
                V.append(violation(driver, w2, "Превышение времени управления за 2 недели",
                         f"не более {norms['max_driving_2weeks']} ч", f"{round(s,1)} ч", WARNING,
                         "Сократить количество смен"))

    # 6. Переработка за настраиваемый учётный период
    total = sum(e["hours"] or work_hours(e["start_time"], e["end_time"]) for e in work)
    d0 = datetime.date.fromisoformat(period_from)
    d1 = datetime.date.fromisoformat(period_to)
    acc_months = int(norms.get("accounting_period_months") or 1)
    if _is_full_accounting_period(d0, d1, acc_months):
        norm_h = period_norm_hours(con, d0, d1, float(norms["week_norm_hours"]))
        if total > norm_h:
            over = round(total - norm_h, 1)
            V.append(violation(driver, period_to, "Переработка сверх нормы учётного периода",
                     f"{norm_h} ч за {acc_months} мес", f"{round(total,1)} ч (+{over} ч)", WARNING,
                     "Проверить суммированный учёт; сверхурочные не более 120 ч в год"))
    return _warnings_only(V)


def check_period(con, period_from, period_to, driver_id=None):
    """Проверка графика всех (или одного) водителей за период."""
    norms = db.get_active_norms(con, period_from)
    q = "SELECT * FROM drivers WHERE status != 'уволен'"
    args = []
    if driver_id:
        q += " AND id=?"; args.append(driver_id)
    result = []
    for drv in con.execute(q, args).fetchall():
        entries = db.rows(con.execute(
            "SELECT * FROM roster WHERE driver_id=? AND date>=? AND date<=? ORDER BY date",
            (drv["id"], period_from, period_to)))
        result += check_driver_roster(con, dict(drv), entries, norms, period_from, period_to)
    result.sort(key=lambda v: (v["date"], v["fio"], v["type"]))
    return result
