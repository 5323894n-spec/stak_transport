# Summary Schedule Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the approved `Сводное расписание` module that builds saved dispatcher-ready summary schedule versions from existing route schedules, roster assignments, buses, drivers, checks, Excel export, history, and order creation.

**Architecture:** Implement a separate snapshot module with new SQLite tables, one FastAPI router, one Excel helper, and one SPA view. The module reads existing `routes`, `route_trips`, `roster`, `roster_assignments`, `drivers`, `buses`, `absences`, and `norms`, then saves immutable summary rows and detected problems. Existing schedule, roster, order, and waybill behavior remains unchanged.

**Tech Stack:** Python 3, FastAPI, SQLite, openpyxl, pytest, FastAPI TestClient, vanilla JavaScript SPA in `static/app.js`.

---

## File Structure

- Create `app/api_summary.py`: summary API, generation, checks, view aggregation, Excel export endpoint, order creation from saved summary.
- Modify `app/db.py`: add `summary_schedules`, `summary_schedule_lines`, `summary_schedule_errors`, indexes, and column migrations for repeated upgrades.
- Modify `app/auth.py`: add write access for section `summary`.
- Modify `app/main.py`: include `summary_router`.
- Modify `app/xl.py`: add `summary_schedule_xlsx_response()`.
- Modify `static/app.js`: add menu item and `VIEWS.summarySchedule`.
- Modify `static/styles.css`: add compact summary table, KPI, and error status styles.
- Create `tests/test_summary_schedule_api.py`: backend, Excel, history, and order tests.
- Create `tests/test_summary_schedule_ui.py`: static UI presence test.
- Modify `README.md`: short usage instructions.

## Existing Project Anchors

- Router mounting: `app/main.py`.
- Role matrix: `app/auth.py`, `WRITE_ACCESS`.
- DB schema: `app/db.py`, `SCHEMA` and `MIGRATIONS`.
- Day type: `sched_day_type(con, iso_date)` in `app/api_planning.py`.
- Route output summary: `outputs_summary(con, route_id, day_type)` in `app/api_planning.py`.
- Fuel calculation: `planned_fuel(bus, distance, iso_date)` in `app/api_planning.py`.
- Time and 424 checks: `app/norms.py`, especially `tmin()`, `tstr()`, `shift_minutes()`, `check_period()`.
- Existing Excel responses: `app/xl.py`.
- SPA navigation: `static/app.js`, `NAV` and `VIEWS`.

## Data Rules

- A summary version stores rows for each selected service date and each matching `route_trips` row.
- Because `route_trips` has no schedule status field, first implementation treats `routes.active=1` as `действует`.
- Inactive routes are skipped unless `include_inactive=true`.
- `summary_schedules.filters_json` stores selected route ids and generation flags for history.
- Period summaries can be viewed and exported; order creation is allowed only when `period_start == period_end`.
- 424 labor-rest findings are warnings only.
- Order creation is blocked only by rows in `summary_schedule_errors` with level `Критическая ошибка`.

---

### Task 1: Storage, Router, and Empty History Endpoint

**Files:**
- Create: `tests/test_summary_schedule_api.py`
- Create: `app/api_summary.py`
- Modify: `app/db.py`
- Modify: `app/auth.py`
- Modify: `app/main.py`

- [ ] **Step 1: Write the failing schema and history endpoint test**

Create `tests/test_summary_schedule_api.py`:

```python
# -*- coding: utf-8 -*-

from fastapi.testclient import TestClient

DATE = "2026-07-06"
DAY_TYPE = "будни"


def make_client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "atp-summary.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
    finally:
        con.close()

    client = TestClient(app)
    token = client.post("/api/login", json={"username": "admin", "password": "admin"}).json()["token"]
    client.headers.update({"Authorization": "Bearer " + token})
    return client


def test_summary_schema_and_history_endpoint_exist(tmp_path):
    client = make_client(tmp_path)

    import app.db as db

    con = db.connect()
    try:
        tables = {row["name"] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()

    assert {"summary_schedules", "summary_schedule_lines", "summary_schedule_errors"}.issubset(tables)
    response = client.get("/api/summary-schedules")
    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}
```

- [ ] **Step 2: Run the test and confirm it fails**

Run:

```powershell
python -m pytest tests/test_summary_schedule_api.py::test_summary_schema_and_history_endpoint_exist -q
```

Expected: FAIL because the tables and endpoint are not present.

- [ ] **Step 3: Add DB tables in `app/db.py`**

Insert this SQL in `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS summary_schedules(
  id INTEGER PRIMARY KEY,
  schedule_date TEXT,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  day_type TEXT,
  status TEXT DEFAULT 'сформировано',
  created_by TEXT,
  created_at TEXT,
  updated_at TEXT,
  routes_count INTEGER DEFAULT 0,
  trips_count INTEGER DEFAULT 0,
  runs_count INTEGER DEFAULT 0,
  vehicles_count INTEGER DEFAULT 0,
  drivers_count INTEGER DEFAULT 0,
  errors_count INTEGER DEFAULT 0,
  warnings_count INTEGER DEFAULT 0,
  filters_json TEXT DEFAULT '{}',
  comment TEXT,
  excel_file_path TEXT
);

CREATE TABLE IF NOT EXISTS summary_schedule_lines(
  id INTEGER PRIMARY KEY,
  summary_schedule_id INTEGER NOT NULL,
  service_date TEXT NOT NULL,
  route_id INTEGER,
  route_number TEXT,
  route_name TEXT,
  direction TEXT,
  run_number INTEGER,
  shift_number INTEGER,
  trip_number INTEGER,
  vehicle_id INTEGER,
  vehicle_number TEXT,
  garage_number TEXT,
  driver_id INTEGER,
  driver_tab_number TEXT,
  driver_name TEXT,
  departure_time TEXT,
  arrival_time TEXT,
  trip_duration INTEGER DEFAULT 0,
  depot_departure_time TEXT,
  depot_return_time TEXT,
  distance_km REAL DEFAULT 0,
  day_type TEXT,
  schedule_version TEXT,
  status TEXT DEFAULT 'действует',
  error_flag INTEGER DEFAULT 0,
  comment TEXT,
  FOREIGN KEY(summary_schedule_id) REFERENCES summary_schedules(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS summary_schedule_errors(
  id INTEGER PRIMARY KEY,
  summary_schedule_id INTEGER NOT NULL,
  line_id INTEGER,
  level TEXT NOT NULL,
  route_number TEXT,
  run_number INTEGER,
  trip_number INTEGER,
  object_type TEXT,
  object_label TEXT,
  message TEXT NOT NULL,
  recommendation TEXT,
  created_at TEXT,
  FOREIGN KEY(summary_schedule_id) REFERENCES summary_schedules(id) ON DELETE CASCADE,
  FOREIGN KEY(line_id) REFERENCES summary_schedule_lines(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_summary_lines_summary ON summary_schedule_lines(summary_schedule_id);
CREATE INDEX IF NOT EXISTS idx_summary_lines_date_route ON summary_schedule_lines(service_date, route_id, run_number, shift_number);
CREATE INDEX IF NOT EXISTS idx_summary_errors_summary ON summary_schedule_errors(summary_schedule_id);
```

Add migrations:

```python
("summary_schedules", "filters_json", "TEXT DEFAULT '{}'"),
("summary_schedule_lines", "service_date", "TEXT"),
("summary_schedule_lines", "vehicle_id", "INTEGER"),
("summary_schedule_lines", "driver_id", "INTEGER"),
```

- [ ] **Step 4: Add role access in `app/auth.py`**

Change the dispatcher and exploitation role sets:

```python
"диспетчер": {"orders", "waybills", "roster", "summary"},
"эксплуатация": {"routes", "trips", "roster", "orders", "summary"},
```

- [ ] **Step 5: Create `app/api_summary.py`**

```python
# -*- coding: utf-8 -*-
"""Сводное расписание: версии, проверки, Excel и формирование наряда."""

from fastapi import APIRouter, Depends

from . import db
from .auth import current_user

router = APIRouter(prefix="/api/summary-schedules", tags=["summary-schedules"])


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
```

- [ ] **Step 6: Mount router in `app/main.py`**

Add:

```python
from .api_summary import router as summary_router
```

Add:

```python
app.include_router(summary_router)
```

- [ ] **Step 7: Run and commit**

Run:

```powershell
python -m pytest tests/test_summary_schedule_api.py::test_summary_schema_and_history_endpoint_exist -q
```

Expected: PASS.

Commit:

```powershell
git add app/db.py app/auth.py app/main.py app/api_summary.py tests/test_summary_schedule_api.py
git commit -m "feat: add summary schedule storage"
```

---

### Task 2: Snapshot Generation and View Aggregation

**Files:**
- Modify: `tests/test_summary_schedule_api.py`
- Modify: `app/api_summary.py`

- [ ] **Step 1: Add seed helper and failing generation test**

Append to `tests/test_summary_schedule_api.py`:

```python
def seed_clean_summary_source():
    import app.db as db

    con = db.connect()
    try:
        driver_id = con.execute(
            "INSERT INTO drivers(tab_number,fio,status,default_schedule) VALUES(?,?,?,?)",
            ("7001", "Summary Driver", "работает", "2/2"),
        ).lastrowid
        bus_id = con.execute(
            "INSERT INTO buses(garage_number,plate,status,fuel_rate,assigned_driver_id) VALUES(?,?,?,?,?)",
            ("G-701", "А701АА69", "исправен", 35.0, driver_id),
        ).lastrowid
        con.execute("UPDATE drivers SET assigned_bus_id=? WHERE id=?", (bus_id, driver_id))
        route_id = con.execute(
            "INSERT INTO routes(number,name,start_point,end_point,length_km,length_back_km,active,version) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("77", "Вокзал - Центр", "Вокзал", "Центр", 12.0, 12.0, 1, 3),
        ).lastrowid
        con.executemany(
            "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,trip_number,direction,dep_time,arr_time,distance_km) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (route_id, DAY_TYPE, 1, 1, 1, "прямое", "06:00", "06:45", 12.0),
                (route_id, DAY_TYPE, 1, 1, 2, "обратное", "07:00", "07:45", 12.0),
            ],
        )
        con.execute(
            "INSERT INTO roster_assignments(driver_id,date,route_id,day_type,output_number,shift_number,trip_from,trip_to,"
            "start_time,end_time,hours,distance_km,trips_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (driver_id, DATE, route_id, DAY_TYPE, 1, 1, 1, 2, "06:00", "07:45", 1.5, 24.0, 2),
        )
        con.commit()
        return {"driver_id": driver_id, "bus_id": bus_id, "route_id": route_id}
    finally:
        con.close()


def test_generate_summary_creates_snapshot_lines_and_views(tmp_path):
    client = make_client(tmp_path)
    ctx = seed_clean_summary_source()

    response = client.post("/api/summary-schedules/generate", json={
        "date_from": DATE,
        "date_to": DATE,
        "route_ids": [ctx["route_id"]],
        "include_inactive": False,
        "comment": "test generation",
    })

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["summary"]["trips_count"] == 2
    assert data["summary"]["routes_count"] == 1
    assert data["summary"]["runs_count"] == 1
    assert data["summary"]["vehicles_count"] == 1
    assert data["summary"]["drivers_count"] == 1
    assert len(data["lines"]) == 2
    assert data["lines"][0]["route_number"] == "77"
    assert data["lines"][0]["driver_name"] == "Summary Driver"
    assert data["lines"][0]["garage_number"] == "G-701"
    assert data["views"]["by_outputs"][0]["trips_count"] == 2

    detail = client.get(f"/api/summary-schedules/{data['summary']['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["summary"]["id"] == data["summary"]["id"]
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
python -m pytest tests/test_summary_schedule_api.py::test_generate_summary_creates_snapshot_lines_and_views -q
```

Expected: FAIL because generation and detail endpoints are absent.

- [ ] **Step 3: Add generation imports and constants in `app/api_summary.py`**

```python
import datetime
import json

from fastapi import Body, HTTPException

from .auth import current_user, require_write
from .api_planning import planned_fuel, sched_day_type
from . import norms as N

CRITICAL = "Критическая ошибка"
ERROR = "Ошибка"
WARNING = "Предупреждение"
INFO = "Информация"
```

- [ ] **Step 4: Add core helper contracts in `app/api_summary.py`**

Implement these helpers with the shown signatures and return shapes:

```python
def _date_range(date_from: str, date_to: str):
    start = datetime.date.fromisoformat(date_from)
    end = datetime.date.fromisoformat(date_to)
    if end < start:
        raise HTTPException(400, "Дата окончания меньше даты начала")
    if (end - start).days > 62:
        raise HTTPException(400, "Период сводного расписания не должен превышать 63 дня")
    while start <= end:
        yield start.isoformat()
        start += datetime.timedelta(days=1)


def _duration_minutes(dep: str, arr: str) -> int:
    if not dep or not arr:
        return 0
    return N.shift_minutes(dep, arr)


def _depot_times(con, service_date: str, line_start: str, line_end: str):
    nrm = db.get_active_norms(con, service_date)
    prep = int(nrm.get("prep_final_minutes") or 0) + int(nrm.get("med_check_minutes") or 0)
    start = N.tmin(line_start)
    end = N.tmin(line_end)
    if start is None or end is None:
        return "", ""
    if end < start:
        end += 1440
    return N.tstr(start - prep - 10), N.tstr(end + 10)
```

Add these helper definitions in the same section:

```python
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
```

Also add `_line_from_trip()`, `_insert_line()`, `_recount_summary()`, `_load_summary()`, and `_build_views()` with the exact return keys described below.

Required behavior:

- `_fetch_assignment()` first searches `roster_assignments` for date, route, output, shift, and trip range; then searches `roster` for a working day row.
- `_fetch_bus()` first uses `drivers.assigned_bus_id`; then `buses.assigned_driver_id`.
- `_line_from_trip()` returns keys matching `summary_schedule_lines` columns.
- `_build_views()` returns keys `by_routes`, `by_outputs`, `by_drivers`, `by_buses`, `by_time`, and `errors`.
- `_build_views()["by_time"]` includes at least `выезд из парка`, `начало рейса`, `прибытие`, `заезд в парк`.

- [ ] **Step 5: Add endpoints in `app/api_summary.py`**

```python
@router.post("/generate")
def generate_summary(payload: dict = Body(default={}), user=Depends(current_user)):
    require_write(user, "summary")
    date_from = payload.get("date_from") or payload.get("schedule_date")
    date_to = payload.get("date_to") or date_from
    if not date_from or not date_to:
        raise HTTPException(400, "Укажите дату или период")
    route_ids = [int(x) for x in payload.get("route_ids") or []]
    include_inactive = bool(payload.get("include_inactive"))
    now = datetime.datetime.now().isoformat(timespec="seconds")
    con = db.connect()
    try:
        summary_id = con.execute(
            "INSERT INTO summary_schedules(schedule_date,period_start,period_end,day_type,status,created_by,created_at,updated_at,filters_json,comment) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (date_from if date_from == date_to else None, date_from, date_to, payload.get("day_type") or "", "сформировано",
             user["username"], now, now, json.dumps({"route_ids": route_ids, "include_inactive": include_inactive}, ensure_ascii=False),
             payload.get("comment") or ""),
        ).lastrowid
        route_sql = "SELECT * FROM routes"
        params = []
        where = []
        if route_ids:
            where.append("id IN (%s)" % ",".join("?" for _ in route_ids))
            params.extend(route_ids)
        if not include_inactive:
            where.append("active=1")
        if where:
            route_sql += " WHERE " + " AND ".join(where)
        route_sql += " ORDER BY number, name"
        routes = db.rows(con.execute(route_sql, params))
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
```

For this task, `_build_and_store_errors()` can clear existing errors and set `error_flag=0`; Task 3 replaces it.

- [ ] **Step 6: Run and commit**

Run:

```powershell
python -m pytest tests/test_summary_schedule_api.py -q
```

Expected: PASS for Task 1 and Task 2 tests.

Commit:

```powershell
git add app/api_summary.py tests/test_summary_schedule_api.py
git commit -m "feat: generate summary schedule snapshots"
```

---

### Task 3: Error Checks and 424 Warnings

**Files:**
- Modify: `tests/test_summary_schedule_api.py`
- Modify: `app/api_summary.py`

- [ ] **Step 1: Add failing tests for critical errors and recheck**

Append:

```python
def test_summary_errors_detect_missing_driver_and_bus(tmp_path):
    client = make_client(tmp_path)
    import app.db as db

    con = db.connect()
    try:
        route_id = con.execute("INSERT INTO routes(number,name,active,version) VALUES(?,?,?,?)", ("88", "No assignment route", 1, 1)).lastrowid
        con.execute(
            "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,trip_number,direction,dep_time,arr_time,distance_km) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (route_id, DAY_TYPE, 1, 1, 1, "прямое", "08:00", "08:30", 7.0),
        )
        con.commit()
    finally:
        con.close()

    response = client.post("/api/summary-schedules/generate", json={"date_from": DATE, "date_to": DATE})
    assert response.status_code == 200, response.text
    errors = response.json()["errors"]
    assert any(e["level"] == "Критическая ошибка" and "водитель" in e["message"].lower() for e in errors)
    assert any(e["level"] == "Критическая ошибка" and "автобус" in e["message"].lower() for e in errors)
    assert any(line["error_flag"] == 1 for line in response.json()["lines"])


def test_summary_recheck_returns_same_summary_id(tmp_path):
    client = make_client(tmp_path)
    seed_clean_summary_source()
    generated = client.post("/api/summary-schedules/generate", json={"date_from": DATE, "date_to": DATE})
    summary_id = generated.json()["summary"]["id"]

    recheck = client.post(f"/api/summary-schedules/{summary_id}/check")

    assert recheck.status_code == 200, recheck.text
    assert recheck.json()["summary"]["id"] == summary_id
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest tests/test_summary_schedule_api.py::test_summary_errors_detect_missing_driver_and_bus tests/test_summary_schedule_api.py::test_summary_recheck_returns_same_summary_id -q
```

Expected: FAIL because checks and recheck endpoint are incomplete.

- [ ] **Step 3: Add error helpers in `app/api_summary.py`**

```python
def _add_error(con, summary_id, line_id, level, line, object_type, object_label, message, recommendation):
    con.execute(
        "INSERT INTO summary_schedule_errors(summary_schedule_id,line_id,level,route_number,run_number,trip_number,"
        "object_type,object_label,message,recommendation,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (summary_id, line_id, level, line.get("route_number") if line else "", line.get("run_number") if line else None,
         line.get("trip_number") if line else None, object_type, object_label, message, recommendation,
         datetime.datetime.now().isoformat(timespec="seconds")),
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
```

- [ ] **Step 4: Implement `_build_and_store_errors()`**

The function must:

- clear old errors for the summary;
- add critical errors for missing driver and missing bus;
- add errors for missing route number, output number, trip number, departure, arrival;
- add a warning when arrival is earlier than departure because it may be an overnight trip;
- add critical errors for approved driver absences on the service date;
- add critical errors for buses whose status is not `исправен` or `на линии`;
- add warnings for missing `plate` or `garage_number`;
- add critical errors for overlapping driver or bus assignments on the same date;
- add errors for duplicated trip numbers inside route, date, output, and shift;
- add warnings from `N.check_period(con, date_from, date_to)` with recommendation text starting with `Приказ 424:`.

Use this endpoint for manual recheck:

```python
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
```

- [ ] **Step 5: Run and commit**

Run:

```powershell
python -m pytest tests/test_summary_schedule_api.py -q
```

Expected: PASS.

Commit:

```powershell
git add app/api_summary.py tests/test_summary_schedule_api.py
git commit -m "feat: validate summary schedule snapshots"
```

---

### Task 4: Excel Export with Nine Sheets

**Files:**
- Modify: `tests/test_summary_schedule_api.py`
- Modify: `app/xl.py`
- Modify: `app/api_summary.py`

- [ ] **Step 1: Add failing Excel test**

Append:

```python
def test_summary_export_xlsx_has_required_sheets_and_format(tmp_path):
    import io
    from openpyxl import load_workbook

    client = make_client(tmp_path)
    seed_clean_summary_source()
    generated = client.post("/api/summary-schedules/generate", json={"date_from": DATE, "date_to": DATE})
    summary_id = generated.json()["summary"]["id"]

    response = client.get(f"/api/summary-schedules/{summary_id}/export.xlsx")

    assert response.status_code == 200, response.text
    wb = load_workbook(io.BytesIO(response.content))
    assert wb.sheetnames == [
        "Титульный лист", "Сводка", "По маршрутам", "По выходам", "По водителям",
        "По автобусам", "По времени", "Ошибки", "Исходные данные",
    ]
    assert wb["Титульный лист"]["A2"].value == "СВОДНОЕ РАСПИСАНИЕ"
    assert wb["По маршрутам"].freeze_panes == "A2"
    assert wb["По маршрутам"].auto_filter.ref is not None
    assert wb["По маршрутам"]["A1"].font.bold is True
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
python -m pytest tests/test_summary_schedule_api.py::test_summary_export_xlsx_has_required_sheets_and_format -q
```

Expected: FAIL because export endpoint is absent.

- [ ] **Step 3: Add `summary_schedule_xlsx_response()` in `app/xl.py`**

Use openpyxl `Workbook`, dark-blue header fill `17365D`, white bold header text, wrapped cells, borders, auto-width capped at 42, `freeze_panes = "A2"`, and `auto_filter.ref = ws.dimensions`.

Function signature and return contract:

```python
def summary_schedule_xlsx_response(settings, summary, views, lines, errors, filename="Сводное_расписание_за_период.xlsx"):
    wb = Workbook()
    ws = wb.active
    ws.title = "Титульный лист"
    ws["A2"] = "СВОДНОЕ РАСПИСАНИЕ"
    # Fill title sheet, create the eight data sheets listed below, style headers, and return a download response.
    return _xlsx_download_response(wb, filename)
```

Workbook sheets and source collections:

```python
sheets = [
    ("Титульный лист", None),
    ("Сводка", summary),
    ("По маршрутам", views["by_routes"]),
    ("По выходам", views["by_outputs"]),
    ("По водителям", views["by_drivers"]),
    ("По автобусам", views["by_buses"]),
    ("По времени", views["by_time"]),
    ("Ошибки", errors),
    ("Исходные данные", lines),
]
```

The title sheet must include organization name from `settings["org_name"]`, report title `СВОДНОЕ РАСПИСАНИЕ`, period, created date, route count, trip count, run count, bus count, driver count, error count, created user, and a signature line.

- [ ] **Step 4: Add export endpoint in `app/api_summary.py`**

```python
from .xl import summary_schedule_xlsx_response


@router.get("/{summary_id}/export.xlsx")
def summary_export(summary_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        summary, lines, errors = _load_summary(con, summary_id)
        views = _build_views(lines, errors)
        settings = db.get_settings(con)
        con.execute(
            "UPDATE summary_schedules SET status='выгружено', excel_file_path=?, updated_at=? WHERE id=?",
            ("Сводное_расписание_за_период.xlsx", datetime.datetime.now().isoformat(timespec="seconds"), summary_id),
        )
        con.commit()
        return summary_schedule_xlsx_response(settings, summary, views, lines, errors)
    finally:
        con.close()
```

- [ ] **Step 5: Run and commit**

Run:

```powershell
python -m pytest tests/test_summary_schedule_api.py::test_summary_export_xlsx_has_required_sheets_and_format -q
```

Expected: PASS.

Commit:

```powershell
git add app/xl.py app/api_summary.py tests/test_summary_schedule_api.py
git commit -m "feat: export summary schedule workbook"
```

---

### Task 5: Order Creation from Summary

**Files:**
- Modify: `tests/test_summary_schedule_api.py`
- Modify: `app/api_summary.py`

- [ ] **Step 1: Add failing order tests**

Append:

```python
def test_summary_order_creation_blocks_critical_errors(tmp_path):
    client = make_client(tmp_path)
    import app.db as db

    con = db.connect()
    try:
        route_id = con.execute("INSERT INTO routes(number,name,active,version) VALUES(?,?,?,?)", ("90", "Broken", 1, 1)).lastrowid
        con.execute(
            "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,trip_number,direction,dep_time,arr_time,distance_km) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (route_id, DAY_TYPE, 1, 1, 1, "прямое", "10:00", "10:30", 6.0),
        )
        con.commit()
    finally:
        con.close()

    generated = client.post("/api/summary-schedules/generate", json={"date_from": DATE, "date_to": DATE})
    summary_id = generated.json()["summary"]["id"]
    response = client.post(f"/api/summary-schedules/{summary_id}/order", json={"regenerate": True})
    assert response.status_code == 409
    assert "Критические" in response.text


def test_summary_order_creation_creates_order_lines(tmp_path):
    client = make_client(tmp_path)
    seed_clean_summary_source()
    generated = client.post("/api/summary-schedules/generate", json={"date_from": DATE, "date_to": DATE})
    summary_id = generated.json()["summary"]["id"]

    response = client.post(f"/api/summary-schedules/{summary_id}/order", json={"regenerate": True})

    assert response.status_code == 200, response.text
    assert response.json()["lines"] == 1
    order = client.get(f"/api/orders?date={DATE}")
    assert order.status_code == 200, order.text
    assert len(order.json()["lines"]) == 1
    assert order.json()["lines"][0]["route_number"] == "77"
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m pytest tests/test_summary_schedule_api.py::test_summary_order_creation_blocks_critical_errors tests/test_summary_schedule_api.py::test_summary_order_creation_creates_order_lines -q
```

Expected: FAIL because order endpoint is absent.

- [ ] **Step 3: Add order endpoint and helper**

Implement helper behavior:

- reject summaries with `period_start != period_end`;
- reject summaries that have `summary_schedule_errors.level='Критическая ошибка'`;
- use existing `orders` table;
- block existing order unless payload has `regenerate=true`;
- block approved, issued, or completed order unless payload has `force=true`;
- aggregate summary lines by `service_date`, `route_id`, `run_number`, `shift_number`, `driver_id`, `vehicle_id`;
- write one `order_lines` row per group;
- calculate planned fuel using `planned_fuel(bus, distance, date)`;
- update summary status to `передано в наряд`.

Endpoint:

```python
@router.post("/{summary_id}/order")
def summary_to_order(summary_id: int, payload: dict = Body(default={}), user=Depends(current_user)):
    require_write(user, "orders")
    con = db.connect()
    try:
        summary = db.one(con.execute("SELECT * FROM summary_schedules WHERE id=?", (summary_id,)))
        if not summary:
            raise HTTPException(404, "Сводное расписание не найдено")
        order_id, lines = _create_order_from_summary(con, summary, payload or {})
        con.execute(
            "UPDATE summary_schedules SET status='передано в наряд', updated_at=? WHERE id=?",
            (datetime.datetime.now().isoformat(timespec="seconds"), summary_id),
        )
        con.commit()
        return {"order_id": order_id, "lines": lines}
    finally:
        con.close()
```

- [ ] **Step 4: Run and commit**

Run:

```powershell
python -m pytest tests/test_summary_schedule_api.py::test_summary_order_creation_blocks_critical_errors tests/test_summary_schedule_api.py::test_summary_order_creation_creates_order_lines -q
```

Expected: PASS.

Commit:

```powershell
git add app/api_summary.py tests/test_summary_schedule_api.py
git commit -m "feat: create orders from summary schedules"
```

---

### Task 6: Frontend Summary Page

**Files:**
- Create: `tests/test_summary_schedule_ui.py`
- Modify: `static/app.js`
- Modify: `static/styles.css`

- [ ] **Step 1: Add failing UI presence test**

Create `tests/test_summary_schedule_ui.py`:

```python
# -*- coding: utf-8 -*-
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "app.js"


def test_summary_schedule_menu_and_actions_are_present():
    text = APP_JS.read_text(encoding="utf-8")
    assert '["summarySchedule", "Сводное расписание"]' in text
    assert "VIEWS.summarySchedule" in text
    assert "/api/summary-schedules/generate" in text
    assert "Сформировать сводное расписание" in text
    assert "Проверка ошибок" in text
    assert "Выгрузить в Excel" in text
    assert "Сформировать наряд" in text
```

- [ ] **Step 2: Run failing UI test**

Run:

```powershell
python -m pytest tests/test_summary_schedule_ui.py -q
```

Expected: FAIL because the page is absent.

- [ ] **Step 3: Add menu item in `static/app.js`**

In `NAV`, add near order and roster:

```javascript
["summarySchedule", "Сводное расписание"],
```

- [ ] **Step 4: Add summary view in `static/app.js`**

Add `SUMMARY_TABS`, `summaryState()`, `summaryTable()`, `summaryGenerate()`, `summaryRecheck()`, `summaryExport()`, `summaryOrder()`, and `VIEWS.summarySchedule`.

Required UI text:

```javascript
const SUMMARY_TABS = [
  ["by_routes", "По маршрутам"],
  ["by_outputs", "По выходам"],
  ["by_drivers", "По водителям"],
  ["by_buses", "По автобусам"],
  ["by_time", "По времени"],
  ["errors", "Проверка ошибок"],
  ["export", "Экспорт в Excel"],
  ["history", "История формирования"],
];
```

Required action endpoints:

```javascript
await api("/api/summary-schedules/generate", { method: "POST", body });
await api(`/api/summary-schedules/${st.selected_id}/check`, { method: "POST" });
openWin(`/api/summary-schedules/${st.selected_id}/export.xlsx`);
await api(`/api/summary-schedules/${st.selected_id}/order`, { method: "POST", body: { regenerate: true, force: true } });
```

Required buttons:

```html
Сформировать сводное расписание
Проверить ошибки
Сформировать наряд
Выгрузить в Excel
Обновить
Очистить фильтры
Сохранить версию
```

- [ ] **Step 5: Add CSS in `static/styles.css`**

```css
.summary-kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 10px;
  margin: 12px 0;
}
.summary-kpis > div {
  border: 1px solid #cbd8e6;
  border-radius: 8px;
  padding: 10px 12px;
  background: #fff;
}
.summary-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.summary-table th {
  background: #17365d;
  color: #fff;
  text-align: left;
  padding: 8px;
  position: sticky;
  top: 0;
}
.summary-table td {
  border: 1px solid #d8e2ee;
  padding: 6px 8px;
  vertical-align: top;
}
.sum-ok { background: #e8f7ef; }
.sum-info { background: #eaf3ff; }
.sum-warning, .sum-row-error { background: #fff3cd; }
.sum-error { background: #ffe5d0; }
.sum-critical { background: #f8d7da; }
```

- [ ] **Step 6: Run and commit**

Run:

```powershell
python -m pytest tests/test_summary_schedule_ui.py -q
```

Expected: PASS.

Commit:

```powershell
git add static/app.js static/styles.css tests/test_summary_schedule_ui.py
git commit -m "feat: add summary schedule interface"
```

---

### Task 7: README and Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add README section**

Append:

```markdown
## Сводное расписание

Раздел `Сводное расписание` собирает данные из созданных расписаний маршрутов, графика водителей и справочников автобусов в единую диспетчерскую версию.

Порядок работы:

1. Откройте `Сводное расписание` в главном меню.
2. Выберите дату или период.
3. При необходимости выберите маршрут, либо оставьте `Все маршруты`.
4. Нажмите `Сформировать сводное расписание`.
5. Проверьте вкладку `Проверка ошибок`.
6. Используйте фильтры `только ошибки`, `без водителя`, `без автобуса`.
7. Нажмите `Выгрузить в Excel`, чтобы получить файл с титульным листом, сводкой и таблицами по маршрутам, выходам, водителям, автобусам, времени, ошибкам и исходным данным.
8. Для одной даты нажмите `Сформировать наряд`, если критических ошибок нет.

Предупреждения по Приказу Минтранса РФ № 424 отображаются как предупреждения и не блокируют работу. Формирование наряда блокируется только при критических ошибках.
```

- [ ] **Step 2: Run focused tests**

Run:

```powershell
python -m pytest tests/test_summary_schedule_api.py tests/test_summary_schedule_ui.py tests/test_schedule_api.py tests/test_roster_multi_shift_api.py tests/test_424_advisory_api.py tests/test_order_excel_export.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run:

```powershell
python -m pytest -q
```

Expected: PASS.

- [ ] **Step 4: Commit README**

```powershell
git add README.md
git commit -m "docs: describe summary schedule workflow"
```

---

### Task 8: Manual Browser Verification

**Files:**
- No source changes.

- [ ] **Step 1: Start the server**

Run from `G:\Мой диск\сайт обработки и аналитики\ATP_servis_v2\atp-system`:

```powershell
python run.py
```

Expected: local app opens on the configured port, commonly `http://127.0.0.1:8001/`.

- [ ] **Step 2: Open summary page**

Open:

```text
http://127.0.0.1:8001/#/summarySchedule
```

Check:

- menu contains `Сводное расписание`;
- filters are visible at the top;
- all required buttons are visible;
- all required tabs are visible;
- generation fills KPI cards and tables;
- rows with problems are highlighted;
- Excel file contains nine sheets;
- clean one-day summary can create an order;
- critical errors block order creation and show the reason.

- [ ] **Step 3: Check git status**

Run:

```powershell
git status --short
```

Expected: no unstaged files from this implementation after commits. Existing unrelated local files may remain visible and must not be reverted.

---

## Spec Coverage Check

- Menu section `Сводное расписание`: Task 6.
- Date and period generation: Task 2.
- Source data from schedules, roster, drivers, buses: Task 2.
- Views by routes, outputs, drivers, buses, time: Tasks 2 and 6.
- Filters without full page reload for loaded data: Task 6.
- Error checks and highlighted rows: Tasks 3 and 6.
- Excel export with title, summary, core tables, source data, and errors: Task 4.
- Order creation from saved summary: Task 5.
- History: Tasks 1, 2, and 6.
- 424 warnings only: Task 3.
- README update: Task 7.
- Existing functionality regression: Task 7.
