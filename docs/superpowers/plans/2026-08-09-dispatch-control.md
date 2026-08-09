# Dispatch Control Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an operational dispatch section («Диспетчер») over the approved daily order — line-release board and per-trip schedule adherence — with a per-date manual/GPS source toggle, telemetry ingest, and Excel export.

**Architecture:** Three SQLite tables. Logic in `app/dispatch_service.py`, exposed by `app/api_dispatch.py`, Excel in `app/dispatch_reports.py`, UI in `static/dispatch.js` (`VIEWS.dispatch`). The board is built from `order_lines` of the approved order; both manual actions and GPS telemetry write the same tables.

**Tech Stack:** Python 3, FastAPI, SQLite, openpyxl, pytest, httpx (TestClient), Node.js (UI test).

## Global Constraints

- Domain errors subclass `ValueError`; API maps to 400, "not found" (message ends «не найден/а») to 404, telemetry-in-manual / unknown-vehicle to 409.
- Service functions never `commit`; the API commits on success and writes `db.audit(...)`.
- Russian user-facing messages. Times are `HH:MM` strings; `deviation_min` is `int` (fact − plan).
- `db.audit(con, username, action, obj_type, obj_id, old=None, new=None, ...)`.
- Day type via `from app.api_planning import sched_day_type` → «будни»/«суббота»/«воскресенье».
- Statuses: `план, выпущен, на_линии, сошёл, срыв, замена`. `выпущен` sets `actual_release` + `deviation_min`; `сошёл/срыв/замена` require `reason`.
- Run tests with `python -m pytest -q`.

---

## File map
- `app/db.py` — 3 `CREATE TABLE`, indexes, `dispatch_tolerance_min` default setting.
- `app/auth.py` — `"dispatch"` in `WRITE_ACCESS` for `диспетчер`, `эксплуатация`.
- `app/dispatch_service.py` — logic (create).
- `app/api_dispatch.py` — REST (create); `app/main.py` — include router.
- `app/dispatch_reports.py` — Excel (create).
- `app/seed.py` — demo dispatch state.
- `static/dispatch.js`, `static/app.js` (NAV), `static/index.html` (script+version), `static/styles.css`.
- `tests/test_dispatch_service.py`, `tests/test_dispatch_api.py`, `tests/test_dispatch_reports.py`, `tests/test_dispatch_frontend.py`, `tests/js/dispatch_deviation_behavior.js`.

---

### Task 1: Schema, settings, permissions

**Files:** Modify `app/db.py`, `app/auth.py`; Create `tests/test_dispatch_service.py`.

- [ ] **Step 1: Failing schema test**

```python
# tests/test_dispatch_service.py
import pytest


def _open_db(tmp_path):
    from app import db
    db.DB_PATH = str(tmp_path / "dispatch.db")
    db.init_db()
    return db.connect()


def test_dispatch_tables_and_permission_and_setting(tmp_path):
    con = _open_db(tmp_path)
    try:
        tables = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        setting = con.execute("SELECT value FROM settings WHERE key='dispatch_tolerance_min'").fetchone()
    finally:
        con.close()
    assert {"dispatch_days", "dispatch_outputs", "dispatch_trip_facts"} <= tables
    assert setting is not None
    from app.auth import WRITE_ACCESS
    assert "dispatch" in WRITE_ACCESS["диспетчер"]
    assert "dispatch" in WRITE_ACCESS["эксплуатация"]
```

- [ ] **Step 2: Run — FAIL.** `python -m pytest tests/test_dispatch_service.py -q`

- [ ] **Step 3: Add tables to `SCHEMA`** (before closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS dispatch_days(
  id INTEGER PRIMARY KEY, date TEXT UNIQUE NOT NULL,
  source_mode TEXT DEFAULT 'manual', created_by TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS dispatch_outputs(
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, order_line_id INTEGER NOT NULL UNIQUE,
  plan_release TEXT, actual_release TEXT, deviation_min INTEGER,
  status TEXT DEFAULT 'план', reason TEXT, note TEXT, updated_by TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS dispatch_trip_facts(
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, order_line_id INTEGER NOT NULL,
  trip_number INTEGER NOT NULL, plan_dep TEXT, actual_dep TEXT,
  deviation_min INTEGER, on_time INTEGER, updated_by TEXT, updated_at TEXT,
  UNIQUE(order_line_id, trip_number));
```

- [ ] **Step 4: Indexes in `init_db`** (near other indexes):

```python
con.execute("CREATE INDEX IF NOT EXISTS idx_dispatch_outputs_date ON dispatch_outputs(date)")
con.execute("CREATE INDEX IF NOT EXISTS idx_dispatch_trip_facts_line ON dispatch_trip_facts(order_line_id)")
```

- [ ] **Step 5: Default setting.** In `DEFAULT_SETTINGS` (the dict with `session_timeout_min`, `repair_repeat_days`) add `"dispatch_tolerance_min": "2"`.

- [ ] **Step 6: Permissions** in `app/auth.py`:

```python
    "диспетчер": {"orders", "waybills", "roster", "summary", "revenue", "dispatch"},
    "эксплуатация": {"routes", "trips", "roster", "orders", "summary", "dispatch"},
```

- [ ] **Step 7: PASS + commit** `git commit -m "feat(dispatch): schema, settings, permissions"`

Note: confirm the defaults dict name/mechanism in `app/db.py` (search `session_timeout_min`); insert the setting the same way existing defaults are seeded.

---

### Task 2: Board build and status transitions

**Files:** Create `app/dispatch_service.py`; Modify `tests/test_dispatch_service.py`.

**Interfaces produced:**
- `class DispatchError(ValueError)`
- `ensure_day(con, date) -> dict`
- `set_source_mode(con, date, mode, *, user) -> dict`
- `build_board(con, date) -> dict` → `{"date", "source_mode", "has_order", "order_approved", "rows": [...], "summary": {...}}`; each row has `output_id, order_line_id, route_number, output_number, shift_number, driver_fio, garage_number, plan_release, actual_release, deviation_min, status, reason`.
- `set_output_status(con, output_id, status, *, at=None, reason=None, note=None, user) -> dict`
- `STATUSES` tuple; `_hhmm(value)` / `_deviation(plan, actual)` helpers.

- [ ] **Step 1: Failing tests** (seed an approved order with two outputs):

```python
# append to tests/test_dispatch_service.py
from app import dispatch_service as ds


def _seed_order(con, date="2026-08-09"):
    d = con.execute("INSERT INTO drivers(tab_number,fio) VALUES(?,?)", ("Т1","Иванов")).lastrowid
    b = con.execute("INSERT INTO buses(garage_number,plate) VALUES(?,?)", ("Г1","A1")).lastrowid
    r = con.execute("INSERT INTO routes(number,name) VALUES(?,?)", ("7","Центр")).lastrowid
    oid = con.execute("INSERT INTO orders(date,status) VALUES(?, 'утверждён')", (date,)).lastrowid
    line = con.execute(
        "INSERT INTO order_lines(order_id,route_id,output_number,shift_number,driver_id,bus_id,depart_depot,start_line) "
        "VALUES(?,?,?,?,?,?,?,?)", (oid, r, 1, 1, d, b, "05:50", "06:00")).lastrowid
    con.commit()
    return date, line, r, b


def test_build_board_from_approved_order(tmp_path):
    con = _open_db(tmp_path)
    try:
        date, line, *_ = _seed_order(con)
        board = ds.build_board(con, date)
        con.commit()
        assert board["has_order"] and board["order_approved"]
        assert board["source_mode"] == "manual"
        row = board["rows"][0]
        assert row["order_line_id"] == line
        assert row["plan_release"] == "05:50"
        assert row["status"] == "план"
    finally:
        con.close()


def test_release_sets_deviation_and_status(tmp_path):
    con = _open_db(tmp_path)
    try:
        date, line, *_ = _seed_order(con)
        board = ds.build_board(con, date)
        output_id = board["rows"][0]["output_id"]
        updated = ds.set_output_status(con, output_id, "выпущен", at="05:54", user="disp")
        con.commit()
        assert updated["status"] == "выпущен"
        assert updated["actual_release"] == "05:54"
        assert updated["deviation_min"] == 4
    finally:
        con.close()


def test_disruption_requires_reason(tmp_path):
    con = _open_db(tmp_path)
    try:
        date, line, *_ = _seed_order(con)
        output_id = ds.build_board(con, date)["rows"][0]["output_id"]
        with pytest.raises(ds.DispatchError):
            ds.set_output_status(con, output_id, "срыв", user="disp")
        ok = ds.set_output_status(con, output_id, "срыв", reason="ДТП", user="disp")
        con.commit()
        assert ok["status"] == "срыв" and ok["reason"] == "ДТП"
    finally:
        con.close()


def test_empty_board_without_approved_order(tmp_path):
    con = _open_db(tmp_path)
    try:
        board = ds.build_board(con, "2026-08-09")
        assert board["has_order"] is False and board["rows"] == []
    finally:
        con.close()
```

- [ ] **Step 2: Run — FAIL** (module missing).

- [ ] **Step 3: Implement** `app/dispatch_service.py`:

```python
# -*- coding: utf-8 -*-
"""Операционный контроль дня: табло выпуска и регулярность."""
import datetime
from . import db

STATUSES = ("план", "выпущен", "на_линии", "сошёл", "срыв", "замена")
_REASON_REQUIRED = {"сошёл", "срыв", "замена"}


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


def ensure_day(con, date):
    row = con.execute("SELECT * FROM dispatch_days WHERE date=?", (date,)).fetchone()
    if row is None:
        con.execute("INSERT INTO dispatch_days(date, source_mode, created_at) VALUES(?, 'manual', ?)", (date, _now()))
        row = con.execute("SELECT * FROM dispatch_days WHERE date=?", (date,)).fetchone()
    return dict(row)


def set_source_mode(con, date, mode, *, user):
    if mode not in ("manual", "gps"):
        raise DispatchError("Источник должен быть manual или gps")
    ensure_day(con, date)
    con.execute("UPDATE dispatch_days SET source_mode=? WHERE date=?", (mode, date))
    return {"date": date, "source_mode": mode}


def _approved_order(con, date):
    return con.execute("SELECT * FROM orders WHERE date=? AND status='утверждён'", (date,)).fetchone()


def build_board(con, date):
    day = ensure_day(con, date)
    order = _approved_order(con, date)
    if order is None:
        return {"date": date, "source_mode": day["source_mode"], "has_order": False,
                "order_approved": False, "rows": [], "summary": day_summary_counts([])}
    lines = con.execute(
        "SELECT l.*, r.number AS route_number, d.fio AS driver_fio, b.garage_number "
        "FROM order_lines l LEFT JOIN routes r ON r.id=l.route_id "
        "LEFT JOIN drivers d ON d.id=l.driver_id LEFT JOIN buses b ON b.id=l.bus_id "
        "WHERE l.order_id=? ORDER BY r.number, l.output_number, l.shift_number", (order["id"],)).fetchall()
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
    return {"date": date, "source_mode": day["source_mode"], "has_order": True,
            "order_approved": True, "rows": rows, "summary": day_summary_counts(rows)}


def _ensure_output(con, date, line):
    row = con.execute("SELECT * FROM dispatch_outputs WHERE order_line_id=?", (line["id"],)).fetchone()
    if row is None:
        plan = line["depart_depot"] or line["start_line"]
        con.execute("INSERT INTO dispatch_outputs(date, order_line_id, plan_release, status, updated_at) "
                    "VALUES(?,?,?, 'план', ?)", (date, line["id"], plan, _now()))
        row = con.execute("SELECT * FROM dispatch_outputs WHERE order_line_id=?", (line["id"],)).fetchone()
    return dict(row)


def set_output_status(con, output_id, status, *, at=None, reason=None, note=None, user):
    row = con.execute("SELECT * FROM dispatch_outputs WHERE id=?", (output_id,)).fetchone()
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
    con.execute("UPDATE dispatch_outputs SET status=?, actual_release=?, deviation_min=?, reason=?, note=?, "
                "updated_by=?, updated_at=? WHERE id=?",
                (status, actual_release, deviation, reason, note, user, _now(), output_id))
    return dict(con.execute("SELECT * FROM dispatch_outputs WHERE id=?", (output_id,)).fetchone())


def day_summary_counts(rows):
    summary = {"planned": len(rows)}
    for key, status in (("released", "выпущен"), ("on_line", "на_линии"),
                        ("off_line", "сошёл"), ("disrupted", "срыв"), ("replaced", "замена")):
        summary[key] = sum(1 for r in rows if r["status"] == status)
    active = [r for r in rows if r["status"] in ("выпущен", "на_линии", "сошёл")]
    on_time = sum(1 for r in active if r.get("deviation_min") is not None and abs(r["deviation_min"]) <= 2)
    summary["release_regularity"] = round(100 * on_time / len(rows), 1) if rows else 0.0
    return summary
```

- [ ] **Step 4: PASS + commit** `git commit -m "feat(dispatch): board and status transitions"`

---

### Task 3: Trip adherence and day summary

**Files:** Modify `app/dispatch_service.py`, `tests/test_dispatch_service.py`.

**Interfaces produced:**
- `list_trip_facts(con, date, order_line_id) -> list[dict]` (keys: `trip_number, plan_dep, actual_dep, deviation_min, on_time`).
- `set_trip_fact(con, order_line_id, trip_number, actual_dep, *, date, user) -> dict`.
- `day_summary(con, date) -> dict` (board summary + `trip_regularity`).
- `_tolerance(con) -> int`.

- [ ] **Step 1: Failing tests** — extend `_seed_order` to also insert two `route_trips` (day_type «воскресенье» for 2026-08-09 which is Sunday) for the output, then:

```python
def _seed_trips(con, route_id, day_type):
    con.executemany(
        "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,trip_number,dep_time,arr_time) "
        "VALUES(?,?,?,?,?,?,?)",
        [(route_id, day_type, 1, 1, 1, "06:00", "06:30"), (route_id, day_type, 1, 1, 2, "07:00", "07:30")])
    con.commit()


def test_trip_facts_plan_and_on_time(tmp_path):
    con = _open_db(tmp_path)
    try:
        from app.api_planning import sched_day_type
        date, line, route_id, _ = _seed_order(con)
        _seed_trips(con, route_id, sched_day_type(con, date))
        ds.build_board(con, date)
        facts = ds.list_trip_facts(con, date, line)
        assert [f["trip_number"] for f in facts] == [1, 2]
        assert facts[0]["plan_dep"] == "06:00"
        saved = ds.set_trip_fact(con, line, 1, "06:01", date=date, user="disp")
        con.commit()
        assert saved["deviation_min"] == 1 and saved["on_time"] == 1
        late = ds.set_trip_fact(con, line, 2, "07:05", date=date, user="disp")
        con.commit()
        assert late["deviation_min"] == 5 and late["on_time"] == 0
        summary = ds.day_summary(con, date)
        assert summary["trip_regularity"] == 50.0
    finally:
        con.close()
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement**:

```python
from .api_planning import sched_day_type


def _tolerance(con):
    row = con.execute("SELECT value FROM settings WHERE key='dispatch_tolerance_min'").fetchone()
    try:
        return int(row["value"]) if row else 2
    except (TypeError, ValueError):
        return 2


def list_trip_facts(con, date, order_line_id):
    line = con.execute("SELECT route_id, output_number, shift_number FROM order_lines WHERE id=?", (order_line_id,)).fetchone()
    if line is None:
        raise DispatchError("Выход наряда не найден")
    day_type = sched_day_type(con, date)
    trips = con.execute(
        "SELECT trip_number, MIN(dep_time) AS dep FROM route_trips "
        "WHERE route_id=? AND day_type=? AND output_number=? AND (shift_number=? OR shift_number IS NULL) "
        "AND trip_number IS NOT NULL GROUP BY trip_number ORDER BY trip_number",
        (line["route_id"], day_type, line["output_number"], line["shift_number"])).fetchall()
    facts = {r["trip_number"]: dict(r) for r in con.execute(
        "SELECT trip_number, actual_dep, deviation_min, on_time FROM dispatch_trip_facts WHERE order_line_id=?",
        (order_line_id,))}
    result = []
    for t in trips:
        fact = facts.get(t["trip_number"], {})
        result.append({"trip_number": t["trip_number"], "plan_dep": t["dep"],
                       "actual_dep": fact.get("actual_dep"), "deviation_min": fact.get("deviation_min"),
                       "on_time": fact.get("on_time")})
    return result


def set_trip_fact(con, order_line_id, trip_number, actual_dep, *, date, user):
    plan = next((f["plan_dep"] for f in list_trip_facts(con, date, order_line_id)
                 if f["trip_number"] == trip_number), None)
    if plan is None:
        raise DispatchError("Рейс не найден в плане выхода")
    deviation = _deviation(plan, actual_dep)
    on_time = 1 if deviation is not None and abs(deviation) <= _tolerance(con) else 0
    con.execute(
        "INSERT INTO dispatch_trip_facts(date, order_line_id, trip_number, plan_dep, actual_dep, deviation_min, on_time, updated_by, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(order_line_id, trip_number) DO UPDATE SET actual_dep=excluded.actual_dep, "
        "plan_dep=excluded.plan_dep, deviation_min=excluded.deviation_min, on_time=excluded.on_time, "
        "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
        (date, order_line_id, trip_number, plan, actual_dep, deviation, on_time, user, _now()))
    return {"trip_number": trip_number, "plan_dep": plan, "actual_dep": actual_dep,
            "deviation_min": deviation, "on_time": on_time}


def day_summary(con, date):
    board = build_board(con, date)
    summary = dict(board["summary"])
    facts = con.execute("SELECT on_time FROM dispatch_trip_facts WHERE date=? AND actual_dep IS NOT NULL", (date,)).fetchall()
    total = len(facts)
    on_time = sum(1 for f in facts if f["on_time"] == 1)
    summary["trip_regularity"] = round(100 * on_time / total, 1) if total else 0.0
    summary["trips_recorded"] = total
    return summary
```

Also update `day_summary_counts` tolerance use: replace the hard-coded `abs(...) <= 2` with a passed tolerance — change `day_summary_counts(rows)` to `day_summary_counts(rows, tolerance=2)` and call it from `build_board` as `day_summary_counts(rows, _tolerance(con))`.

- [ ] **Step 4: PASS + commit** `git commit -m "feat(dispatch): trip adherence and summary"`

---

### Task 4: Telemetry ingest and source mode

**Files:** Modify `app/dispatch_service.py`, `tests/test_dispatch_service.py`.

**Interfaces produced:** `apply_telemetry(con, payload, *, user) -> dict`.

- [ ] **Step 1: Failing tests**:

```python
def test_telemetry_applies_in_gps_mode_only(tmp_path):
    con = _open_db(tmp_path)
    try:
        date, line, route_id, bus_id = _seed_order(con)
        ds.build_board(con, date)
        with pytest.raises(ds.DispatchError):
            ds.apply_telemetry(con, {"date": date, "garage_number": "Г1", "event": "release", "time": "06:00"}, user="gw")
        ds.set_source_mode(con, date, "gps", user="admin"); con.commit()
        out = ds.apply_telemetry(con, {"date": date, "garage_number": "Г1", "event": "release", "time": "06:03"}, user="gw")
        con.commit()
        assert out["status"] == "выпущен" and out["actual_release"] == "06:03" and out["deviation_min"] == 13
    finally:
        con.close()


def test_telemetry_unknown_vehicle(tmp_path):
    con = _open_db(tmp_path)
    try:
        date, *_ = _seed_order(con)
        ds.set_source_mode(con, date, "gps", user="admin")
        ds.build_board(con, date); con.commit()
        with pytest.raises(ds.DispatchError):
            ds.apply_telemetry(con, {"date": date, "garage_number": "НЕТ", "event": "release"}, user="gw")
    finally:
        con.close()
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement**:

```python
_TELEMETRY_STATUS = {"release": "выпущен", "on_line": "на_линии", "off_line": "сошёл", "disruption": "срыв"}


def apply_telemetry(con, payload, *, user):
    date = payload.get("date")
    day = ensure_day(con, date)
    if day["source_mode"] != "gps":
        raise DispatchError("Телеметрия принимается только в режиме GPS")
    event = payload.get("event")
    if event == "trip_departure":
        line_id = _line_by_vehicle(con, date, payload)
        return set_trip_fact(con, line_id, int(payload["trip_number"]), payload.get("time"), date=date, user=user)
    if event not in _TELEMETRY_STATUS:
        raise DispatchError("Неизвестное событие телеметрии")
    line_id = _line_by_vehicle(con, date, payload)
    output = con.execute("SELECT id FROM dispatch_outputs WHERE order_line_id=?", (line_id,)).fetchone()
    reason = payload.get("reason") or ("GPS" if event in ("off_line", "disruption") else None)
    return set_output_status(con, output["id"], _TELEMETRY_STATUS[event], at=payload.get("time"), reason=reason, user=user)


def _line_by_vehicle(con, date, payload):
    order = _approved_order(con, date)
    if order is None:
        raise DispatchError("Наряд на дату не найден")
    if payload.get("vehicle_id"):
        row = con.execute("SELECT id FROM order_lines WHERE order_id=? AND bus_id=?", (order["id"], payload["vehicle_id"])).fetchone()
    else:
        row = con.execute("SELECT l.id FROM order_lines l JOIN buses b ON b.id=l.bus_id WHERE l.order_id=? AND b.garage_number=?",
                          (order["id"], payload.get("garage_number"))).fetchone()
    if row is None:
        raise DispatchError("Автобус не найден в наряде на дату")
    return row["id"]
```

Note: for `off_line`/`disruption` telemetry a `reason` defaults to «GPS» so the reason-required rule passes.

- [ ] **Step 4: PASS + commit** `git commit -m "feat(dispatch): telemetry ingest and source mode"`

---

### Task 5: REST API

**Files:** Create `app/api_dispatch.py`; Modify `app/main.py`; Create `tests/test_dispatch_api.py`.

**Endpoints** (`/api/dispatch`, all `Depends(current_user)`; writes `require_write(user,"dispatch")` + audit + commit):
`GET /board`, `GET /adherence`, `GET /summary`, `PUT /source-mode`, `POST /outputs/{output_id}/status`, `PUT /trips/{order_line_id}/{trip_number}`, `POST /telemetry`, `GET /report.xlsx`.

- [ ] **Step 1: Failing API tests** (reuse `_client` from `tests/test_route_schedule_document`; seed order+trips via a helper mirroring `_seed_order`/`_seed_trips` but committing through a fresh `db.connect()`):

```python
# tests/test_dispatch_api.py — key assertions
def test_board_status_and_adherence_flow(tmp_path):
    client, date, line = _prepared(tmp_path)  # helper seeds approved order + trips
    board = client.get("/api/dispatch/board", params={"date": date}).json()
    assert board["has_order"] and board["rows"]
    output_id = board["rows"][0]["output_id"]
    r = client.post(f"/api/dispatch/outputs/{output_id}/status", json={"status": "выпущен", "at": "05:55"})
    assert r.status_code == 200 and r.json()["status"] == "выпущен"
    r = client.put(f"/api/dispatch/trips/{line}/1", json={"actual_dep": "06:00"})
    assert r.status_code == 200 and r.json()["on_time"] == 1
    assert client.get("/api/dispatch/summary", params={"date": date}).json()["released"] == 1


def test_source_mode_and_telemetry(tmp_path):
    client, date, line = _prepared(tmp_path)
    assert client.put("/api/dispatch/source-mode", json={"date": date, "mode": "gps"}).status_code == 200
    r = client.post("/api/dispatch/telemetry", json={"date": date, "garage_number": "Г1", "event": "release", "time": "06:00"})
    assert r.status_code == 200
    # manual-mode telemetry rejected
    client.put("/api/dispatch/source-mode", json={"date": date, "mode": "manual"})
    assert client.post("/api/dispatch/telemetry", json={"date": date, "garage_number": "Г1", "event": "release"}).status_code == 409
```

- [ ] **Step 2: Run — FAIL (404).**

- [ ] **Step 3: Implement `app/api_dispatch.py`** — mirror `app/api_revenue.py` structure: `router = APIRouter(prefix="/api/dispatch")`, `_guard(user)=require_write(user,"dispatch")`, `_handle(exc)` mapping (telemetry-manual message «только в режиме GPS» → 409; «не найден/а» → 404; else 400). Each write opens `db.connect()`, calls the service, `db.audit`, `con.commit()`, returns the result; wrap `ValueError` → `_handle`. `GET` endpoints don't guard writes. `report.xlsx` uses `_xlsx_download_response` (Task 6). Register `from .api_dispatch import router as dispatch_router` and `app.include_router(dispatch_router)` in `app/main.py`.

- [ ] **Step 4: PASS + commit** `git commit -m "feat(dispatch): REST API"`

---

### Task 6: Excel dispatch report

**Files:** Create `app/dispatch_reports.py`; wire `GET /report.xlsx`; Create `tests/test_dispatch_reports.py`.

**Interfaces:** `build_dispatch_report(con, date) -> Workbook`; `dispatch_report_filename(date) -> str`.

- [ ] **Step 1: Failing test** — seed a released output + one trip fact, build report, assert sheet titles `["Выпуск","Регулярность"]` and that the driver/route and a deviation value appear.

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement** using `apply_sheet_setup`, `write_title_band`, `write_table_header` from `app.route_document_xlsx`. Sheet «Выпуск» from `build_board(con,date)["rows"]`; sheet «Регулярность» from each output's `list_trip_facts`. `dispatch_report_filename(date) -> f"Диспетчер_{date}.xlsx"`.

- [ ] **Step 4: PASS + commit** `git commit -m "feat(dispatch): Excel report"`

---

### Task 7: Frontend tab

**Files:** Create `static/dispatch.js`; Modify `static/app.js` (NAV), `static/index.html` (script + bump `app.js` version), `static/styles.css`; Create `tests/test_dispatch_frontend.py`, `tests/js/dispatch_deviation_behavior.js`.

- [ ] **Step 1: Failing static + JS tests**:

```python
# tests/test_dispatch_frontend.py
def test_dispatch_nav_and_view():
    app = _src("app.js"); js = _src("dispatch.js")
    assert '["dispatch", "Диспетчер"]' in app
    assert "VIEWS.dispatch" in js
    assert "/api/dispatch/board" in js and "/api/dispatch/telemetry" in js
    assert "dispatchDeviationLabel" in js
    assert "Смоделировать выпуск" in js  # GPS simulator button

def test_index_loads_dispatch_script():
    assert "/static/dispatch.js?v=1.0" in _src("index.html")
```

```javascript
// tests/js/dispatch_deviation_behavior.js
const ctx = vm.createContext({ console, VIEWS: {} }); ctx.window = ctx;
vm.runInContext(fs.readFileSync(".../static/dispatch.js","utf8"), ctx);
assert.equal(vm.runInContext("dispatchDeviationLabel",ctx)(4), "+4′");
assert.equal(vm.runInContext("dispatchDeviationLabel",ctx)(-3), "−3′");
assert.equal(vm.runInContext("dispatchDeviationLabel",ctx)(null), "—");
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement.** Add `["dispatch", "Диспетчер"]` to `NAV` after `["order", ...]`. Create `static/dispatch.js` exporting `dispatchDeviationLabel(min)` (`window.dispatchDeviationLabel = ...`) and registering `VIEWS.dispatch` (date picker, source toggle calling `PUT /source-mode`, subtabs «Выпуск»/«Регулярность», status buttons in manual mode / «Смоделировать выпуск» posting telemetry in gps mode, summary row, «Отчёт Excel» via `openWin('/api/dispatch/report.xlsx?date=...')`). Add script tag `<script src="/static/dispatch.js?v=1.0">` and bump `app.js?v=3.3`→`3.4` in `index.html` (update all `app.js?v=3.3` pins: `grep -rn "app.js?v=3.3" static/ tests/`). Add `.dispatch-tab` styles (+ `@media print`).

- [ ] **Step 4: PASS** — run frontend tests + `node tests/js/dispatch_deviation_behavior.js` + the bumped `app.js` pin tests.

- [ ] **Step 5: Commit** `git commit -m "feat(dispatch): dispatch tab in the interface"`

---

### Task 8: Demo seed and full regression

**Files:** Modify `app/seed.py`; Create `tests/test_dispatch_seed.py`.

- [ ] **Step 1: Failing seed test** — after `seed.run()`, assert `dispatch_outputs` has ≥1 row with `status='выпущен'`.

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement.** In `run()` before the final `con.close()`, open a fresh connection: for today's approved order (`orders WHERE date=today status='утверждён'`), `build_board`, then set a couple of outputs `выпущен` (with an `at` a few minutes off plan), one `срыв` (reason «техническая неисправность»), and record 1–2 trip facts. Guard with `if fetchone()` so re-runs are safe. Commit + close.

- [ ] **Step 4: PASS + commit** `git commit -m "feat(dispatch): demo dispatch state"`

- [ ] **Step 5: Full regression** `python -m pytest -q` → all pass.

- [ ] **Step 6: Commit any fixes.**
