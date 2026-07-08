# Schedule Constructor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first release of the schedule module as an operational constructor with summary cards, better checks, bulk actions, a timeline view, and Excel export.

**Architecture:** Keep the existing FastAPI + SQLite + plain SPA architecture. Extend `app/api_planning.py` around the existing `route_trips` model, then replace the current `VIEWS.schedule` rendering with focused helper functions in `static/app.js`. Add CSS only for the schedule constructor components.

**Tech Stack:** FastAPI, SQLite, openpyxl, pytest, FastAPI TestClient, vanilla JavaScript, plain CSS.

**Repository Note:** `G:\Мой диск\сайт обработки и аналитики\ATP_servis_v2\atp-system` is not a git repository. Do not run commit steps. At the end of each task, record changed files and run the listed verification commands.

---

## File Structure

- Modify `requirements.txt`: add `pytest` for backend tests.
- Create `tests/test_schedule_api.py`: API tests for generation, summary, checks, bulk shift, renumber, and export.
- Modify `app/api_planning.py`: schedule summary helpers, expanded checks, generation options, bulk shift, renumber, export.
- Modify `static/app.js`: redesigned `VIEWS.schedule`, schedule helper renderers, bulk action handlers, export handler.
- Modify `static/styles.css`: schedule constructor layout, timeline, status row highlighting.

No database migration is needed for the first release.

---

### Task 1: Add Backend Test Harness

**Files:**
- Modify: `requirements.txt`
- Create: `tests/test_schedule_api.py`

- [ ] **Step 1: Add pytest dependency**

Add this line to `requirements.txt` if it is not present:

```text
pytest>=8.0
```

- [ ] **Step 2: Write the failing API tests**

Create `tests/test_schedule_api.py` with this content:

```python
# -*- coding: utf-8 -*-
import io

from fastapi.testclient import TestClient
from openpyxl import load_workbook


def make_client(tmp_path, monkeypatch):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "atp-test.db")
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


def create_route(client):
    payload = {
        "number": "12",
        "name": "Центр - Вокзал",
        "comm_type": "городское",
        "start_point": "Центр",
        "end_point": "Вокзал",
        "length_km": 11.5,
        "length_back_km": 10.8,
        "trip_time_min": 35,
        "trip_time_back_min": 38,
        "interval_min": 12,
        "outputs_count": 3,
        "bus_types": "большой",
        "work_days": "ежедневно",
        "version": 1,
        "active": 1,
    }
    response = client.post("/api/refs/routes", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def add_trip(client, route_id, **overrides):
    payload = {
        "route_id": route_id,
        "day_type": "будни",
        "output_number": 1,
        "shift_number": 1,
        "trip_number": 1,
        "direction": "прямое",
        "dep_time": "06:00",
        "arr_time": "06:35",
        "distance_km": 11.5,
        "break_after_min": 6,
        "break_type": "",
    }
    payload.update(overrides)
    response = client.post("/api/trips", json=payload)
    assert response.status_code == 200, response.text
    return payload


def test_generate_schedule_returns_summary(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    route_id = create_route(client)

    response = client.post("/api/trips/generate", json={
        "route_id": route_id,
        "day_type": "будни",
        "outputs": 3,
        "first_dep": "05:30",
        "last_dep": "08:30",
        "trip_time": 35,
        "trip_time_back": 38,
        "interval": 12,
        "rest_min": 6,
        "lunch_min": 40,
        "distance": 11.5,
        "distance_back": 10.8,
        "mode": "interval",
    })
    assert response.status_code == 200, response.text
    assert response.json()["trips"] > 0

    summary = client.get(f"/api/routes/{route_id}/schedule-summary?day_type=будни")
    assert summary.status_code == 200, summary.text
    data = summary.json()
    assert data["trips_count"] > 0
    assert data["outputs_count"] == 3
    assert data["bus_need"] == 3
    assert data["driver_need"] >= 3
    assert data["distance_km"] > 0
    assert data["first_dep"] == "05:30"


def test_route_check_reports_overlap_and_recommendation(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    route_id = create_route(client)
    add_trip(client, route_id, trip_number=1, dep_time="06:00", arr_time="06:40")
    add_trip(client, route_id, trip_number=2, dep_time="06:30", arr_time="07:10")

    response = client.get(f"/api/routes/{route_id}/check?day_type=будни")
    assert response.status_code == 200, response.text
    problems = response.json()["problems"]
    assert any(p["severity"] == "критично" and p["kind"] == "overlap" for p in problems)
    assert all("recommendation" in p for p in problems)


def test_route_check_reports_duplicate_trip_number(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    route_id = create_route(client)
    add_trip(client, route_id, trip_number=1, dep_time="06:00", arr_time="06:35")
    add_trip(client, route_id, trip_number=1, dep_time="06:45", arr_time="07:20")

    response = client.get(f"/api/routes/{route_id}/check?day_type=будни")
    assert response.status_code == 200, response.text
    assert any(p["kind"] == "duplicate_trip_number" for p in response.json()["problems"])


def test_bulk_shift_moves_trip_times(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    route_id = create_route(client)
    add_trip(client, route_id, dep_time="06:00", arr_time="06:35")

    response = client.post("/api/trips/bulk-shift", json={
        "route_id": route_id,
        "day_type": "будни",
        "minutes": 15,
    })
    assert response.status_code == 200, response.text
    assert response.json()["updated"] == 1

    trips = client.get(f"/api/trips?route_id={route_id}&day_type=будни").json()["items"]
    assert trips[0]["dep_time"] == "06:15"
    assert trips[0]["arr_time"] == "06:50"


def test_renumber_orders_trips_inside_each_output(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    route_id = create_route(client)
    add_trip(client, route_id, output_number=1, trip_number=9, dep_time="07:00", arr_time="07:35")
    add_trip(client, route_id, output_number=1, trip_number=8, dep_time="06:00", arr_time="06:35")

    response = client.post("/api/trips/renumber", json={"route_id": route_id, "day_type": "будни"})
    assert response.status_code == 200, response.text
    assert response.json()["updated"] == 2

    trips = client.get(f"/api/trips?route_id={route_id}&day_type=будни").json()["items"]
    assert [t["trip_number"] for t in trips] == [1, 2]
    assert [t["dep_time"] for t in trips] == ["06:00", "07:00"]


def test_schedule_export_xlsx(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    route_id = create_route(client)
    add_trip(client, route_id, dep_time="06:00", arr_time="06:35")

    response = client.get(f"/api/routes/{route_id}/schedule-export.xlsx?day_type=будни")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    wb = load_workbook(io.BytesIO(response.content), read_only=True)
    ws = wb.active
    values = list(ws.iter_rows(values_only=True))
    assert values[0][0].startswith("Расписание маршрута")
    assert values[1][:6] == ("Маршрут", "Тип дня", "Выход", "Смена", "Рейс", "Направление")
    assert values[2][4] == 1
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```powershell
python -m pytest tests/test_schedule_api.py -q
```

Expected now: failures for missing endpoints such as `/api/routes/{rid}/schedule-summary`, `/api/trips/bulk-shift`, `/api/trips/renumber`, `/api/routes/{rid}/schedule-export.xlsx`, and missing structured fields like `kind` or `recommendation`.

---

### Task 2: Implement Schedule Summary and Structured Checks

**Files:**
- Modify: `app/api_planning.py`
- Test: `tests/test_schedule_api.py`

- [ ] **Step 1: Add helper functions near `outputs_summary`**

Add these functions before `@router.get("/routes/{rid}/outputs")`:

```python
def _trip_duration(t):
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
                "ошибка", "missing_fields",
                f"Рейс {t.get('trip_number') or t['id']}: не заполнены поля {', '.join(missing)}",
                t["output_number"], t["id"], t.get("trip_number"),
                "Откройте рейс и заполните обязательные поля."))
        elif _trip_duration(t) <= 0:
            problems.append(_problem(
                "ошибка", "invalid_arrival",
                f"Рейс {t.get('trip_number')}: прибытие не позже отправления",
                t["output_number"], t["id"], t.get("trip_number"),
                "Проверьте время прибытия или оформите рейс через полночь отдельным правилом."))

    for on, ts in by_out.items():
        seen_numbers = {}
        for t in ts:
            tn = t.get("trip_number")
            if tn:
                seen_numbers.setdefault(tn, []).append(t)
        for tn, dupes in seen_numbers.items():
            if len(dupes) > 1:
                problems.append(_problem(
                    "предупреждение", "duplicate_trip_number",
                    f"Выход {on}: номер рейса {tn} используется {len(dupes)} раза",
                    on, dupes[0]["id"], tn,
                    "Запустите перенумерацию рейсов или исправьте номера вручную."))
        for a, b in zip(ts, ts[1:]):
            if not a.get("arr_time") or not b.get("dep_time"):
                continue
            gap = N.tmin(b["dep_time"]) - N.tmin(a["arr_time"])
            if gap < 0:
                problems.append(_problem(
                    "критично", "overlap",
                    f"Выход {on}: рейс {b['trip_number']} ({b['dep_time']}) начинается до прибытия рейса {a['trip_number']} ({a['arr_time']})",
                    on, b["id"], b.get("trip_number"),
                    "Сдвиньте отправление, перенесите рейс на другой выход или увеличьте количество выходов."))
            elif gap < 3:
                problems.append(_problem(
                    "предупреждение", "short_rest",
                    f"Выход {on}: между рейсами {a['trip_number']} и {b['trip_number']} менее 3 минут отстоя",
                    on, b["id"], b.get("trip_number"),
                    "Увеличьте межрейсовый отстой минимум до 3 минут."))
        if ts and ts[0].get("dep_time") and ts[-1].get("arr_time"):
            total_min = N.shift_minutes(ts[0]["dep_time"], ts[-1]["arr_time"])
            if total_min / 60.0 > float(nrm["max_shift_hours_summed"]) and len({t["shift_number"] for t in ts}) < 2:
                problems.append(_problem(
                    "ошибка", "long_output_without_shift_split",
                    f"Выход {on}: продолжительность {round(total_min / 60, 1)} ч без деления на смены",
                    on, ts[-1]["id"], ts[-1].get("trip_number"),
                    "Разделите выход на две смены или назначьте пересменку."))
            if not any((t["break_after_min"] or 0) >= int(nrm["break_min_minutes"]) for t in ts) and total_min > 300:
                problems.append(_problem(
                    "ошибка", "missing_lunch",
                    f"Выход {on}: нет перерыва не менее {nrm['break_min_minutes']} мин",
                    on, ts[0]["id"], ts[0].get("trip_number"),
                    "Добавьте обед, пересменку или технологический перерыв."))

    if route and route["interval_min"] and len(trips) > 1:
        starts = sorted([N.tmin(t["dep_time"]) for t in trips if t.get("dep_time") is not None])
        expected = int(route["interval_min"])
        for prev, cur in zip(starts, starts[1:]):
            gap = cur - prev
            if gap > expected * 2:
                problems.append(_problem(
                    "предупреждение", "large_interval_gap",
                    f"Между отправлениями {N.tstr(prev)} и {N.tstr(cur)} интервал {gap} мин при нормативе {expected} мин",
                    recommendation="Проверьте равномерность выпуска или добавьте рейс."))
    return problems

def schedule_summary(con, route_id, day_type):
    trips = db.rows(con.execute(
        "SELECT * FROM route_trips WHERE route_id=? AND day_type=? ORDER BY output_number, dep_time, id",
        (route_id, day_type)))
    outputs = outputs_summary(con, route_id, day_type)
    problems = schedule_problems(con, route_id, day_type)
    counts = {"критично": 0, "ошибка": 0, "предупреждение": 0}
    for p in problems:
        counts[p["severity"]] = counts.get(p["severity"], 0) + 1
    first_dep = min([t["dep_time"] for t in trips if t.get("dep_time")], default="")
    last_arr = max([t["arr_time"] for t in trips if t.get("arr_time")], default="")
    distance = round(sum((t["distance_km"] or 0) for t in trips), 1)
    return {
        "route_id": route_id,
        "day_type": day_type,
        "trips_count": len(trips),
        "outputs_count": len({t["output_number"] for t in trips}),
        "shift_count": len(outputs),
        "bus_need": len({t["output_number"] for t in trips}),
        "driver_need": len(outputs),
        "distance_km": distance,
        "first_dep": first_dep,
        "last_arr": last_arr,
        "problems_count": len(problems),
        "critical_count": counts.get("критично", 0),
        "error_count": counts.get("ошибка", 0),
        "warning_count": counts.get("предупреждение", 0),
    }
```

- [ ] **Step 2: Replace `route_check` body**

Keep the decorator and signature, but make the endpoint return structured problems:

```python
@router.get("/routes/{rid}/check")
def route_check(rid: int, day_type: str = "будни", user=Depends(current_user)):
    con = db.connect()
    try:
        return {"problems": schedule_problems(con, rid, day_type), "outputs": outputs_summary(con, rid, day_type)}
    finally:
        con.close()
```

- [ ] **Step 3: Add schedule summary endpoint**

Add after `route_check`:

```python
@router.get("/routes/{rid}/schedule-summary")
def route_schedule_summary(rid: int, day_type: str = "будни", user=Depends(current_user)):
    con = db.connect()
    try:
        route = db.one(con.execute("SELECT id FROM routes WHERE id=?", (rid,)))
        if not route:
            raise HTTPException(404, "Маршрут не найден")
        return schedule_summary(con, rid, day_type)
    finally:
        con.close()
```

- [ ] **Step 4: Run targeted tests**

Run:

```powershell
python -m pytest tests/test_schedule_api.py::test_generate_schedule_returns_summary tests/test_schedule_api.py::test_route_check_reports_overlap_and_recommendation tests/test_schedule_api.py::test_route_check_reports_duplicate_trip_number -q
```

Expected: the three tests pass. Other tests may still fail because bulk operations and export are not implemented yet.

---

### Task 3: Implement Generation Options, Bulk Shift, Renumber, and Export

**Files:**
- Modify: `app/api_planning.py`
- Test: `tests/test_schedule_api.py`

- [ ] **Step 1: Extend `trips_generate` parameter handling**

Inside `trips_generate`, replace the current `interval` and distance block with:

```python
        dist = float(payload.get("distance", route["length_km"] or 10))
        dist_back = float(payload.get("distance_back") or route["length_back_km"] or 0) or dist
        mode = payload.get("mode", "interval")
        if mode == "outputs":
            cycle = trip_time + trip_time_back + rest * 2
            interval = max(5, cycle // max(1, outputs))
        else:
            interval = int(payload.get("interval", route["interval_min"] or max(5, (trip_time + rest) // max(1, outputs))))
```

Keep the existing insert loop, but make sure reverse trips use `dist_back`, which the current code already does.

- [ ] **Step 2: Add bulk shift endpoint**

Add after `trip_delete`:

```python
@router.post("/trips/bulk-shift")
def trips_bulk_shift(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "trips")
    con = db.connect()
    try:
        rid = int(payload["route_id"])
        day_type = payload.get("day_type", "будни")
        minutes = int(payload.get("minutes") or 0)
        if minutes == 0:
            raise HTTPException(400, "Сдвиг должен быть отличен от 0")
        if abs(minutes) > 720:
            raise HTTPException(400, "Сдвиг не может быть больше 12 часов")
        output_number = int(payload.get("output_number") or 0)
        q = "SELECT * FROM route_trips WHERE route_id=? AND day_type=?"
        args = [rid, day_type]
        if output_number:
            q += " AND output_number=?"
            args.append(output_number)
        items = db.rows(con.execute(q, args))
        for t in items:
            con.execute(
                "UPDATE route_trips SET dep_time=?, arr_time=? WHERE id=?",
                (N.tstr(N.tmin(t["dep_time"]) + minutes), N.tstr(N.tmin(t["arr_time"]) + minutes), t["id"]))
        db.audit(con, user["username"], "массовый сдвиг расписания", "route_trips", rid,
                 comment=f"{day_type}, выход {output_number or 'все'}, {minutes} мин")
        con.commit()
        return {"updated": len(items)}
    finally:
        con.close()
```

- [ ] **Step 3: Add renumber endpoint**

Add after `trips_bulk_shift`:

```python
@router.post("/trips/renumber")
def trips_renumber(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "trips")
    con = db.connect()
    try:
        rid = int(payload["route_id"])
        day_type = payload.get("day_type", "будни")
        output_number = int(payload.get("output_number") or 0)
        q = ("SELECT id, output_number FROM route_trips WHERE route_id=? AND day_type=?")
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
        db.audit(con, user["username"], "перенумерация рейсов", "route_trips", rid,
                 comment=f"{day_type}, выход {output_number or 'все'}, рейсов {updated}")
        con.commit()
        return {"updated": updated}
    finally:
        con.close()
```

- [ ] **Step 4: Add Excel export endpoint**

Add after `route_schedule_summary`:

```python
@router.get("/routes/{rid}/schedule-export.xlsx")
def route_schedule_export(rid: int, day_type: str = "будни", user=Depends(current_user)):
    con = db.connect()
    try:
        route = db.one(con.execute("SELECT * FROM routes WHERE id=?", (rid,)))
        if not route:
            raise HTTPException(404, "Маршрут не найден")
        trips = db.rows(con.execute(
            "SELECT * FROM route_trips WHERE route_id=? AND day_type=? ORDER BY output_number, dep_time, id",
            (rid, day_type)))
        title = f"Расписание маршрута № {route['number']} ({day_type})"
        headers = ["Маршрут", "Тип дня", "Выход", "Смена", "Рейс", "Направление",
                   "Отправление", "Прибытие", "Длительность, мин", "Пробег, км",
                   "Отстой, мин", "Тип перерыва"]
        rows_ = [[
            route["number"], day_type, t["output_number"], t["shift_number"], t["trip_number"],
            t["direction"], t["dep_time"], t["arr_time"], _trip_duration(t),
            t["distance_km"], t["break_after_min"] or 0, t["break_type"] or ""
        ] for t in trips]
        return xlsx_response(title, headers, rows_, filename=f"schedule_route_{route['number']}_{day_type}.xlsx")
    finally:
        con.close()
```

- [ ] **Step 5: Run full backend tests**

Run:

```powershell
python -m pytest tests/test_schedule_api.py -q
```

Expected: all tests in `tests/test_schedule_api.py` pass.

---

### Task 4: Redesign Schedule Frontend

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Add schedule helper functions before `VIEWS.schedule`**

Insert above `/* ================= РАСПИСАНИЯ ================= */`:

```javascript
function scheduleStatusBadge(s) {
  if (!s || !s.trips_count) return `<span class="badge b-mut">расписание пустое</span>`;
  if (s.critical_count) return `<span class="badge b-err">критично: ${s.critical_count}</span>`;
  if (s.error_count) return `<span class="badge b-err">ошибки: ${s.error_count}</span>`;
  if (s.warning_count) return `<span class="badge b-warn">предупреждения: ${s.warning_count}</span>`;
  return `<span class="badge b-ok">расписание корректно</span>`;
}

function scheduleCards(s) {
  const card = (n, l, cls) => `<div class="card ${cls || ""}"><div class="num">${esc(n)}</div><div class="lbl">${esc(l)}</div></div>`;
  return `<div class="cards schedule-kpis">
    ${card(s.trips_count || 0, "рейсов")}
    ${card(s.outputs_count || 0, "выходов")}
    ${card(s.bus_need || 0, "автобусов требуется")}
    ${card(s.driver_need || 0, "водителей требуется")}
    ${card((s.distance_km || 0) + " км", "плановый пробег")}
    ${card((s.first_dep || "—") + "–" + (s.last_arr || "—"), "период движения")}
    ${card(s.problems_count || 0, "замечаний", s.critical_count || s.error_count ? "err" : s.warning_count ? "warn" : "ok")}
  </div>`;
}

function tripProblemMap(problems) {
  const out = {};
  (problems || []).forEach(p => {
    if (p.trip_id) out[p.trip_id] = p;
  });
  return out;
}

function scheduleTimeline(trips, problems) {
  if (!trips.length) return `<div class="panel muted">Нет рейсов для временной шкалы.</div>`;
  const bad = tripProblemMap(problems);
  const byOut = {};
  trips.forEach(t => { (byOut[t.output_number] = byOut[t.output_number] || []).push(t); });
  const toMin = (v) => { const p = String(v || "00:00").split(":"); return (+p[0]) * 60 + (+p[1]); };
  const minT = Math.min(...trips.map(t => toMin(t.dep_time)));
  const maxT = Math.max(...trips.map(t => toMin(t.arr_time)));
  const span = Math.max(60, maxT - minT);
  const hours = [];
  for (let h = Math.floor(minT / 60); h <= Math.ceil(maxT / 60); h++) hours.push(h % 24);
  return `<div class="timeline">
    <div class="timeline-scale"><span></span>${hours.map(h => `<b>${String(h).padStart(2, "0")}:00</b>`).join("")}</div>
    ${Object.entries(byOut).map(([out, list]) => `<div class="timeline-row">
      <div class="timeline-label">Вых. ${esc(out)}</div>
      <div class="timeline-track">${list.map(t => {
        const left = Math.max(0, (toMin(t.dep_time) - minT) / span * 100);
        const width = Math.max(4, (toMin(t.arr_time) - toMin(t.dep_time)) / span * 100);
        const cls = bad[t.id] ? " bad" : t.break_type ? " lunch" : "";
        return `<button class="timeline-trip${cls}" style="left:${left}%;width:${width}%"
          title="Рейс ${esc(t.trip_number)} ${esc(t.dep_time)}–${esc(t.arr_time)}"
          onclick='tripEdit(${JSON.stringify(t).replace(/'/g, "&#39;")})'>
          ${esc(t.trip_number)} · ${esc(t.dep_time)}
        </button>`;
      }).join("")}</div>
    </div>`).join("")}
  </div>`;
}
```

- [ ] **Step 2: Replace `VIEWS.schedule` with the constructor layout**

Replace the current `VIEWS.schedule` function with:

```javascript
VIEWS.schedule = async function () {
  const st = window._sched || { route_id: REFS.routes[0] ? REFS.routes[0].id : 0, day_type: "будни", q: "" };
  window._sched = st;
  if (!st.route_id) { $("content").innerHTML = "<div class='panel'>Сначала создайте маршрут в справочнике.</div>"; return; }
  const [tr, chk, sum] = await Promise.all([
    api(`/api/trips?route_id=${st.route_id}&day_type=${st.day_type}`),
    api(`/api/routes/${st.route_id}/check?day_type=${st.day_type}`),
    api(`/api/routes/${st.route_id}/schedule-summary?day_type=${st.day_type}`)]);
  const q = (st.q || "").toLowerCase();
  const problemsByTrip = tripProblemMap(chk.problems);
  const visibleTrips = tr.items.filter(t => !q || JSON.stringify(t).toLowerCase().includes(q));
  const outs = chk.outputs.map(o => `<tr><td>${o.output_number}</td><td>${o.shift_number}</td><td>${o.start}–${o.end}</td>
    <td>${o.trips}</td><td>${o.distance}</td><td>${o.hours}</td><td>${o.night_hours}</td></tr>`).join("");
  const trips = visibleTrips.map(t => {
    const p = problemsByTrip[t.id];
    const cls = p ? (p.severity === "предупреждение" ? "trip-row-warning" : "trip-row-error") : "";
    return `<tr class="${cls}">
      <td>${t.output_number}</td><td>${t.shift_number}</td><td>${t.trip_number}</td><td>${esc(t.direction)}</td>
      <td>${t.dep_time}</td><td>${t.arr_time}</td><td>${t.distance_km}</td>
      <td>${t.break_after_min || 0}${t.break_type ? " (" + esc(t.break_type) + ")" : ""}</td>
      <td>${p ? sevBadge(p.severity) + " " + esc(p.kind) : '<span class="badge b-ok">ok</span>'}</td>
      <td><button class="btn small ghost" onclick='tripEdit(${JSON.stringify(t).replace(/'/g, "&#39;")})'>изм.</button>
          <button class="btn small ghost" onclick="tripDel(${t.id})">✕</button></td></tr>`;
  }).join("");
  $("content").innerHTML = `<div class="schedule-hero">
      <div class="toolbar">
        <select onchange="_sched.route_id=+this.value; route()">
          ${REFS.routes.map(r => `<option value="${r.id}" ${r.id === st.route_id ? "selected" : ""}>№ ${esc(r.number)} — ${esc(r.name || "")} (${esc(r.comm_type)})</option>`).join("")}
        </select>
        <div class="tabs" style="margin:0; border:none">
          ${["будни", "суббота", "воскресенье"].map(t => `<button class="${st.day_type === t ? "on" : ""}" onclick="_sched.day_type='${t}'; route()">${t}</button>`).join("")}
        </div>
        ${scheduleStatusBadge(sum)}
        <input placeholder="поиск по рейсам…" value="${esc(st.q || "")}" onchange="_sched.q=this.value; route()">
      </div>
      <div class="toolbar">
        <button class="btn" onclick="schedGen()">Сгенерировать расписание</button>
        <button class="btn sec" onclick="tripEdit({route_id:${st.route_id}, day_type:'${st.day_type}', output_number:1, shift_number:1, direction:'прямое'})">+ рейс</button>
        <button class="btn sec" onclick="schedBulkShift()">Сдвинуть время</button>
        <button class="btn sec" onclick="schedRenumber()">Перенумеровать</button>
        <button class="btn sec" onclick="schedExport()">Excel</button>
      </div>
    </div>
    ${scheduleCards(sum)}
    <div class="schedule-layout">
      <div>
        <div class="panel"><h3>Рейсы (${visibleTrips.length}/${tr.items.length})</h3>${tbl(["Выход", "Смена", "№", "Направление", "Отпр.", "Приб.", "Км", "Отстой", "Проверка", ""], trips)}</div>
        <div class="panel"><h3>Временная шкала</h3>${scheduleTimeline(tr.items, chk.problems)}</div>
      </div>
      <div>
        <div class="panel"><h3>Ошибки и рекомендации</h3>${chk.problems.length ? chk.problems.map(p =>
          `<div class="vio ${p.severity === "предупреждение" ? "w" : ""}"><b>${sevBadge(p.severity)} ${esc(p.kind)}</b>${esc(p.message)}<br><span class="muted">${esc(p.recommendation || "")}</span></div>`).join("") : '<span class="badge b-ok">Замечаний не найдено</span>'}</div>
        <div class="panel"><h3>Выходы и смены</h3>${tbl(["Выход", "Смена", "Время", "Рейсов", "Км", "Часы", "Ночные"], outs)}</div>
      </div>
    </div>`;
};
```

- [ ] **Step 3: Update `schedGen` modal fields**

In `schedGen`, add `mode` and `distance_back` fields:

```javascript
    { k: "mode", label: "Режим генерации", type: "select", options: [["interval", "по интервалу"], ["outputs", "по количеству выходов"]] },
    { k: "distance_back", label: "Пробег обратного рейса, км", type: "number", step: "0.1", def: r.length_back_km || r.length_km || 10 },
```

Keep numeric conversion, but exclude `mode` from conversion:

```javascript
  Object.keys(v).forEach(k => { if (!["first_dep", "last_dep", "mode"].includes(k)) v[k] = +v[k]; });
```

- [ ] **Step 4: Add bulk action handlers after `schedGen`**

```javascript
async function schedBulkShift() {
  const st = window._sched;
  const v = await formModal("Массовый сдвиг рейсов", [
    { k: "minutes", label: "Сдвиг, минут (+ позже / - раньше)", type: "number", def: 5 },
    { k: "output_number", label: "Только выход (0 — все)", type: "number", def: 0 }]);
  if (!v) return;
  v.route_id = st.route_id; v.day_type = st.day_type;
  v.minutes = +v.minutes; v.output_number = +v.output_number || 0;
  try {
    const r = await api("/api/trips/bulk-shift", { method: "POST", body: v });
    toast(`Сдвинуто рейсов: ${r.updated}`);
    route();
  } catch (e) { toast(e.message, true); }
}

async function schedRenumber() {
  const st = window._sched;
  try {
    const r = await api("/api/trips/renumber", { method: "POST", body: { route_id: st.route_id, day_type: st.day_type } });
    toast(`Перенумеровано рейсов: ${r.updated}`);
    route();
  } catch (e) { toast(e.message, true); }
}

function schedExport() {
  const st = window._sched;
  openWin(`/api/routes/${st.route_id}/schedule-export.xlsx?day_type=${encodeURIComponent(st.day_type)}`);
}
```

- [ ] **Step 5: Run backend tests after frontend edit**

Run:

```powershell
python -m pytest tests/test_schedule_api.py -q
```

Expected: all backend tests still pass.

---

### Task 5: Add Schedule CSS

**Files:**
- Modify: `static/styles.css`

- [ ] **Step 1: Add schedule styles near the existing table/button styles**

Append before the `@media` block:

```css
.schedule-hero { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; margin-bottom: 12px; }
.schedule-kpis { grid-template-columns: repeat(auto-fill, minmax(145px, 1fr)); }
.schedule-layout { display: grid; grid-template-columns: minmax(0, 1.8fr) minmax(330px, .8fr); gap: 14px; align-items: start; }
.trip-row-error td { background: #fff1f2; }
.trip-row-warning td { background: #fff8ed; }
.timeline { overflow-x: auto; padding-bottom: 4px; }
.timeline-scale { display: grid; grid-template-columns: 82px repeat(12, minmax(72px, 1fr)); gap: 6px; color: var(--muted); font-size: 11px; margin-bottom: 8px; min-width: 760px; }
.timeline-row { display: grid; grid-template-columns: 82px 1fr; gap: 8px; align-items: center; margin-bottom: 8px; min-width: 760px; }
.timeline-label { color: var(--muted); font-size: 12px; font-weight: 600; }
.timeline-track { position: relative; height: 34px; border: 1px solid var(--line); border-radius: 8px; background: #f8fbff; overflow: hidden; }
.timeline-trip { position: absolute; top: 4px; height: 24px; border: 0; border-radius: 6px; background: #dbeafe; color: #1e40af; font-size: 11px; cursor: pointer; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.timeline-trip:hover { filter: brightness(.96); }
.timeline-trip.bad { background: #fee2e2; color: #991b1b; }
.timeline-trip.lunch { background: #dcf3e3; color: #14622b; }
@media (max-width: 1100px) { .schedule-layout { grid-template-columns: 1fr; } }
```

- [ ] **Step 2: Run a syntax smoke check**

Run:

```powershell
python -m pytest tests/test_schedule_api.py -q
```

Expected: backend tests still pass. CSS has no automated test in this project.

---

### Task 6: Manual Browser Verification

**Files:**
- Read/verify: `static/app.js`, `static/styles.css`, `app/api_planning.py`

- [ ] **Step 1: Start the app**

Run:

```powershell
python run.py --demo --port 8000
```

Expected: terminal prints `АТП-система: http://127.0.0.1:8000`.

- [ ] **Step 2: Open the app and sign in**

Open:

```text
http://127.0.0.1:8000
```

Use:

```text
admin / admin
```

Expected: dashboard loads without JavaScript errors.

- [ ] **Step 3: Verify schedule constructor**

In the app:

1. Open `Расписания маршрутов`.
2. Select any route.
3. Click `Сгенерировать расписание`.
4. Use mode `по интервалу`, keep default values, save.
5. Confirm summary cards show non-zero рейсы, выходы, автобусы, водители, пробег.
6. Confirm the рейсы table is visible.
7. Confirm the временная шкала shows рейс blocks.
8. Confirm `Ошибки и рекомендации` is either green or shows readable recommendations.

- [ ] **Step 4: Verify bulk actions**

In the same screen:

1. Click `Сдвинуть время`.
2. Enter `5` minutes and output `0`.
3. Confirm рейсы moved by 5 minutes.
4. Click `Перенумеровать`.
5. Confirm рейсы are numbered from 1 within each output.

- [ ] **Step 5: Verify Excel export**

Click `Excel`.

Expected:

- browser downloads an `.xlsx` file;
- workbook opens;
- first data row contains route number, day type, output, shift, trip number, direction, departure, arrival.

---

## Final Verification

Run:

```powershell
python -m pytest tests/test_schedule_api.py -q
```

Expected:

```text
6 passed
```

Then perform Task 6 manual verification. Report changed files:

- `requirements.txt`
- `tests/test_schedule_api.py`
- `app/api_planning.py`
- `static/app.js`
- `static/styles.css`
