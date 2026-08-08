# -*- coding: utf-8 -*-
"""Бизнес-логика модуля выручки: тарифы, листы выручки, сверка."""
import datetime

from . import db


class RevenueError(ValueError):
    """Нарушение правил модуля выручки."""


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _row_to_dict(row):
    return dict(row) if row is not None else None


def _check_iso_date(value, label):
    try:
        if datetime.date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except (TypeError, ValueError):
        raise RevenueError(f"{label} должна иметь формат YYYY-MM-DD") from None


def list_fare_types(con, *, include_inactive=False):
    sql = "SELECT id, code, name, unit, active FROM fare_types"
    if not include_inactive:
        sql += " WHERE active=1"
    sql += " ORDER BY name"
    return [dict(r) for r in con.execute(sql)]


def upsert_fare_type(con, *, code, name, unit, fare_type_id=None):
    if not str(code or "").strip() or not str(name or "").strip():
        raise RevenueError("Код и наименование вида билета обязательны")
    if fare_type_id is None:
        cur = con.execute(
            "INSERT INTO fare_types(code, name, unit, active) VALUES(?,?,?,1)",
            (code.strip(), name.strip(), unit or "поездка"),
        )
        return cur.lastrowid
    con.execute(
        "UPDATE fare_types SET code=?, name=?, unit=? WHERE id=?",
        (code.strip(), name.strip(), unit or "поездка", fare_type_id),
    )
    return fare_type_id


def add_tariff(con, *, fare_type_id, valid_from, price, valid_to=None, comment=None):
    if con.execute(
        "SELECT 1 FROM fare_types WHERE id=?", (fare_type_id,)
    ).fetchone() is None:
        raise RevenueError("Вид билета не найден")
    _check_iso_date(valid_from, "Дата начала действия")
    if valid_to is not None:
        _check_iso_date(valid_to, "Дата окончания действия")
        if valid_to < valid_from:
            raise RevenueError("Дата окончания раньше даты начала")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
        raise RevenueError("Цена должна быть неотрицательным числом")
    cur = con.execute(
        "INSERT INTO fare_tariffs(fare_type_id, valid_from, valid_to, price, active, comment) "
        "VALUES(?,?,?,?,1,?)",
        (fare_type_id, valid_from, valid_to, float(price), comment),
    )
    return cur.lastrowid


def active_tariff(con, fare_type_id, on_date):
    row = con.execute(
        """
        SELECT id, fare_type_id, valid_from, valid_to, price
        FROM fare_tariffs
        WHERE fare_type_id=? AND active=1 AND valid_from<=?
          AND (valid_to IS NULL OR valid_to>=?)
        ORDER BY valid_from DESC LIMIT 1
        """,
        (fare_type_id, on_date, on_date),
    ).fetchone()
    return _row_to_dict(row)


def list_tariffs(con, fare_type_id=None):
    sql = (
        "SELECT id, fare_type_id, valid_from, valid_to, price, active, comment "
        "FROM fare_tariffs"
    )
    params = ()
    if fare_type_id is not None:
        sql += " WHERE fare_type_id=?"
        params = (fare_type_id,)
    sql += " ORDER BY fare_type_id, valid_from DESC"
    return [dict(r) for r in con.execute(sql, params)]


def _next_sheet_number(con):
    row = con.execute("SELECT MAX(number) AS n FROM revenue_sheets").fetchone()
    return int(row["n"] or 0) + 1


def create_sheet_from_waybill(con, waybill_id, *, conductor_id=None, created_by):
    wb = con.execute(
        "SELECT id, date, driver_id, bus_id, route_id FROM waybills WHERE id=?",
        (waybill_id,),
    ).fetchone()
    if wb is None:
        raise RevenueError("Путевой лист не найден")
    existing = con.execute(
        "SELECT 1 FROM revenue_sheets WHERE waybill_id=? AND status<>'аннулирован'",
        (waybill_id,),
    ).fetchone()
    if existing is not None:
        raise RevenueError("Для этого путевого листа уже есть лист выручки")
    number = _next_sheet_number(con)
    cur = con.execute(
        """
        INSERT INTO revenue_sheets(
          number, waybill_id, date, driver_id, bus_id, route_id, conductor_id,
          expected_amount, submitted_amount, difference, status, created_by, created_at
        ) VALUES(?,?,?,?,?,?,?,0,0,0,'черновик',?,?)
        """,
        (
            number, wb["id"], wb["date"], wb["driver_id"], wb["bus_id"],
            wb["route_id"], conductor_id, created_by, _now(),
        ),
    )
    return cur.lastrowid


def get_sheet(con, sheet_id):
    row = con.execute("SELECT * FROM revenue_sheets WHERE id=?", (sheet_id,)).fetchone()
    if row is None:
        raise RevenueError("Лист выручки не найден")
    sheet = dict(row)
    sheet["lines"] = [
        dict(r)
        for r in con.execute(
            "SELECT id, fare_type_id, tickets_count, unit_price, amount "
            "FROM revenue_lines WHERE sheet_id=? ORDER BY id",
            (sheet_id,),
        )
    ]
    return sheet


def set_sheet_lines(con, sheet_id, lines):
    sheet = con.execute(
        "SELECT id, date, status FROM revenue_sheets WHERE id=?", (sheet_id,)
    ).fetchone()
    if sheet is None:
        raise RevenueError("Лист выручки не найден")
    if sheet["status"] != "черновик":
        raise RevenueError("Строки можно менять только в черновике")
    seen = set()
    prepared = []
    for fare_type_id, count in lines:
        if fare_type_id in seen:
            raise RevenueError("Вид билета указан дважды")
        seen.add(fare_type_id)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise RevenueError("Количество билетов должно быть целым ≥ 0")
        tariff = active_tariff(con, fare_type_id, sheet["date"])
        if tariff is None:
            raise RevenueError("Нет тарифа на дату смены для вида билета")
        amount = round(tariff["price"] * count, 2)
        prepared.append((fare_type_id, count, tariff["price"], amount))
    con.execute("DELETE FROM revenue_lines WHERE sheet_id=?", (sheet_id,))
    con.executemany(
        "INSERT INTO revenue_lines(sheet_id, fare_type_id, tickets_count, unit_price, amount) "
        "VALUES(?,?,?,?,?)",
        [(sheet_id, *row) for row in prepared],
    )
    expected = round(sum(row[3] for row in prepared), 2)
    con.execute(
        "UPDATE revenue_sheets SET expected_amount=? WHERE id=?",
        (expected, sheet_id),
    )
    return get_sheet(con, sheet_id)
