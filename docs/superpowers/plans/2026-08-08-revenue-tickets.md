# Tickets & Revenue Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a passenger-transport revenue module — versioned fare tariffs, per-shift revenue sheets tied to waybills, cash reconciliation, and Excel reporting — as a new "Выручка" tab.

**Architecture:** Four new SQLite tables in the existing `SCHEMA`. Business logic lives in `app/revenue_service.py` (no FastAPI), exposed by `app/api_revenue.py`, with Excel in `app/revenue_reports.py`. The frontend adds `static/revenue.js` registering `VIEWS.revenue`. Tariffs mirror the versioned-by-date `norms` pattern; revenue sheets denormalize driver/bus/route/date from the linked waybill.

**Tech Stack:** Python 3, FastAPI, SQLite, openpyxl, pytest, httpx (TestClient), Node.js (UI behavior test).

## Global Constraints

- Domain errors are subclasses of `ValueError`; the API layer maps them to 400, "not found" to 404, missing permission to 403 via `require_write(user, "revenue")`.
- Service functions never `commit`; the API layer commits on success and writes `db.audit(...)`.
- All user-facing messages are Russian.
- `db.audit` signature: `audit(con, username, action, obj_type, obj_id, old=None, new=None, ip="", comment="")`.
- Money is `REAL` rubles; ticket counts are non-negative integers.
- Follow existing patterns: `router = APIRouter(prefix="/api")`, `user=Depends(current_user)`.
- Run tests with `python -m pytest -q`.

---

## File map

- `app/db.py` — add 4 `CREATE TABLE` blocks to `SCHEMA`; add indexes in `init_db`.
- `app/auth.py` — add `"revenue"` to `WRITE_ACCESS` for `бухгалтер` and `диспетчер`.
- `app/revenue_service.py` — fare types, tariffs, sheets, lines, status machine (create).
- `app/api_revenue.py` — REST endpoints + audit (create).
- `app/revenue_reports.py` — Excel report builder + response (create).
- `app/main.py` — `include_router(revenue_router)`.
- `app/seed.py` — demo fare types, tariffs, and revenue sheets.
- `static/revenue.js` — `VIEWS.revenue` tab (create).
- `static/app.js` — add `["revenue", "Выручка"]` to `NAV`; bump `app.js` asset version.
- `static/index.html` — `<script src="/static/revenue.js?v=1.0">`; bump `app.js` version.
- `static/styles.css` — revenue tab styles (responsive + print).
- `tests/test_revenue_service.py`, `tests/test_revenue_api.py`, `tests/test_revenue_reports.py`, `tests/test_revenue_frontend.py`, `tests/js/revenue_recalc_behavior.js` — tests (create).

---

### Task 1: Schema, indexes, and permission matrix

**Files:**
- Modify: `app/db.py` (SCHEMA string; `init_db`)
- Modify: `app/auth.py` (`WRITE_ACCESS`)
- Create: `tests/test_revenue_service.py`

**Interfaces:**
- Produces: tables `fare_types`, `fare_tariffs`, `revenue_sheets`, `revenue_lines`; indexes `idx_revenue_sheets_waybill`, `idx_revenue_sheet_active`, `idx_fare_tariffs_type_from`.

- [ ] **Step 1: Write the failing schema test**

```python
# tests/test_revenue_service.py
import pytest


def _open_db(tmp_path):
    from app import db
    db.DB_PATH = str(tmp_path / "revenue.db")
    db.init_db()
    return db.connect()


def test_revenue_tables_and_indexes_exist(tmp_path):
    con = _open_db(tmp_path)
    try:
        tables = {
            row["name"]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indexes = {
            row["name"]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    finally:
        con.close()
    assert {"fare_types", "fare_tariffs", "revenue_sheets", "revenue_lines"} <= tables
    assert "idx_revenue_sheets_waybill" in indexes
    assert "idx_revenue_sheet_active" in indexes


def test_write_access_grants_revenue_to_accountant_and_dispatcher():
    from app.auth import WRITE_ACCESS
    assert "revenue" in WRITE_ACCESS["бухгалтер"]
    assert "revenue" in WRITE_ACCESS["диспетчер"]
```

- [ ] **Step 2: Run it and verify failure**

Run: `python -m pytest tests/test_revenue_service.py -q`
Expected: FAIL — tables/index/WRITE_ACCESS missing.

- [ ] **Step 3: Add the four tables to `SCHEMA` in `app/db.py`**

Insert before the closing `"""` of the `SCHEMA` string (after the `notifications` table):

```sql
CREATE TABLE IF NOT EXISTS fare_types(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  unit TEXT DEFAULT 'поездка', active INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS fare_tariffs(
  id INTEGER PRIMARY KEY, fare_type_id INTEGER NOT NULL,
  valid_from TEXT NOT NULL, valid_to TEXT, price REAL NOT NULL,
  active INTEGER DEFAULT 1, comment TEXT,
  FOREIGN KEY(fare_type_id) REFERENCES fare_types(id) ON DELETE CASCADE);

CREATE TABLE IF NOT EXISTS revenue_sheets(
  id INTEGER PRIMARY KEY, number INTEGER UNIQUE NOT NULL,
  waybill_id INTEGER NOT NULL, date TEXT NOT NULL,
  driver_id INTEGER, bus_id INTEGER, route_id INTEGER, conductor_id INTEGER,
  expected_amount REAL DEFAULT 0, submitted_amount REAL DEFAULT 0,
  difference REAL DEFAULT 0, status TEXT DEFAULT 'черновик',
  created_by TEXT, created_at TEXT, submitted_at TEXT,
  reconciled_by TEXT, reconciled_at TEXT, cancel_reason TEXT, comment TEXT);

CREATE TABLE IF NOT EXISTS revenue_lines(
  id INTEGER PRIMARY KEY, sheet_id INTEGER NOT NULL,
  fare_type_id INTEGER NOT NULL, tickets_count INTEGER NOT NULL DEFAULT 0,
  unit_price REAL NOT NULL, amount REAL NOT NULL,
  UNIQUE(sheet_id, fare_type_id),
  FOREIGN KEY(sheet_id) REFERENCES revenue_sheets(id) ON DELETE CASCADE);
```

- [ ] **Step 4: Add indexes in `init_db`**

In `app/db.py`, `init_db`, next to the existing
`CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_source_key ...` line, add:

```python
con.execute("CREATE INDEX IF NOT EXISTS idx_revenue_sheets_waybill ON revenue_sheets(waybill_id)")
con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_revenue_sheet_active ON revenue_sheets(waybill_id) WHERE status<>'аннулирован'")
con.execute("CREATE INDEX IF NOT EXISTS idx_fare_tariffs_type_from ON fare_tariffs(fare_type_id, valid_from)")
```

- [ ] **Step 5: Grant permission in `app/auth.py`**

Add `"revenue"` to both sets:

```python
    "диспетчер": {"orders", "waybills", "roster", "summary", "revenue"},
    "бухгалтер": {"export1c", "timesheet", "revenue"},
```

- [ ] **Step 6: Run tests, verify pass, commit**

Run: `python -m pytest tests/test_revenue_service.py -q`
Expected: PASS.

```bash
git add app/db.py app/auth.py tests/test_revenue_service.py
git commit -m "feat(revenue): add schema, indexes, permissions"
```

---

### Task 2: Fare types and versioned tariffs

**Files:**
- Create: `app/revenue_service.py`
- Modify: `tests/test_revenue_service.py`

**Interfaces:**
- Produces:
  - `class RevenueError(ValueError)`
  - `list_fare_types(con, *, include_inactive=False) -> list[dict]`
  - `upsert_fare_type(con, *, code, name, unit, fare_type_id=None) -> int`
  - `add_tariff(con, *, fare_type_id, valid_from, price, valid_to=None, comment=None) -> int`
  - `active_tariff(con, fare_type_id, on_date) -> dict | None` (keys: `id, fare_type_id, valid_from, valid_to, price`)
  - `list_tariffs(con, fare_type_id=None) -> list[dict]`

- [ ] **Step 1: Write failing tariff tests**

```python
# append to tests/test_revenue_service.py
from app import revenue_service as rs


def test_active_tariff_picks_version_by_date(tmp_path):
    con = _open_db(tmp_path)
    try:
        ft = rs.upsert_fare_type(con, code="single", name="Разовый", unit="поездка")
        rs.add_tariff(con, fare_type_id=ft, valid_from="2026-01-01", price=30.0)
        rs.add_tariff(con, fare_type_id=ft, valid_from="2026-06-01", price=35.0)
        con.commit()
        assert rs.active_tariff(con, ft, "2026-03-01")["price"] == 30.0
        assert rs.active_tariff(con, ft, "2026-06-01")["price"] == 35.0
        assert rs.active_tariff(con, ft, "2025-12-31") is None
    finally:
        con.close()


def test_active_tariff_respects_valid_to(tmp_path):
    con = _open_db(tmp_path)
    try:
        ft = rs.upsert_fare_type(con, code="promo", name="Акция", unit="поездка")
        rs.add_tariff(
            con, fare_type_id=ft, valid_from="2026-01-01",
            valid_to="2026-01-31", price=20.0,
        )
        con.commit()
        assert rs.active_tariff(con, ft, "2026-01-15")["price"] == 20.0
        assert rs.active_tariff(con, ft, "2026-02-01") is None
    finally:
        con.close()


def test_add_tariff_rejects_negative_price(tmp_path):
    con = _open_db(tmp_path)
    try:
        ft = rs.upsert_fare_type(con, code="x", name="X", unit="поездка")
        with pytest.raises(rs.RevenueError):
            rs.add_tariff(con, fare_type_id=ft, valid_from="2026-01-01", price=-1.0)
    finally:
        con.close()
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/test_revenue_service.py -q`
Expected: FAIL — `ModuleNotFoundError: app.revenue_service`.

- [ ] **Step 3: Implement fare types and tariffs**

```python
# app/revenue_service.py
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


def list_fare_types(con, *, include_inactive=False):
    sql = "SELECT id, code, name, unit, active FROM fare_types"
    if not include_inactive:
        sql += " WHERE active=1"
    sql += " ORDER BY name"
    return [dict(r) for r in con.execute(sql)]


def upsert_fare_type(con, *, code, name, unit, fare_type_id=None):
    if not str(code).strip() or not str(name).strip():
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
    if not isinstance(price, (int, float)) or isinstance(price, bool) or price < 0:
        raise RevenueError("Цена должна быть неотрицательным числом")
    cur = con.execute(
        "INSERT INTO fare_tariffs(fare_type_id, valid_from, valid_to, price, active, comment) "
        "VALUES(?,?,?,?,1,?)",
        (fare_type_id, valid_from, valid_to, float(price), comment),
    )
    return cur.lastrowid


def _check_iso_date(value, label):
    try:
        if datetime.date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except (TypeError, ValueError):
        raise RevenueError(f"{label} должна иметь формат YYYY-MM-DD") from None


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
```

- [ ] **Step 4: Run and verify pass, commit**

Run: `python -m pytest tests/test_revenue_service.py -q`
Expected: PASS.

```bash
git add app/revenue_service.py tests/test_revenue_service.py
git commit -m "feat(revenue): versioned fare tariffs"
```

---

### Task 3: Revenue sheets from waybills and line recalculation

**Files:**
- Modify: `app/revenue_service.py`
- Modify: `tests/test_revenue_service.py`

**Interfaces:**
- Consumes: `active_tariff`, `RevenueError`.
- Produces:
  - `create_sheet_from_waybill(con, waybill_id, *, conductor_id=None, created_by) -> int`
  - `get_sheet(con, sheet_id) -> dict` (includes `"lines": list[dict]`)
  - `set_sheet_lines(con, sheet_id, lines) -> dict` where `lines` is
    `list[tuple[int, int]]` of `(fare_type_id, tickets_count)`; returns updated sheet.
  - `_next_sheet_number(con) -> int`

- [ ] **Step 1: Write failing sheet tests**

```python
# append to tests/test_revenue_service.py
def _seed_waybill(con, *, date="2026-08-07", number=5001):
    driver_id = con.execute(
        "INSERT INTO drivers(tab_number, fio) VALUES(?,?)", ("Т1", "Иванов")
    ).lastrowid
    bus_id = con.execute(
        "INSERT INTO buses(garage_number, plate) VALUES(?,?)", ("Г1", "A001")
    ).lastrowid
    route_id = con.execute(
        "INSERT INTO routes(number, name) VALUES(?,?)", ("42", "Центр")
    ).lastrowid
    con.execute(
        "INSERT INTO waybills(number, date, driver_id, bus_id, route_id, status) "
        "VALUES(?,?,?,?,?,?)",
        (number, date, driver_id, bus_id, route_id, "оформлен"),
    )
    wid = con.execute("SELECT id FROM waybills WHERE number=?", (number,)).fetchone()["id"]
    return wid, route_id


def _fare(con, code, price, valid_from="2026-01-01"):
    ft = rs.upsert_fare_type(con, code=code, name=code, unit="поездка")
    rs.add_tariff(con, fare_type_id=ft, valid_from=valid_from, price=price)
    return ft


def test_create_sheet_copies_waybill_fields(tmp_path):
    con = _open_db(tmp_path)
    try:
        wid, route_id = _seed_waybill(con, date="2026-08-07")
        sheet_id = rs.create_sheet_from_waybill(con, wid, created_by="admin")
        con.commit()
        sheet = rs.get_sheet(con, sheet_id)
        assert sheet["date"] == "2026-08-07"
        assert sheet["route_id"] == route_id
        assert sheet["status"] == "черновик"
        assert sheet["number"] >= 1
    finally:
        con.close()


def test_create_sheet_rejects_second_active_sheet(tmp_path):
    con = _open_db(tmp_path)
    try:
        wid, _ = _seed_waybill(con)
        rs.create_sheet_from_waybill(con, wid, created_by="admin")
        con.commit()
        with pytest.raises(rs.RevenueError):
            rs.create_sheet_from_waybill(con, wid, created_by="admin")
    finally:
        con.close()


def test_create_sheet_unknown_waybill(tmp_path):
    con = _open_db(tmp_path)
    try:
        with pytest.raises(rs.RevenueError):
            rs.create_sheet_from_waybill(con, 999999, created_by="admin")
    finally:
        con.close()


def test_set_lines_computes_amounts_from_tariff_on_date(tmp_path):
    con = _open_db(tmp_path)
    try:
        wid, _ = _seed_waybill(con, date="2026-08-07")
        single = _fare(con, "single", 30.0)
        child = _fare(con, "child", 15.0)
        sheet_id = rs.create_sheet_from_waybill(con, wid, created_by="admin")
        sheet = rs.set_sheet_lines(con, sheet_id, [(single, 100), (child, 20)])
        con.commit()
        assert sheet["expected_amount"] == 30.0 * 100 + 15.0 * 20
        amounts = {ln["fare_type_id"]: ln["amount"] for ln in sheet["lines"]}
        assert amounts[single] == 3000.0
    finally:
        con.close()


def test_set_lines_rejects_negative_and_missing_tariff(tmp_path):
    con = _open_db(tmp_path)
    try:
        wid, _ = _seed_waybill(con, date="2026-08-07")
        single = _fare(con, "single", 30.0)
        no_tariff = rs.upsert_fare_type(con, code="none", name="Нет", unit="поездка")
        sheet_id = rs.create_sheet_from_waybill(con, wid, created_by="admin")
        with pytest.raises(rs.RevenueError):
            rs.set_sheet_lines(con, sheet_id, [(single, -1)])
        with pytest.raises(rs.RevenueError):
            rs.set_sheet_lines(con, sheet_id, [(no_tariff, 5)])
    finally:
        con.close()
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/test_revenue_service.py -q`
Expected: FAIL — sheet functions missing.

- [ ] **Step 3: Implement sheet creation and line recalculation**

```python
# add to app/revenue_service.py
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
```

- [ ] **Step 4: Run and verify pass, commit**

Run: `python -m pytest tests/test_revenue_service.py -q`
Expected: PASS.

```bash
git add app/revenue_service.py tests/test_revenue_service.py
git commit -m "feat(revenue): revenue sheets from waybills"
```

---

### Task 4: Cash reconciliation and status machine

**Files:**
- Modify: `app/revenue_service.py`
- Modify: `tests/test_revenue_service.py`

**Interfaces:**
- Produces:
  - `submit_sheet(con, sheet_id, submitted_amount, *, user) -> dict`
  - `reconcile_sheet(con, sheet_id, *, user) -> dict`
  - `cancel_sheet(con, sheet_id, reason, *, user) -> dict`
  - `list_sheets(con, *, date_from=None, date_to=None, route_id=None, status=None) -> list[dict]`

- [ ] **Step 1: Write failing status/reconciliation tests**

```python
# append to tests/test_revenue_service.py
def _draft_with_lines(con):
    wid, _ = _seed_waybill(con, date="2026-08-07")
    single = _fare(con, "single", 30.0)
    sheet_id = rs.create_sheet_from_waybill(con, wid, created_by="admin")
    rs.set_sheet_lines(con, sheet_id, [(single, 100)])  # expected 3000
    return sheet_id


def test_submit_computes_difference_and_advances_status(tmp_path):
    con = _open_db(tmp_path)
    try:
        sheet_id = _draft_with_lines(con)
        sheet = rs.submit_sheet(con, sheet_id, 2950.0, user="cashier")
        con.commit()
        assert sheet["status"] == "сдан"
        assert sheet["submitted_amount"] == 2950.0
        assert sheet["difference"] == -50.0
    finally:
        con.close()


def test_reconcile_requires_submitted(tmp_path):
    con = _open_db(tmp_path)
    try:
        sheet_id = _draft_with_lines(con)
        with pytest.raises(rs.RevenueError):
            rs.reconcile_sheet(con, sheet_id, user="buh")
        rs.submit_sheet(con, sheet_id, 3000.0, user="cashier")
        sheet = rs.reconcile_sheet(con, sheet_id, user="buh")
        con.commit()
        assert sheet["status"] == "сверен"
    finally:
        con.close()


def test_cancel_sets_status_and_reason(tmp_path):
    con = _open_db(tmp_path)
    try:
        sheet_id = _draft_with_lines(con)
        sheet = rs.cancel_sheet(con, sheet_id, "ошибка", user="admin")
        con.commit()
        assert sheet["status"] == "аннулирован"
        assert sheet["cancel_reason"] == "ошибка"
    finally:
        con.close()


def test_lines_locked_after_submit(tmp_path):
    con = _open_db(tmp_path)
    try:
        sheet_id = _draft_with_lines(con)
        single = con.execute("SELECT fare_type_id FROM revenue_lines WHERE sheet_id=?", (sheet_id,)).fetchone()["fare_type_id"]
        rs.submit_sheet(con, sheet_id, 3000.0, user="cashier")
        with pytest.raises(rs.RevenueError):
            rs.set_sheet_lines(con, sheet_id, [(single, 50)])
    finally:
        con.close()
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/test_revenue_service.py -q`
Expected: FAIL — status functions missing.

- [ ] **Step 3: Implement status machine and listing**

```python
# add to app/revenue_service.py
def _require_status(con, sheet_id, expected_statuses):
    row = con.execute(
        "SELECT id, status FROM revenue_sheets WHERE id=?", (sheet_id,)
    ).fetchone()
    if row is None:
        raise RevenueError("Лист выручки не найден")
    if row["status"] not in expected_statuses:
        raise RevenueError(f"Недопустимый статус листа: {row['status']}")
    return row


def submit_sheet(con, sheet_id, submitted_amount, *, user):
    _require_status(con, sheet_id, {"черновик"})
    if (
        isinstance(submitted_amount, bool)
        or not isinstance(submitted_amount, (int, float))
        or submitted_amount < 0
    ):
        raise RevenueError("Сумма сдачи должна быть неотрицательным числом")
    expected = con.execute(
        "SELECT expected_amount FROM revenue_sheets WHERE id=?", (sheet_id,)
    ).fetchone()["expected_amount"]
    difference = round(float(submitted_amount) - float(expected), 2)
    con.execute(
        "UPDATE revenue_sheets SET submitted_amount=?, difference=?, status='сдан', "
        "submitted_at=? WHERE id=?",
        (float(submitted_amount), difference, _now(), sheet_id),
    )
    return get_sheet(con, sheet_id)


def reconcile_sheet(con, sheet_id, *, user):
    _require_status(con, sheet_id, {"сдан"})
    con.execute(
        "UPDATE revenue_sheets SET status='сверен', reconciled_by=?, reconciled_at=? WHERE id=?",
        (user, _now(), sheet_id),
    )
    return get_sheet(con, sheet_id)


def cancel_sheet(con, sheet_id, reason, *, user):
    _require_status(con, sheet_id, {"черновик", "сдан", "сверен"})
    if not str(reason or "").strip():
        raise RevenueError("Укажите причину аннулирования")
    con.execute(
        "UPDATE revenue_sheets SET status='аннулирован', cancel_reason=? WHERE id=?",
        (reason.strip(), sheet_id),
    )
    return get_sheet(con, sheet_id)


def list_sheets(con, *, date_from=None, date_to=None, route_id=None, status=None):
    clauses = []
    params = []
    if date_from:
        clauses.append("date>=?"); params.append(date_from)
    if date_to:
        clauses.append("date<=?"); params.append(date_to)
    if route_id:
        clauses.append("route_id=?"); params.append(route_id)
    if status:
        clauses.append("status=?"); params.append(status)
    sql = "SELECT * FROM revenue_sheets"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY date DESC, number DESC"
    return [dict(r) for r in con.execute(sql, params)]
```

- [ ] **Step 4: Run and verify pass, commit**

Run: `python -m pytest tests/test_revenue_service.py -q`
Expected: PASS.

```bash
git add app/revenue_service.py tests/test_revenue_service.py
git commit -m "feat(revenue): cash reconciliation and status machine"
```

---

### Task 5: REST API and permissions

**Files:**
- Create: `app/api_revenue.py`
- Modify: `app/main.py`
- Create: `tests/test_revenue_api.py`

**Interfaces:**
- Consumes: all `revenue_service` functions; `current_user`, `require_write`.
- Produces router `revenue_router` mounted at `/api/revenue`.

- [ ] **Step 1: Write failing API tests**

```python
# tests/test_revenue_api.py
from fastapi.testclient import TestClient


def _client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app
    db.DB_PATH = str(tmp_path / "revenue-api.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
    finally:
        con.close()
    client = TestClient(app)
    token = client.post(
        "/api/login", json={"username": "admin", "password": "admin"}
    ).json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _seed_waybill(number=7001, date="2026-08-07"):
    import app.db as db
    con = db.connect()
    try:
        driver_id = con.execute("INSERT INTO drivers(tab_number, fio) VALUES(?,?)", ("Т9", "Петров")).lastrowid
        bus_id = con.execute("INSERT INTO buses(garage_number, plate) VALUES(?,?)", ("Г9", "B009")).lastrowid
        route_id = con.execute("INSERT INTO routes(number, name) VALUES(?,?)", ("9", "Депо")).lastrowid
        con.execute(
            "INSERT INTO waybills(number, date, driver_id, bus_id, route_id, status) VALUES(?,?,?,?,?,?)",
            (number, date, driver_id, bus_id, route_id, "оформлен"),
        )
        wid = con.execute("SELECT id FROM waybills WHERE number=?", (number,)).fetchone()["id"]
        con.commit()
        return wid, route_id
    finally:
        con.close()


def test_revenue_flow_end_to_end(tmp_path):
    client = _client(tmp_path)
    wid, _ = _seed_waybill()
    ft = client.post("/api/revenue/fare-types", json={"code": "single", "name": "Разовый", "unit": "поездка"}).json()
    client.post("/api/revenue/tariffs", json={"fare_type_id": ft["id"], "valid_from": "2026-01-01", "price": 30.0})
    sheet = client.post("/api/revenue/sheets", json={"waybill_id": wid}).json()
    lined = client.put(f"/api/revenue/sheets/{sheet['id']}/lines", json={"lines": [{"fare_type_id": ft["id"], "tickets_count": 100}]}).json()
    assert lined["expected_amount"] == 3000.0
    submitted = client.post(f"/api/revenue/sheets/{sheet['id']}/submit", json={"submitted_amount": 2980.0}).json()
    assert submitted["difference"] == -20.0
    reconciled = client.post(f"/api/revenue/sheets/{sheet['id']}/reconcile", json={}).json()
    assert reconciled["status"] == "сверен"


def test_sheet_unknown_waybill_is_400(tmp_path):
    client = _client(tmp_path)
    response = client.post("/api/revenue/sheets", json={"waybill_id": 999999})
    assert response.status_code == 400


def test_revenue_requires_authentication(tmp_path):
    client = _client(tmp_path)
    client.headers.pop("Authorization", None)
    assert client.get("/api/revenue/fare-types").status_code in (401, 403)
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/test_revenue_api.py -q`
Expected: FAIL — endpoints absent (404).

- [ ] **Step 3: Implement the API router**

```python
# app/api_revenue.py
# -*- coding: utf-8 -*-
"""API модуля выручки."""
from fastapi import APIRouter, Body, Depends, HTTPException

from . import db
from .auth import current_user, require_write
from . import revenue_service as rs

router = APIRouter(prefix="/api/revenue")


def _guard(user):
    require_write(user, "revenue")


def _handle(exc):
    if isinstance(exc, rs.RevenueError):
        message = str(exc)
        not_found = message.endswith("не найден") or message.endswith("не найдена")
        raise HTTPException(404 if not_found else 400, message) from exc
    raise exc


@router.get("/fare-types")
def fare_types_list(include_inactive: bool = False, user=Depends(current_user)):
    con = db.connect()
    try:
        return {"items": rs.list_fare_types(con, include_inactive=include_inactive)}
    finally:
        con.close()


@router.post("/fare-types")
def fare_types_upsert(payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    con = db.connect()
    try:
        try:
            ft_id = rs.upsert_fare_type(
                con, code=payload.get("code"), name=payload.get("name"),
                unit=payload.get("unit", "поездка"),
                fare_type_id=payload.get("id"),
            )
            db.audit(con, user["username"], "вид билета", "revenue", ft_id, new=payload)
            con.commit()
            return {
                "id": ft_id, "code": payload.get("code"),
                "name": payload.get("name"), "unit": payload.get("unit", "поездка"),
            }
        except ValueError as exc:
            con.rollback(); _handle(exc)
    finally:
        con.close()


@router.get("/tariffs")
def tariffs_list(fare_type_id: int | None = None, user=Depends(current_user)):
    con = db.connect()
    try:
        return {"items": rs.list_tariffs(con, fare_type_id)}
    finally:
        con.close()


@router.post("/tariffs")
def tariffs_add(payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    con = db.connect()
    try:
        try:
            tid = rs.add_tariff(
                con, fare_type_id=payload.get("fare_type_id"),
                valid_from=payload.get("valid_from"), price=payload.get("price"),
                valid_to=payload.get("valid_to"), comment=payload.get("comment"),
            )
            db.audit(con, user["username"], "тариф", "revenue", tid, new=payload)
            con.commit()
        except ValueError as exc:
            con.rollback(); _handle(exc)
        return {"id": tid}
    finally:
        con.close()


@router.get("/sheets")
def sheets_list(
    date_from: str | None = None, date_to: str | None = None,
    route_id: int | None = None, status: str | None = None,
    user=Depends(current_user),
):
    con = db.connect()
    try:
        return {"items": rs.list_sheets(
            con, date_from=date_from, date_to=date_to,
            route_id=route_id, status=status,
        )}
    finally:
        con.close()


@router.get("/sheets/{sheet_id}")
def sheet_get(sheet_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        try:
            return rs.get_sheet(con, sheet_id)
        except ValueError as exc:
            _handle(exc)
    finally:
        con.close()


@router.post("/sheets")
def sheet_create(payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    con = db.connect()
    try:
        try:
            sid = rs.create_sheet_from_waybill(
                con, payload.get("waybill_id"),
                conductor_id=payload.get("conductor_id"),
                created_by=user["username"],
            )
            db.audit(con, user["username"], "создание листа выручки", "revenue", sid)
            con.commit()
            return rs.get_sheet(con, sid)
        except ValueError as exc:
            con.rollback(); _handle(exc)
    finally:
        con.close()


@router.put("/sheets/{sheet_id}/lines")
def sheet_lines(sheet_id: int, payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    con = db.connect()
    try:
        try:
            lines = [
                (int(item["fare_type_id"]), int(item["tickets_count"]))
                for item in payload.get("lines", [])
            ]
            sheet = rs.set_sheet_lines(con, sheet_id, lines)
            db.audit(con, user["username"], "строки листа выручки", "revenue", sheet_id, new=payload)
            con.commit()
            return sheet
        except ValueError as exc:
            con.rollback(); _handle(exc)
    finally:
        con.close()


def _transition(sheet_id, user, fn):
    con = db.connect()
    try:
        try:
            sheet = fn(con)
            db.audit(con, user["username"], "переход статуса листа выручки", "revenue", sheet_id, new={"status": sheet["status"]})
            con.commit()
            return sheet
        except ValueError as exc:
            con.rollback(); _handle(exc)
    finally:
        con.close()


@router.post("/sheets/{sheet_id}/submit")
def sheet_submit(sheet_id: int, payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    return _transition(sheet_id, user, lambda con: rs.submit_sheet(
        con, sheet_id, payload.get("submitted_amount"), user=user["username"],
    ))


@router.post("/sheets/{sheet_id}/reconcile")
def sheet_reconcile(sheet_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    _guard(user)
    return _transition(sheet_id, user, lambda con: rs.reconcile_sheet(
        con, sheet_id, user=user["username"],
    ))


@router.post("/sheets/{sheet_id}/cancel")
def sheet_cancel(sheet_id: int, payload: dict = Body(...), user=Depends(current_user)):
    _guard(user)
    return _transition(sheet_id, user, lambda con: rs.cancel_sheet(
        con, sheet_id, payload.get("reason"), user=user["username"],
    ))
```

Note: `_handle` always raises, so lines after it (e.g. the return in
`fare_types_upsert`) are only reached on success. Simplify the
`fare_types_upsert` success return to `return {"id": ft_id, "code": payload.get("code"), "name": payload.get("name"), "unit": payload.get("unit", "поездка")}` — replace the awkward comprehension.

- [ ] **Step 4: Register the router in `app/main.py`**

Add the import alongside the other `from .api_* import router as *_router` lines and the include:

```python
from .api_revenue import router as revenue_router
```

```python
app.include_router(revenue_router)
```

- [ ] **Step 5: Run and verify pass, commit**

Run: `python -m pytest tests/test_revenue_api.py -q`
Expected: PASS.

```bash
git add app/api_revenue.py app/main.py tests/test_revenue_api.py
git commit -m "feat(revenue): REST API and permissions"
```

---

### Task 6: Excel report

**Files:**
- Create: `app/revenue_reports.py`
- Modify: `app/api_revenue.py` (report endpoint)
- Create: `tests/test_revenue_reports.py`

**Interfaces:**
- Consumes: `list_sheets`; xlsx helpers from `app.route_document_xlsx`.
- Produces:
  - `build_revenue_report(con, *, date_from, date_to, group_by) -> openpyxl.Workbook`
  - `revenue_report_filename(date_from, date_to, group_by) -> str`
  - endpoint `GET /api/revenue/report.xlsx?date_from&date_to&group_by`

- [ ] **Step 1: Write failing report tests**

```python
# tests/test_revenue_reports.py
import io
from openpyxl import load_workbook


def _open_db(tmp_path):
    from app import db
    db.DB_PATH = str(tmp_path / "revenue-report.db")
    db.init_db()
    return db.connect()


def _seed_sheet(con, route_number, amount, date="2026-08-07"):
    from app import revenue_service as rs
    driver_id = con.execute("INSERT INTO drivers(tab_number, fio) VALUES(?,?)", ("Т", "И")).lastrowid
    bus_id = con.execute("INSERT INTO buses(garage_number, plate) VALUES(?,?)", ("Г", "P")).lastrowid
    route_id = con.execute("INSERT INTO routes(number, name) VALUES(?,?)", (route_number, "R")).lastrowid
    num = con.execute("SELECT COALESCE(MAX(number),0)+1 n FROM waybills").fetchone()["n"]
    con.execute("INSERT INTO waybills(number, date, driver_id, bus_id, route_id, status) VALUES(?,?,?,?,?,?)", (num, date, driver_id, bus_id, route_id, "оформлен"))
    wid = con.execute("SELECT id FROM waybills WHERE number=?", (num,)).fetchone()["id"]
    ft = rs.upsert_fare_type(con, code=f"c{route_number}", name="Разовый", unit="поездка")
    rs.add_tariff(con, fare_type_id=ft, valid_from="2026-01-01", price=amount)
    sid = rs.create_sheet_from_waybill(con, wid, created_by="admin")
    rs.set_sheet_lines(con, sid, [(ft, 1)])
    rs.submit_sheet(con, sid, amount, user="admin")
    con.commit()
    return route_id


def test_report_groups_by_route(tmp_path):
    from app.revenue_reports import build_revenue_report
    con = _open_db(tmp_path)
    try:
        _seed_sheet(con, "10", 100.0)
        _seed_sheet(con, "20", 250.0)
        wb = build_revenue_report(con, date_from="2026-08-01", date_to="2026-08-31", group_by="route")
    finally:
        con.close()
    sheet = wb.active
    values = [c.value for row in sheet.iter_rows() for c in row]
    assert any(v == "ВЫРУЧКА ПО МАРШРУТАМ" for v in values)
    assert 100.0 in values and 250.0 in values
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/test_revenue_reports.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the report builder**

```python
# app/revenue_reports.py
# -*- coding: utf-8 -*-
"""Excel-отчёты модуля выручки."""
from openpyxl import Workbook

from .route_document_xlsx import (
    apply_sheet_setup, write_table_header, write_title_band,
    _xlsx_download_response,
)
from . import revenue_service as rs

_TITLES = {
    "route": ("ВЫРУЧКА ПО МАРШРУТАМ", "route_id", "Маршрут"),
    "driver": ("ВЫРУЧКА ПО ВОДИТЕЛЯМ", "driver_id", "Водитель"),
}


def build_revenue_report(con, *, date_from, date_to, group_by="route"):
    title, key, label = _TITLES.get(group_by, _TITLES["route"])
    sheets = [
        s for s in rs.list_sheets(con, date_from=date_from, date_to=date_to)
        if s["status"] != "аннулирован"
    ]
    totals = {}
    for s in sheets:
        bucket = totals.setdefault(s[key], {"expected": 0.0, "submitted": 0.0})
        bucket["expected"] += s["expected_amount"] or 0.0
        bucket["submitted"] += s["submitted_amount"] or 0.0
    wb = Workbook()
    ws = wb.active
    ws.title = "Выручка"
    apply_sheet_setup(ws)
    write_title_band(ws, 1, title, end_col=4)
    write_table_header(ws, 2, (label, "Ожидаемо, руб.", "Сдано, руб.", "Разница, руб."))
    row = 3
    for ident, bucket in sorted(totals.items(), key=lambda kv: str(kv[0])):
        diff = round(bucket["submitted"] - bucket["expected"], 2)
        ws.cell(row, 1, ident)
        ws.cell(row, 2, round(bucket["expected"], 2))
        ws.cell(row, 3, round(bucket["submitted"], 2))
        ws.cell(row, 4, diff)
        row += 1
    return wb


def revenue_report_filename(date_from, date_to, group_by):
    return f"Выручка_{group_by}_{date_from}_{date_to}.xlsx"
```

- [ ] **Step 4: Add the report endpoint to `app/api_revenue.py`**

```python
from .revenue_reports import build_revenue_report, revenue_report_filename
from .route_document_xlsx import _xlsx_download_response


@router.get("/report.xlsx")
def revenue_report(
    date_from: str, date_to: str, group_by: str = "route",
    user=Depends(current_user),
):
    con = db.connect()
    try:
        wb = build_revenue_report(con, date_from=date_from, date_to=date_to, group_by=group_by)
    finally:
        con.close()
    return _xlsx_download_response(wb, revenue_report_filename(date_from, date_to, group_by))
```

- [ ] **Step 5: Run and verify pass, commit**

Run: `python -m pytest tests/test_revenue_reports.py tests/test_revenue_api.py -q`
Expected: PASS.

```bash
git add app/revenue_reports.py app/api_revenue.py tests/test_revenue_reports.py
git commit -m "feat(revenue): Excel revenue report"
```

---

### Task 7: Frontend tab

**Files:**
- Create: `static/revenue.js`
- Modify: `static/app.js` (`NAV`)
- Modify: `static/index.html` (script tag + asset version)
- Modify: `static/styles.css`
- Create: `tests/test_revenue_frontend.py`
- Create: `tests/js/revenue_recalc_behavior.js`

- [ ] **Step 1: Write failing static-contract tests**

```python
# tests/test_revenue_frontend.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _src(name):
    return (ROOT / "static" / name).read_text(encoding="utf-8")


def test_nav_and_view_registered():
    app = _src("app.js")
    assert '["revenue", "Выручка"]' in app
    revenue = _src("revenue.js")
    assert "VIEWS.revenue" in revenue
    assert "/api/revenue/sheets" in revenue
    assert "/api/revenue/fare-types" in revenue
    assert "revenueRecalcExpected" in revenue


def test_index_loads_revenue_script():
    index = _src("index.html")
    assert "/static/revenue.js?v=1.0" in index


def test_styles_have_revenue_rules():
    styles = _src("styles.css")
    assert ".revenue-tab" in styles
    assert "@media print" in styles
```

- [ ] **Step 2: Write the executable recalculation behavior test**

```javascript
// tests/js/revenue_recalc_behavior.js
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const context = vm.createContext({ console, VIEWS: {} });
context.window = context;
vm.runInContext(fs.readFileSync(path.resolve(__dirname, "../../static/revenue.js"), "utf8"), context);

const lines = [
  { unit_price: 30, tickets_count: 100 },
  { unit_price: 15, tickets_count: 20 },
];
const total = vm.runInContext("revenueRecalcExpected")(lines);
assert.equal(total, 3300);
console.log("revenue recalc OK");
```

- [ ] **Step 3: Run both and verify failure**

Run: `python -m pytest tests/test_revenue_frontend.py -q`
Expected: FAIL — nav/view/script/styles missing.

- [ ] **Step 4: Add the NAV entry in `static/app.js`**

Insert into the `NAV` array under the "Учёт" group, after the `fuel` line:

```javascript
  ["revenue", "Выручка"],
```

- [ ] **Step 5: Create `static/revenue.js`**

```javascript
// static/revenue.js — вкладка «Выручка»
function revenueRecalcExpected(lines) {
  return (lines || []).reduce(
    (sum, ln) => sum + (Number(ln.unit_price) || 0) * (Number(ln.tickets_count) || 0),
    0,
  );
}
if (typeof window !== "undefined") window.revenueRecalcExpected = revenueRecalcExpected;

if (typeof VIEWS !== "undefined") {
  VIEWS.revenue = async function () {
    const st = window._revenue || { tab: "sheets" };
    window._revenue = st;
    if (st.tab === "tariffs") {
      const data = await api("/api/revenue/fare-types");
      const rows = data.items.map(t => `<tr><td>${esc(t.name)}</td><td>${esc(t.unit)}</td></tr>`).join("");
      return `<div class="revenue-tab"><h3>Тарифы и виды билетов</h3><table>${rows}</table></div>`;
    }
    const data = await api("/api/revenue/sheets");
    const rows = data.items.map(s =>
      `<tr><td>${s.number}</td><td>${esc(s.date)}</td><td>${s.expected_amount}</td>` +
      `<td>${s.submitted_amount}</td><td>${s.difference}</td><td>${esc(s.status)}</td></tr>`,
    ).join("");
    return `<div class="revenue-tab">
      <div class="route-card-toolbar">
        <button class="btn sec" onclick="window._revenue.tab='sheets';route()">Листы выручки</button>
        <button class="btn sec" onclick="window._revenue.tab='tariffs';route()">Тарифы</button>
        <button class="btn" onclick="openWin('/api/revenue/report.xlsx?date_from='+thisMonth()+'-01&date_to='+today()+'&group_by=route')">Отчёт Excel</button>
      </div>
      <table><thead><tr><th>№</th><th>Дата</th><th>Ожидаемо</th><th>Сдано</th><th>Разница</th><th>Статус</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  };
}
```

(The `api`, `esc`, `route`, `openWin`, `thisMonth`, `today` helpers are provided globally by `app.js`. The exported `revenueRecalcExpected` is what the JS behavior test drives.)

- [ ] **Step 6: Load the script and bump the app asset version in `static/index.html`**

Add before the closing `</body>` (after `route-card.js`):

```html
<script src="/static/revenue.js?v=1.0"></script>
```

Because `app.js` changed (NAV), bump its cache version. Find current pins and update every one:

```bash
grep -rn "app.js?v=3.2" static/ tests/
```

Change `app.js?v=3.2` to `app.js?v=3.3` in `static/index.html` and update the
matching assertions in `tests/test_route_documents_frontend.py` and
`tests/test_route_shift_frontend.py` (search each for `app.js?v=3.2`).

- [ ] **Step 7: Add styles in `static/styles.css`**

Append:

```css
.revenue-tab { padding: 8px 0; }
.revenue-tab table { width: 100%; border-collapse: collapse; }
.revenue-tab th, .revenue-tab td { border: 1px solid #d8dee6; padding: 4px 8px; text-align: left; }
@media (max-width: 760px) { .revenue-tab table { font-size: 13px; } }
@media print { .revenue-tab .route-card-toolbar { display: none; } }
```

(If a `@media print` block already exists, add the `.revenue-tab` print rule inside the existing conventions rather than duplicating the at-rule if the file's linter requires it; a second `@media print` block is otherwise valid CSS.)

- [ ] **Step 8: Run tests and verify pass**

Run:
```
python -m pytest tests/test_revenue_frontend.py tests/test_route_documents_frontend.py tests/test_route_shift_frontend.py -q
node tests/js/revenue_recalc_behavior.js
```
Expected: PASS and "revenue recalc OK".

- [ ] **Step 9: Commit**

```bash
git add static/revenue.js static/app.js static/index.html static/styles.css tests/test_revenue_frontend.py tests/js/revenue_recalc_behavior.js tests/test_route_documents_frontend.py tests/test_route_shift_frontend.py
git commit -m "feat(revenue): revenue tab in the interface"
```

---

### Task 8: Demo seed and final regression

**Files:**
- Modify: `app/seed.py`
- Create: `tests/test_revenue_seed.py`

- [ ] **Step 1: Write a failing seed test**

```python
# tests/test_revenue_seed.py
def test_seed_creates_fare_types_and_a_revenue_sheet(tmp_path):
    from app import db, seed
    db.DB_PATH = str(tmp_path / "revenue-seed.db")
    seed.run()  # opens its own connection, seeds a full demo АТП, closes it
    con = db.connect()
    try:
        fare_types = con.execute("SELECT COUNT(*) n FROM fare_types").fetchone()["n"]
        sheets = con.execute("SELECT COUNT(*) n FROM revenue_sheets").fetchone()["n"]
    finally:
        con.close()
    assert fare_types >= 3
    assert sheets >= 1
```

`seed.run()` calls `db.init_db()` itself and returns early only when drivers
already exist; a fresh `tmp_path` database seeds fully.

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/test_revenue_seed.py -q`
Expected: FAIL — no fare types seeded.

- [ ] **Step 3: Add revenue demo data in `app/seed.py`**

Insert this block in `run()` immediately before the final `con.close()` line
(the one followed by the "Демо-данные загружены" print). It opens its own
connection so it reads waybills already committed by `waybill_close`:

```python
    from . import revenue_service as _rs
    rc = db.connect()
    try:
        if rc.execute("SELECT 1 FROM fare_types").fetchone() is None:
            specs = [("single", "Разовый", "поездка", 32.0),
                     ("child", "Детский", "поездка", 16.0),
                     ("baggage", "Багаж", "место", 20.0),
                     ("month", "Проездной месячный", "месяц", 1900.0)]
            type_ids = {}
            for code, name, unit, price in specs:
                ft = _rs.upsert_fare_type(rc, code=code, name=name, unit=unit)
                _rs.add_tariff(rc, fare_type_id=ft, valid_from="2026-01-01", price=price)
                type_ids[code] = ft
            last_wb = rc.execute("SELECT id FROM waybills ORDER BY id DESC LIMIT 1").fetchone()
            if last_wb is not None:
                sid = _rs.create_sheet_from_waybill(rc, last_wb["id"], created_by="admin")
                _rs.set_sheet_lines(rc, sid, [(type_ids["single"], 120), (type_ids["child"], 25)])
                _rs.submit_sheet(rc, sid, 4000.0, user="admin")
            rc.commit()
    finally:
        rc.close()
```

- [ ] **Step 4: Run and verify pass, commit**

Run: `python -m pytest tests/test_revenue_seed.py -q`
Expected: PASS.

```bash
git add app/seed.py tests/test_revenue_seed.py
git commit -m "feat(revenue): demo fare types and revenue sheet"
```

- [ ] **Step 5: Full regression**

Run: `python -m pytest -q`
Expected: all pass (only pre-existing skips/warnings remain).

- [ ] **Step 6: Manual smoke (optional)**

Start the app (`python run.py --demo`), open the "Выручка" tab, confirm the
sheets list, tariffs list, and Excel report download work.

- [ ] **Step 7: Commit any regression fixes**

```bash
git add -A
git commit -m "fix(revenue): finalize revenue module"
```

Skip if no changes were needed.
