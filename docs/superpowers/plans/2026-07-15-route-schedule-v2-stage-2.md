# Route Schedule V2 Stage 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add route/day periods, interval transitions, reusable templates, departure previews, and per-period bus-demand calculations without replacing the existing `route_trips` compatibility model.

**Architecture:** Extend the Stage 1 route schema with period and template tables, keep validation and calculations in a pure `route_periods.py` domain module, and expose focused APIs from a new router. All generated departures are previews in Stage 2; writing stop-level trips remains Stage 3, so existing orders, rosters, waybills, and manual trip editing continue to use `route_trips` unchanged.

**Tech Stack:** Python 3.14, FastAPI, SQLite, vanilla JavaScript SPA, pytest.

---

## Scope and file map

- Create `app/route_periods.py`: time parsing, full-set validation, abrupt/smooth departure generation, cycle time, bus demand, and warnings.
- Create `app/api_route_periods.py`: periods, templates, and preview endpoints with audit.
- Modify `app/route_schema.py`: `day_periods`, `period_templates`, `period_template_items`, and `period_previews`.
- Modify `app/main.py`: include the Stage 2 router.
- Modify `static/route-card.js`: add the «Периоды дня» tab and full-set editor.
- Modify `static/app.js`: add a non-destructive period preview to «Расписания маршрутов».
- Modify `static/styles.css` and `static/index.html`: period timeline, demand cards, diff/preview states, and cache key.
- Create focused schema, domain, API, template, preview, and frontend tests.

Stage 2 does not insert, update, or delete `route_trips` during automatic period calculations. The existing `/api/trips/generate` endpoint remains available until Stage 3 introduces preview/apply generation from periods.

## Task 1: Add the period and template schema

**Files:**
- Modify: `app/route_schema.py`
- Create: `tests/test_route_period_schema.py`

- [ ] **Step 1: Write the failing schema test**

```python
def test_period_schema_is_repeat_safe(tmp_path):
    from app import db
    db.DB_PATH = str(tmp_path / "periods.db")
    db.init_db()
    db.init_db()
    con = db.connect()
    try:
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"day_periods", "period_templates", "period_template_items", "period_previews"} <= tables
        period_columns = {row[1] for row in con.execute("PRAGMA table_info(day_periods)")}
        assert {"route_id", "day_type", "start_min", "end_min", "interval_min",
                "travel_time_factor", "transition_mode", "transition_window_min",
                "color", "priority", "active"} <= period_columns
    finally:
        con.close()
```

- [ ] **Step 2: Run the schema test and verify it fails**

Run: `python -m pytest tests/test_route_period_schema.py -q`

Expected: FAIL because `day_periods` is absent.

- [ ] **Step 3: Add idempotent Stage 2 DDL**

Add to `migrate_route_network(con)`:

```sql
CREATE TABLE IF NOT EXISTS day_periods(
  id INTEGER PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  day_type TEXT NOT NULL,
  name TEXT NOT NULL,
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  interval_min INTEGER NOT NULL,
  travel_time_factor REAL NOT NULL DEFAULT 1.0,
  transition_mode TEXT NOT NULL DEFAULT 'abrupt',
  transition_window_min INTEGER NOT NULL DEFAULT 0,
  color TEXT NOT NULL DEFAULT '#3b82f6',
  priority INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_day_periods_route_day
  ON day_periods(route_id,day_type,start_min,end_min);

CREATE TABLE IF NOT EXISTS period_templates(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS period_template_items(
  id INTEGER PRIMARY KEY,
  template_id INTEGER NOT NULL REFERENCES period_templates(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  start_min INTEGER NOT NULL,
  end_min INTEGER NOT NULL,
  interval_min INTEGER NOT NULL,
  travel_time_factor REAL NOT NULL DEFAULT 1.0,
  transition_mode TEXT NOT NULL DEFAULT 'abrupt',
  transition_window_min INTEGER NOT NULL DEFAULT 0,
  color TEXT NOT NULL DEFAULT '#3b82f6',
  priority INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS period_previews(
  token TEXT PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  day_type TEXT NOT NULL,
  username TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
```

- [ ] **Step 4: Run the schema test**

Run: `python -m pytest tests/test_route_period_schema.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the schema**

```powershell
git add app/route_schema.py tests/test_route_period_schema.py
git commit -m "feat(schedule): add route period schema"
```

## Task 2: Validate complete period sets

**Files:**
- Create: `app/route_periods.py`
- Create: `tests/test_route_periods_unit.py`

- [ ] **Step 1: Write failing validation tests**

```python
import pytest

from app.route_periods import validate_periods


def test_validate_periods_orders_and_normalizes_values():
    rows = validate_periods([
        {"name": "Вечер", "start": "16:00", "end": "22:00", "interval_min": 15,
         "travel_time_factor": 1.1, "transition_mode": "smooth", "transition_window_min": 30},
        {"name": "Утро", "start": "06:00", "end": "16:00", "interval_min": 10,
         "travel_time_factor": 1.0, "transition_mode": "abrupt"},
    ], require_continuous=True, service_start="06:00", service_end="22:00")
    assert [(row["start_min"], row["end_min"]) for row in rows] == [(360, 960), (960, 1320)]


@pytest.mark.parametrize("rows,message", [
    ([{"name": "A", "start": "06:00", "end": "10:00", "interval_min": 10},
      {"name": "B", "start": "09:30", "end": "12:00", "interval_min": 15}], "пересекаются"),
    ([{"name": "A", "start": "06:00", "end": "10:00", "interval_min": 0}], "интервал"),
])
def test_validate_periods_rejects_invalid_sets(rows, message):
    with pytest.raises(ValueError, match=message):
        validate_periods(rows)


def test_continuous_mode_rejects_gap():
    with pytest.raises(ValueError, match="разрыв"):
        validate_periods([
            {"name": "A", "start": "06:00", "end": "10:00", "interval_min": 10},
            {"name": "B", "start": "10:30", "end": "12:00", "interval_min": 15},
        ], require_continuous=True, service_start="06:00", service_end="12:00")
```

- [ ] **Step 2: Run the tests and verify import failure**

Run: `python -m pytest tests/test_route_periods_unit.py -q`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement time parsing and validation**

Implement these public functions in `app/route_periods.py`:

```python
VALID_TRANSITIONS = {"abrupt", "smooth"}


def parse_time(value):
    if isinstance(value, int):
        return value
    hours, minutes = map(int, str(value).split(":"))
    total = hours * 60 + minutes
    if not 0 <= total < 2880 or not 0 <= minutes < 60:
        raise ValueError("Некорректное время периода")
    return total


def validate_periods(items, *, require_continuous=False,
                     service_start=None, service_end=None):
    normalized = []
    for source in items:
        row = dict(source)
        row["start_min"] = parse_time(row.get("start_min", row.get("start")))
        row["end_min"] = parse_time(row.get("end_min", row.get("end")))
        row["interval_min"] = int(row["interval_min"])
        row["travel_time_factor"] = float(row.get("travel_time_factor", 1))
        row["transition_mode"] = row.get("transition_mode", "abrupt")
        row["transition_window_min"] = int(row.get("transition_window_min", 0))
        if row["end_min"] <= row["start_min"]:
            raise ValueError("Конец периода должен быть позже начала")
        if row["interval_min"] < 1:
            raise ValueError("Интервал должен быть не меньше 1 минуты")
        if not 0.25 <= row["travel_time_factor"] <= 4:
            raise ValueError("Коэффициент времени должен быть от 0.25 до 4")
        if row["transition_mode"] not in VALID_TRANSITIONS:
            raise ValueError("Неизвестный способ перехода")
        normalized.append(row)
    normalized.sort(key=lambda row: (row["start_min"], row.get("priority", 0)))
    for previous, current in zip(normalized, normalized[1:]):
        if current["start_min"] < previous["end_min"]:
            raise ValueError("Периоды пересекаются")
        if require_continuous and current["start_min"] != previous["end_min"]:
            raise ValueError("Между периодами есть запрещённый разрыв")
    if require_continuous and normalized:
        if service_start is not None and normalized[0]["start_min"] != parse_time(service_start):
            raise ValueError("Периоды не покрывают начало работы")
        if service_end is not None and normalized[-1]["end_min"] != parse_time(service_end):
            raise ValueError("Периоды не покрывают окончание работы")
    return normalized
```

- [ ] **Step 4: Run unit tests**

Run: `python -m pytest tests/test_route_periods_unit.py -q`

Expected: PASS.

- [ ] **Step 5: Commit validation**

```powershell
git add app/route_periods.py tests/test_route_periods_unit.py
git commit -m "feat(schedule): validate route day periods"
```

## Task 3: Calculate transitions, departures, and bus demand

**Files:**
- Modify: `app/route_periods.py`
- Create: `tests/test_route_period_preview.py`

- [ ] **Step 1: Write failing calculation tests**

```python
from app.route_periods import calculate_period_preview


def test_abrupt_periods_generate_expected_departures_and_demand():
    result = calculate_period_preview([
        {"name": "Пик", "start": "06:00", "end": "07:00", "interval_min": 10,
         "travel_time_factor": 1, "transition_mode": "abrupt"},
        {"name": "День", "start": "07:00", "end": "08:00", "interval_min": 20,
         "travel_time_factor": 1, "transition_mode": "abrupt"},
    ], forward_min=40, backward_min=40, terminal_layover_min=5)
    assert result["departures"][:3] == [360, 370, 380]
    assert 420 in result["departures"]
    assert result["periods"][0]["cycle_min"] == 90
    assert result["periods"][0]["buses_required"] == 9
    assert result["periods"][1]["buses_required"] == 5


def test_smooth_transition_has_no_gap_outside_neighbor_intervals():
    result = calculate_period_preview([
        {"name": "Пик", "start": "06:00", "end": "07:00", "interval_min": 10,
         "transition_mode": "abrupt"},
        {"name": "День", "start": "07:00", "end": "09:00", "interval_min": 20,
         "transition_mode": "smooth", "transition_window_min": 60},
    ], forward_min=30, backward_min=30)
    gaps = [b - a for a, b in zip(result["departures"], result["departures"][1:])]
    assert all(10 <= gap <= 20 for gap in gaps)
    assert gaps[-1] == 20
```

- [ ] **Step 2: Run the preview tests and verify missing-function failure**

Run: `python -m pytest tests/test_route_period_preview.py -q`

Expected: FAIL because `calculate_period_preview` is absent.

- [ ] **Step 3: Implement deterministic preview calculation**

Add:

```python
import math


def _gap_for_period(period, previous_interval, elapsed):
    target = period["interval_min"]
    window = period.get("transition_window_min", 0)
    if period.get("transition_mode") != "smooth" or not window or previous_interval is None:
        return target
    progress = min(1.0, max(0.0, elapsed / window))
    return max(1, round(previous_interval + (target - previous_interval) * progress))


def calculate_period_preview(items, *, forward_min, backward_min,
                             terminal_layover_min=6):
    periods = validate_periods(items)
    departures = []
    summaries = []
    previous_interval = None
    for period in periods:
        factor = period["travel_time_factor"]
        cycle = math.ceil((forward_min + backward_min) * factor + terminal_layover_min * 2)
        demand = math.ceil(cycle / period["interval_min"])
        summaries.append({**period, "cycle_min": cycle, "buses_required": demand})
        cursor = period["start_min"] if not departures else max(period["start_min"], departures[-1] + 1)
        while cursor < period["end_min"]:
            if not departures or cursor > departures[-1]:
                departures.append(cursor)
            elapsed = cursor - period["start_min"]
            cursor += _gap_for_period(period, previous_interval, elapsed)
        previous_interval = period["interval_min"]
    warnings = []
    for previous, current in zip(summaries, summaries[1:]):
        delta = current["buses_required"] - previous["buses_required"]
        if abs(delta) >= 2:
            warnings.append({"code": "demand_jump", "from": previous["name"],
                             "to": current["name"], "delta": delta})
    return {"departures": departures, "periods": summaries,
            "max_buses_required": max((p["buses_required"] for p in summaries), default=0),
            "warnings": warnings}
```

- [ ] **Step 4: Run calculation tests**

Run: `python -m pytest tests/test_route_period_preview.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the calculator**

```powershell
git add app/route_periods.py tests/test_route_period_preview.py
git commit -m "feat(schedule): calculate interval and bus demand preview"
```

## Task 4: Add transactional period APIs

**Files:**
- Create: `app/api_route_periods.py`
- Modify: `app/main.py`
- Create: `tests/test_route_period_api.py`

- [ ] **Step 1: Write failing authenticated API tests**

Test these contracts:

```python
def test_replace_and_read_complete_period_set(client, route_id):
    response = client.put(f"/api/routes/{route_id}/periods/будни", json={
        "require_continuous": True, "service_start": "06:00", "service_end": "22:00",
        "items": [
            {"name": "Утро", "start": "06:00", "end": "10:00", "interval_min": 10,
             "travel_time_factor": 1.05, "transition_mode": "abrupt", "color": "#ef4444"},
            {"name": "День", "start": "10:00", "end": "22:00", "interval_min": 20,
             "travel_time_factor": 1, "transition_mode": "smooth",
             "transition_window_min": 30, "color": "#3b82f6"},
        ]})
    assert response.status_code == 200
    saved = client.get(f"/api/routes/{route_id}/periods/будни").json()
    assert [item["name"] for item in saved["items"]] == ["Утро", "День"]


def test_rejected_period_set_does_not_replace_existing_rows(client, route_id):
    valid = {"items": [
        {"name": "Рабочий день", "start": "06:00", "end": "22:00", "interval_min": 15}
    ]}
    assert client.put(
        f"/api/routes/{route_id}/periods/будни", json=valid
    ).status_code == 200
    before = client.get(f"/api/routes/{route_id}/periods/будни").json()["items"]
    rejected = client.put(f"/api/routes/{route_id}/periods/будни", json={"items": [
        {"name": "A", "start": "06:00", "end": "12:00", "interval_min": 10},
        {"name": "B", "start": "11:00", "end": "22:00", "interval_min": 20},
    ]})
    assert rejected.status_code == 400
    after = client.get(f"/api/routes/{route_id}/periods/будни").json()["items"]
    assert after == before
```

- [ ] **Step 2: Run API tests and verify 404 failures**

Run: `python -m pytest tests/test_route_period_api.py -q`

Expected: FAIL because the period routes are absent.

- [ ] **Step 3: Implement GET and full-set PUT**

Register `APIRouter(prefix="/api")` and add:

```python
PERIOD_FIELDS = (
    "name", "start_min", "end_min", "interval_min", "travel_time_factor",
    "transition_mode", "transition_window_min", "color", "priority", "active",
)


def _period_rows(con, route_id, day_type):
    return db.rows(con.execute(
        "SELECT * FROM day_periods WHERE route_id=? AND day_type=? "
        "ORDER BY start_min,priority,id", (route_id, day_type)
    ))


def _replace_periods(con, route_id, day_type, payload, user):
    if not con.execute("SELECT 1 FROM routes WHERE id=?", (route_id,)).fetchone():
        raise HTTPException(404, "Маршрут не найден")
    normalized = validate_periods(
        payload.get("items") or [],
        require_continuous=bool(payload.get("require_continuous")),
        service_start=payload.get("service_start"),
        service_end=payload.get("service_end"),
    )
    old = _period_rows(con, route_id, day_type)
    con.execute("DELETE FROM day_periods WHERE route_id=? AND day_type=?", (route_id, day_type))
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    for position, source in enumerate(normalized):
        row = {
            "name": str(source.get("name") or f"Период {position + 1}").strip(),
            "start_min": source["start_min"], "end_min": source["end_min"],
            "interval_min": source["interval_min"],
            "travel_time_factor": source["travel_time_factor"],
            "transition_mode": source["transition_mode"],
            "transition_window_min": source["transition_window_min"],
            "color": source.get("color") or "#3b82f6",
            "priority": int(source.get("priority", position)),
            "active": 1 if source.get("active", 1) else 0,
        }
        con.execute(
            "INSERT INTO day_periods(route_id,day_type," + ",".join(PERIOD_FIELDS) +
            ",created_at,updated_at) VALUES(" + ",".join("?" for _ in range(14)) + ")",
            [route_id, day_type] + [row[field] for field in PERIOD_FIELDS] + [timestamp, timestamp],
        )
    saved = _period_rows(con, route_id, day_type)
    db.audit(con, user["username"], "замена периодов движения", "routes", route_id,
             old={"day_type": day_type, "items": old},
             new={"day_type": day_type, "items": saved})
    return saved


@router.get("/routes/{route_id}/periods/{day_type}")
def periods_get(route_id: int, day_type: str, user=Depends(current_user)):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM routes WHERE id=?", (route_id,)).fetchone():
            raise HTTPException(404, "Маршрут не найден")
        return {"items": _period_rows(con, route_id, day_type)}
    finally:
        con.close()


@router.put("/routes/{route_id}/periods/{day_type}")
def periods_replace(route_id: int, day_type: str, payload: dict = Body(...),
                    user=Depends(current_user)):
    require_write(user, "trips")
    con = db.connect()
    try:
        saved = _replace_periods(con, route_id, day_type, payload, user)
        con.commit()
        return {"ok": True, "items": saved}
    except ValueError as exc:
        con.rollback()
        raise HTTPException(400, str(exc))
    finally:
        con.close()
```

The PUT endpoint must:

1. call `require_write(user, "trips")`;
2. validate the entire set before deleting anything;
3. open one transaction, capture old rows, delete the route/day set, insert normalized rows, audit old/new values, and commit;
4. roll back automatically on any exception;
5. never write `route_trips`.

- [ ] **Step 4: Include the router and run API/regression tests**

Run: `python -m pytest tests/test_route_period_api.py tests/test_schedule_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the API**

```powershell
git add app/api_route_periods.py app/main.py tests/test_route_period_api.py
git commit -m "feat(schedule): add route period api"
```

## Task 5: Add reusable templates with preview then apply

**Files:**
- Modify: `app/api_route_periods.py`
- Create: `tests/test_route_period_templates.py`

- [ ] **Step 1: Write failing template tests**

Cover:

```python
def test_template_preview_does_not_change_route_until_apply(client, route_id):
    template = client.post("/api/period-templates", json={"name": "Городской будний", "items": [
        {"name": "Пик", "start": "06:00", "end": "09:00", "interval_min": 8},
        {"name": "День", "start": "09:00", "end": "22:00", "interval_min": 18},
    ]}).json()
    preview = client.post(
        f"/api/routes/{route_id}/periods/будни/template-preview",
        json={"template_id": template["id"]},
    ).json()
    assert client.get(f"/api/routes/{route_id}/periods/будни").json()["items"] == []
    applied = client.post(
        f"/api/routes/{route_id}/periods/будни/template-apply",
        json={"preview_token": preview["preview_token"]},
    )
    assert applied.status_code == 200
    assert len(client.get(f"/api/routes/{route_id}/periods/будни").json()["items"]) == 2
```

Also test expired, already-used, wrong-user, and wrong-route tokens.

- [ ] **Step 2: Run template tests and verify failures**

Run: `python -m pytest tests/test_route_period_templates.py -q`

Expected: FAIL with 404 responses.

- [ ] **Step 3: Implement template CRUD and preview/apply**

Add endpoints:

```text
GET    /api/period-templates
POST   /api/period-templates
PUT    /api/period-templates/{template_id}
DELETE /api/period-templates/{template_id}
POST   /api/routes/{route_id}/periods/{day_type}/template-preview
POST   /api/routes/{route_id}/periods/{day_type}/template-apply
```

Preview stores the normalized proposed set and old/new diff in `period_previews` for 30 minutes. Apply validates token ownership, expiry, route/day match, and one-time use, then replaces the complete set and audits in one transaction. Add `applied_at TEXT` to `period_previews` in Task 1 DDL before implementing this task.

- [ ] **Step 4: Run template and period API tests**

Run: `python -m pytest tests/test_route_period_templates.py tests/test_route_period_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit templates**

```powershell
git add app/api_route_periods.py app/route_schema.py tests/test_route_period_templates.py
git commit -m "feat(schedule): add period templates with preview apply"
```

## Task 6: Add non-destructive departure and demand preview API

**Files:**
- Modify: `app/api_route_periods.py`
- Create: `tests/test_route_period_preview_api.py`

- [ ] **Step 1: Write failing preview API tests**

```python
def test_period_preview_returns_departures_and_demand_without_writing_trips(client, route_id):
    # Save two periods first.
    before = client.get(f"/api/trips?route_id={route_id}&day_type=будни").json()["items"]
    response = client.post(f"/api/routes/{route_id}/periods/будни/preview", json={
        "terminal_layover_min": 5,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["departures"]
    assert data["max_buses_required"] > 0
    after = client.get(f"/api/trips?route_id={route_id}&day_type=будни").json()["items"]
    assert after == before
```

Also test missing periods, missing route travel time, and a demand-jump warning.

- [ ] **Step 2: Run preview API tests and verify 404**

Run: `python -m pytest tests/test_route_period_preview_api.py -q`

Expected: FAIL because `/preview` is absent.

- [ ] **Step 3: Implement preview endpoint**

Add:

```python
@router.post("/routes/{route_id}/periods/{day_type}/preview")
def periods_preview(route_id: int, day_type: str, payload: dict = Body(default=None),
                    user=Depends(current_user)):
    payload = payload or {}
    con = db.connect()
    try:
        route = db.one(con.execute("SELECT * FROM routes WHERE id=?", (route_id,)))
        if not route:
            raise HTTPException(404, "Маршрут не найден")
        periods = _period_rows(con, route_id, day_type)
        periods = [row for row in periods if row["active"]]
        if not periods:
            raise HTTPException(400, "Для маршрута не заданы активные периоды")
        forward = int(route.get("trip_time_min") or 0)
        backward = int(route.get("trip_time_back_min") or forward)
        if forward <= 0 or backward <= 0:
            raise HTTPException(400, "Укажите время рейса в обоих направлениях")
        result = calculate_period_preview(
            periods, forward_min=forward, backward_min=backward,
            terminal_layover_min=int(payload.get("terminal_layover_min", 6)),
        )
        result["departure_minutes"] = result.pop("departures")
        result["departures"] = [
            f"{(minute // 60) % 24:02d}:{minute % 60:02d}"
            for minute in result["departure_minutes"]
        ]
        return result
    finally:
        con.close()
```

Return both `departure_minutes` and formatted `departures`; keep integer minutes as the stable calculation contract.

- [ ] **Step 4: Run preview and legacy generation tests**

Run: `python -m pytest tests/test_route_period_preview_api.py tests/test_schedule_api.py -q`

Expected: PASS, including unchanged legacy `/api/trips/generate` behavior.

- [ ] **Step 5: Commit preview API**

```powershell
git add app/api_route_periods.py tests/test_route_period_preview_api.py
git commit -m "feat(schedule): preview departures and bus demand"
```

## Task 7: Add the «Периоды дня» route-card tab

**Files:**
- Modify: `static/route-card.js`
- Modify: `static/styles.css`
- Modify: `static/index.html`
- Create: `tests/test_route_period_frontend.py`

- [ ] **Step 1: Write a failing frontend smoke test**

```python
def test_route_card_contains_period_editor_and_preview_actions():
    source = (ROOT / "static" / "route-card.js").read_text(encoding="utf-8")
    assert "Периоды дня" in source
    assert "/periods/" in source
    assert "routePeriodPreview" in source
    assert "template-preview" in source
    assert "template-apply" in source
```

- [ ] **Step 2: Run the frontend test and verify failure**

Run: `python -m pytest tests/test_route_period_frontend.py -q`

Expected: FAIL because the tab/actions are absent.

- [ ] **Step 3: Implement the editor**

Add a `periods` tab to `ROUTE_CARD_TABS`. The tab must provide:

- day type selection;
- ordered period rows with name, start, end, interval, factor, color, transition, and smoothing window;
- add, duplicate, reorder, and remove actions in local state;
- full-set save through `PUT /api/routes/{id}/periods/{day_type}`;
- explicit validation error display without discarding edits;
- template selector, preview diff, Cancel, and Apply;
- Preview calculation button and cards for departures, cycle time, per-period demand, maximum demand, and warnings.

Do not call `/api/trips/generate` from this tab.

- [ ] **Step 4: Add responsive period styles and cache key**

Add `.route-period-grid`, `.route-period-row`, `.route-period-timeline`, `.route-period-block`, `.route-demand-grid`, `.route-demand-jump`, mobile stacking, and print-safe rules. Update the route-specific cache suffix from `route=3.3` to `route=3.4` while preserving the compatibility substrings required by existing UI tests.

- [ ] **Step 5: Run frontend and API tests**

Run: `python -m pytest tests/test_route_period_frontend.py tests/test_route_card_frontend.py tests/test_route_period_api.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the route-card period UI**

```powershell
git add static/route-card.js static/styles.css static/index.html tests/test_route_period_frontend.py
git commit -m "feat(schedule): add route period editor ui"
```

## Task 8: Integrate period preview into the schedule workspace

**Files:**
- Modify: `static/app.js`
- Modify: `static/styles.css`
- Create: `tests/test_schedule_period_ui.py`

- [ ] **Step 1: Write a failing schedule UI test**

```python
def test_schedule_view_offers_period_preview_without_replacing_legacy_generator():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "schedulePeriodPreview" in source
    assert "/periods/${" in source
    assert "/api/trips/generate" in source
```

- [ ] **Step 2: Run the test and verify failure**

Run: `python -m pytest tests/test_schedule_period_ui.py -q`

Expected: FAIL because `schedulePeriodPreview` is absent.

- [ ] **Step 3: Add preview controls and results**

Keep the existing trip table and legacy generation modal. Add:

- «Предпросмотр по периодам» action;
- a read-only timeline of returned departures;
- per-period interval, factor, cycle time, and bus demand cards;
- maximum required buses and demand-jump warnings;
- a clear label that preview has not changed the saved `route_trips`;
- a link to `#/routeCard/{route_id}` with the periods tab selected through shared state.

- [ ] **Step 4: Run schedule UI and regression tests**

Run: `python -m pytest tests/test_schedule_period_ui.py tests/test_schedule_api.py tests/test_summary_schedule_api.py tests/test_summary_schedule_ui.py -q`

Expected: PASS.

- [ ] **Step 5: Commit schedule integration**

```powershell
git add static/app.js static/styles.css tests/test_schedule_period_ui.py
git commit -m "feat(schedule): show period demand preview"
```

## Task 9: Verify Stage 2 end to end

**Files:**
- Modify: this plan checkbox state only if verification evidence is committed

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest tests -q`

Expected: all tests pass.

- [ ] **Step 2: Verify schema and period replacement on a database copy**

Create a SQLite backup copy of `atp.db`, point `ATP_DB` at the copy, run `db.init_db()` twice, save a two-period set for a test route, reject an overlapping replacement, and verify the original valid set remains unchanged.

Expected: repeat-safe schema, no partial writes, and unchanged production database.

- [ ] **Step 3: Verify browser workflow**

On a temporary database:

1. open route №104 or №44;
2. add morning/day/evening periods for `будни`;
3. verify overlap and forbidden-gap messages;
4. save, reload, and confirm values;
5. preview abrupt then smooth transitions;
6. confirm bus demand and warning cards;
7. preview a template, cancel, verify no changes, then preview and apply;
8. open «Расписания маршрутов», preview periods, and confirm the existing saved trip table is unchanged;
9. run the legacy generator and confirm downstream schedule checks still work.

- [ ] **Step 4: Run final static and Git checks**

Run:

```powershell
node --check static/app.js
node --check static/route-card.js
git diff --check
git status --short
```

Expected: exit code 0 and only intentional files before the last commit.

## Stage 2 completion gate

Stage 2 is complete only when:

- period sets reject overlaps and configured forbidden gaps atomically;
- abrupt and smooth previews have deterministic tested output;
- cycle time and per-period/max bus demand are visible;
- templates require preview then explicit apply;
- preview calculations never mutate `route_trips`;
- the old generator, trip editor, checks, summaries, orders, and waybill tests remain green;
- the merged result passes the full suite and browser workflow.

Stage 3 begins only after this gate and will add stop-level trip times, a trip matrix, manual adjustment strategies, and preview/apply generation into `route_trips`.
