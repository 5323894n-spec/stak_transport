# Route Schedule V2 Stage 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persisted stop-by-stop schedule, a trip-by-stop time matrix, controlled manual time corrections, and preview/apply generation that writes compatible `route_trips` only after explicit confirmation.

**Architecture:** Keep `route_trips` as the compatibility record consumed by rosters, orders, summaries, and waybills. Add second-precision child rows in `trip_stop_times`, pure calculation code in `route_timetable.py`, and short-lived user/route/day-bound generation previews. Stage 3 does not introduce shift types, driver assignments, layover optimization, or the labor-rules engine; those remain Stages 4 and 5.

**Tech Stack:** Python 3.14, FastAPI, SQLite, vanilla JavaScript SPA, openpyxl, pytest.

---

## Scope and compatibility rules

- Generated schedules replace one complete `(route_id, day_type)` set only after a preview token is applied.
- Preview and rejected manual corrections never mutate `route_trips` or `trip_stop_times`.
- `route_trips.dep_time`, `arr_time`, direction, output, shift, distance, and break fields remain available to every existing consumer.
- Service-day seconds may exceed 86,400 so trips crossing midnight remain monotonic; API responses also expose formatted `HH:MM` values.
- Period intervals describe departures from the initial terminal. Each assigned output receives a forward trip, a terminal layover, and a backward trip before it can serve another initial-terminal departure.
- Explicit segment runtime overrides win over a period factor; otherwise the period factor applies to `route_stops.run_time_sec`. Dwell time is never multiplied.
- Manual correction strategies in this stage are `selected_only`, `shift_following`, and `redistribute_remaining`, matching the approved design. Timing-point anchoring remains available through `redistribute_remaining` and is not a fourth persisted strategy.

## File map

- Modify `app/route_schema.py`: Stage 3 tables, indexes, and additive `route_trips` columns.
- Create `app/route_timetable.py`: pure stop-time, output-assignment, validation, and manual-adjustment functions.
- Create `app/api_route_timetable.py`: matrix, preview/apply, recalculate, manual adjustment, reset, and export endpoints.
- Modify `app/main.py`: register the timetable router.
- Modify `app/api_planning.py`: synchronize stop times when a legacy trip is edited or deleted.
- Modify `static/app.js`: generation preview/apply controls and the stop-time matrix.
- Modify `static/styles.css` and `static/index.html`: matrix, preview, manual markers, responsive/print styles, cache key.
- Create focused tests listed in each task.

---

## Task 1: Add persisted stop times and generation previews

**Files:**
- Modify: `app/route_schema.py`
- Create: `tests/test_route_timetable_schema.py`

- [ ] **Step 1: Write the failing repeat-safe schema test**

```python
def test_stage_three_schema_is_repeat_safe(tmp_path):
    from app import db
    db.DB_PATH = str(tmp_path / "stage-three.db")
    db.init_db()
    db.init_db()
    con = db.connect()
    try:
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"trip_stop_times", "route_stop_runtimes",
                "schedule_generation_previews"} <= tables
        columns = {row[1] for row in con.execute(
            "PRAGMA table_info(trip_stop_times)"
        )}
        assert {"trip_id", "route_stop_id", "sequence", "arrival_sec",
                "departure_sec", "is_timing_point", "is_manual_override",
                "override_strategy", "override_reason"} <= columns
        trip_columns = {row[1] for row in con.execute(
            "PRAGMA table_info(route_trips)"
        )}
        assert {"period_id", "source", "generation_key"} <= trip_columns
    finally:
        con.close()
```

- [ ] **Step 2: Run the schema test and verify the expected failure**

Run: `python -m pytest tests/test_route_timetable_schema.py -q`

Expected: FAIL because `trip_stop_times` is absent.

- [ ] **Step 3: Add idempotent Stage 3 DDL**

Add these tables to `ROUTE_NETWORK_SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS route_stop_runtimes(
  id INTEGER PRIMARY KEY,
  route_stop_id INTEGER NOT NULL REFERENCES route_stops(id) ON DELETE CASCADE,
  period_id INTEGER NOT NULL REFERENCES day_periods(id) ON DELETE CASCADE,
  run_time_sec INTEGER NOT NULL CHECK(run_time_sec > 0),
  source TEXT NOT NULL DEFAULT 'manual',
  updated_at TEXT NOT NULL,
  UNIQUE(route_stop_id,period_id)
);

CREATE TABLE IF NOT EXISTS trip_stop_times(
  id INTEGER PRIMARY KEY,
  trip_id INTEGER NOT NULL REFERENCES route_trips(id) ON DELETE CASCADE,
  route_stop_id INTEGER NOT NULL REFERENCES route_stops(id) ON DELETE RESTRICT,
  sequence INTEGER NOT NULL,
  arrival_sec INTEGER NOT NULL CHECK(arrival_sec >= 0),
  departure_sec INTEGER NOT NULL CHECK(departure_sec >= arrival_sec),
  is_timing_point INTEGER NOT NULL DEFAULT 0,
  is_manual_override INTEGER NOT NULL DEFAULT 0,
  override_strategy TEXT,
  override_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(trip_id,sequence)
);
CREATE INDEX IF NOT EXISTS idx_trip_stop_times_trip
  ON trip_stop_times(trip_id,sequence);
CREATE INDEX IF NOT EXISTS idx_trip_stop_times_route_stop
  ON trip_stop_times(route_stop_id,departure_sec);

CREATE TABLE IF NOT EXISTS schedule_generation_previews(
  token TEXT PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  day_type TEXT NOT NULL,
  username TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  applied_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_schedule_generation_preview_scope
  ON schedule_generation_previews(route_id,day_type,username,created_at);
```

Add a helper that checks `PRAGMA table_info(route_trips)` and executes only missing columns:

```python
def _add_column(con, table, name, definition):
    columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    if name not in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def migrate_route_network(con):
    con.executescript(ROUTE_NETWORK_SCHEMA)
    _add_column(con, "route_trips", "period_id", "INTEGER REFERENCES day_periods(id)")
    _add_column(con, "route_trips", "source", "TEXT NOT NULL DEFAULT 'manual'")
    _add_column(con, "route_trips", "generation_key", "TEXT")
```

- [ ] **Step 4: Run the schema and existing migration tests**

Run: `python -m pytest tests/test_route_timetable_schema.py tests/test_route_period_schema.py tests/test_route_migration.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the schema**

```powershell
git add app/route_schema.py tests/test_route_timetable_schema.py
git commit -m "feat(schedule): add stop time schema"
```

## Task 2: Calculate stop times with period runtimes

**Files:**
- Create: `app/route_timetable.py`
- Create: `tests/test_route_timetable_unit.py`

- [ ] **Step 1: Write failing pure-domain tests**

```python
from app.route_timetable import calculate_trip_stop_times, format_service_time


def test_stop_times_apply_factor_to_runs_but_not_dwell():
    trace = [
        {"id": 1, "sequence": 1, "run_time_sec": 0, "dwell_time_sec": 30,
         "is_timing_point": 1},
        {"id": 2, "sequence": 2, "run_time_sec": 300, "dwell_time_sec": 45,
         "is_timing_point": 0},
        {"id": 3, "sequence": 3, "run_time_sec": 420, "dwell_time_sec": 0,
         "is_timing_point": 1},
    ]
    rows = calculate_trip_stop_times(
        trace, departure_sec=6 * 3600, runtime_factor=1.2, runtime_overrides={}
    )
    assert rows[0]["arrival_sec"] == rows[0]["departure_sec"] == 21600
    assert rows[1]["arrival_sec"] == 21960
    assert rows[1]["departure_sec"] == 22005
    assert rows[2]["arrival_sec"] == 22509


def test_explicit_runtime_override_wins_and_midnight_formats_extended_day():
    trace = [
        {"id": 10, "sequence": 1, "run_time_sec": 0, "dwell_time_sec": 0},
        {"id": 11, "sequence": 2, "run_time_sec": 900, "dwell_time_sec": 0},
    ]
    rows = calculate_trip_stop_times(
        trace, departure_sec=23 * 3600 + 55 * 60,
        runtime_factor=2, runtime_overrides={11: 600},
    )
    assert rows[-1]["arrival_sec"] == 24 * 3600 + 5 * 60
    assert format_service_time(rows[-1]["arrival_sec"]) == "24:05"
```

- [ ] **Step 2: Run the domain tests and verify import failure**

Run: `python -m pytest tests/test_route_timetable_unit.py -q`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement second-precision calculation**

Create `app/route_timetable.py` with these public contracts:

```python
import math


def format_service_time(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def calculate_trip_stop_times(trace, *, departure_sec, runtime_factor=1.0,
                              runtime_overrides=None):
    if not trace:
        raise ValueError("В направлении нет остановок")
    runtime_overrides = runtime_overrides or {}
    cursor = int(departure_sec)
    result = []
    for index, stop in enumerate(trace):
        if index:
            explicit = runtime_overrides.get(stop["id"])
            run = int(explicit if explicit is not None else
                      math.ceil(int(stop.get("run_time_sec") or 0) * runtime_factor))
            if run <= 0:
                raise ValueError("Для перегона не задано положительное время хода")
            cursor += run
        arrival = cursor
        dwell = 0 if index in (0, len(trace) - 1) else int(stop.get("dwell_time_sec") or 0)
        cursor += dwell
        result.append({
            "route_stop_id": stop["id"], "sequence": int(stop["sequence"]),
            "arrival_sec": arrival, "departure_sec": cursor,
            "is_timing_point": 1 if stop.get("is_timing_point") else 0,
        })
    return result
```

- [ ] **Step 4: Run domain tests**

Run: `python -m pytest tests/test_route_timetable_unit.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the calculator**

```powershell
git add app/route_timetable.py tests/test_route_timetable_unit.py
git commit -m "feat(schedule): calculate stop level trip times"
```

## Task 3: Expose and recalculate the trip matrix

**Files:**
- Create: `app/api_route_timetable.py`
- Modify: `app/main.py`
- Modify: `app/api_planning.py`
- Create: `tests/test_route_timetable_api.py`

- [ ] **Step 1: Write failing authenticated API tests**

```python
def test_recalculate_trip_builds_matrix(client, route_with_trace):
    route_id, trip_id = route_with_trace
    response = client.post(f"/api/trips/{trip_id}/stop-times/recalculate")
    assert response.status_code == 200, response.text
    matrix = client.get(
        f"/api/routes/{route_id}/stop-times?day_type=будни"
    ).json()
    assert [stop["sequence"] for stop in matrix["stops"]["forward"]] == [1, 2, 3]
    assert matrix["trips"][0]["trip_id"] == trip_id
    assert len(matrix["trips"][0]["times"]) == 3


def test_legacy_trip_edit_recalculates_stop_times(client, route_with_trace):
    route_id, trip_id = route_with_trace
    client.post(f"/api/trips/{trip_id}/stop-times/recalculate")
    payload = client.get(f"/api/trips?route_id={route_id}&day_type=будни").json()["items"][0]
    payload["dep_time"] = "07:00"
    payload["arr_time"] = "07:30"
    assert client.post("/api/trips", json=payload).status_code == 200
    matrix = client.get(f"/api/routes/{route_id}/stop-times?day_type=будни").json()
    assert matrix["trips"][0]["times"][0]["departure_time"] == "07:00"
```

- [ ] **Step 2: Run tests and verify 404 failures**

Run: `python -m pytest tests/test_route_timetable_api.py -q`

Expected: FAIL because the timetable router is absent.

- [ ] **Step 3: Implement matrix helpers and endpoints**

Register `api_route_timetable.router` in `app/main.py`. Implement:

```python
@router.get("/routes/{route_id}/stop-times")
def stop_time_matrix(route_id: int, day_type: str, direction: str = "",
                     output_number: int = 0, user=Depends(current_user)):
    ...


@router.post("/trips/{trip_id}/stop-times/recalculate")
def trip_stop_times_recalculate(trip_id: int, user=Depends(current_user)):
    require_write(user, "trips")
    ...
```

The matrix response must be exactly:

```python
{
    "stops": {"forward": [...], "backward": [...]},
    "trips": [{
        "trip_id": 42, "output_number": 1, "trip_number": 3,
        "direction": "прямое", "period_id": 7,
        "times": [{"route_stop_id": 11, "arrival_time": "06:30",
                   "departure_time": "06:30", "is_manual_override": False}],
    }],
}
```

Add `_recalculate_trip_stop_times(con, trip_id, preserve_manual=True)` and call it after a successful legacy trip update when the trip already has stop-time rows. Deleting a trip relies on the foreign-key cascade.

- [ ] **Step 4: Run matrix and legacy schedule tests**

Run: `python -m pytest tests/test_route_timetable_api.py tests/test_schedule_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the matrix API**

```powershell
git add app/api_route_timetable.py app/main.py app/api_planning.py tests/test_route_timetable_api.py
git commit -m "feat(schedule): add persisted stop time matrix"
```

## Task 4: Build deterministic schedule-generation previews

**Files:**
- Modify: `app/route_timetable.py`
- Modify: `app/api_route_timetable.py`
- Create: `tests/test_schedule_generation_preview.py`

- [ ] **Step 1: Write failing preview tests**

```python
def test_generation_preview_has_forward_grid_return_trips_and_no_writes(client, configured_route):
    route_id = configured_route
    before = client.get(f"/api/trips?route_id={route_id}&day_type=будни").json()["items"]
    response = client.post(
        f"/api/routes/{route_id}/schedule-generation/preview",
        json={"day_type": "будни", "outputs": 3, "terminal_layover_min": 5},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["preview_token"]
    assert {trip["direction"] for trip in data["trips"]} == {"прямое", "обратное"}
    assert all(trip["stop_times"] for trip in data["trips"])
    after = client.get(f"/api/trips?route_id={route_id}&day_type=будни").json()["items"]
    assert after == before


def test_preview_rejects_incomplete_trace_and_output_deficit(client, configured_route):
    route_id = configured_route
    response = client.post(
        f"/api/routes/{route_id}/schedule-generation/preview",
        json={"day_type": "будни", "outputs": 1},
    )
    assert response.status_code == 400
    assert "выход" in response.json()["detail"].lower()
```

- [ ] **Step 2: Run tests and verify the endpoint is absent**

Run: `python -m pytest tests/test_schedule_generation_preview.py -q`

Expected: FAIL with 404.

- [ ] **Step 3: Implement output assignment and preview storage**

Add this pure contract:

```python
def build_schedule_preview(*, departures, periods, forward_trace, backward_trace,
                           runtime_overrides, outputs, terminal_layover_sec):
    """Assign each initial-terminal departure to an available output.

    For every assigned forward trip, create its backward trip after terminal
    layover. The output becomes available after the backward terminal arrival
    plus layover. Raise ValueError when no output is available for a grid point.
    """
    ...
```

Add `POST /api/routes/{route_id}/schedule-generation/preview`. It must:

1. validate route, periods, and both traces;
2. call the Stage 2 departure calculator;
3. load explicit `route_stop_runtimes` for relevant periods;
4. call `build_schedule_preview`;
5. calculate diff counts against the current route/day rows;
6. store JSON under a 32-character random token bound to route, day, and username for 30 minutes;
7. return token, expiry, trips, per-period demand, warnings, and diff.

- [ ] **Step 4: Run preview, period, and schedule regression tests**

Run: `python -m pytest tests/test_schedule_generation_preview.py tests/test_route_period_preview.py tests/test_schedule_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit preview generation**

```powershell
git add app/route_timetable.py app/api_route_timetable.py tests/test_schedule_generation_preview.py
git commit -m "feat(schedule): preview generated stop level schedule"
```

## Task 5: Apply a preview atomically into compatible trips

**Files:**
- Modify: `app/api_route_timetable.py`
- Create: `tests/test_schedule_generation_apply.py`

- [ ] **Step 1: Write failing apply-token tests**

```python
def test_apply_replaces_route_day_and_persists_stop_times(client, configured_route):
    token = create_generation_preview(client, configured_route)["preview_token"]
    response = client.post(
        f"/api/routes/{configured_route}/schedule-generation/apply",
        json={"day_type": "будни", "preview_token": token},
    )
    assert response.status_code == 200, response.text
    trips = client.get(
        f"/api/trips?route_id={configured_route}&day_type=будни"
    ).json()["items"]
    matrix = client.get(
        f"/api/routes/{configured_route}/stop-times?day_type=будни"
    ).json()["trips"]
    assert trips and len(matrix) == len(trips)
    assert all(trip["source"] == "period_generation" for trip in trips)


def test_apply_token_is_one_time_user_route_day_bound_and_expiring(client, configured_route):
    token = create_generation_preview(client, configured_route)["preview_token"]
    assert apply_generation(client, configured_route, token).status_code == 200
    assert apply_generation(client, configured_route, token).status_code == 409
    assert apply_generation(client, configured_route + 1, token).status_code == 404
```

- [ ] **Step 2: Run tests and verify apply is absent**

Run: `python -m pytest tests/test_schedule_generation_apply.py -q`

Expected: FAIL with 404.

- [ ] **Step 3: Implement one-transaction apply**

Add `POST /api/routes/{route_id}/schedule-generation/apply`. In one SQLite transaction:

```python
con.execute("DELETE FROM route_trips WHERE route_id=? AND day_type=?",
            (route_id, day_type))
for source in plan["trips"]:
    trip_id = con.execute(
        "INSERT INTO route_trips(route_id,day_type,output_number,shift_number,"
        "trip_number,direction,dep_time,arr_time,distance_km,break_after_min,"
        "break_type,period_id,source,generation_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        values,
    ).lastrowid
    insert_trip_stop_times(con, trip_id, source["stop_times"])
updated = con.execute(
    "UPDATE schedule_generation_previews SET applied_at=? "
    "WHERE token=? AND applied_at IS NULL", (now, token)
)
if updated.rowcount != 1:
    raise HTTPException(409, "Предпросмотр уже применён")
```

Validate route, day, username, expiry, and `applied_at` before deleting. Audit old/new counts and the generation key. Roll back on every exception.

- [ ] **Step 4: Run apply and downstream compatibility tests**

Run: `python -m pytest tests/test_schedule_generation_apply.py tests/test_schedule_api.py tests/test_summary_schedule_api.py tests/test_waybill_modes_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit atomic apply**

```powershell
git add app/api_route_timetable.py tests/test_schedule_generation_apply.py
git commit -m "feat(schedule): apply generated schedule atomically"
```

## Task 6: Add controlled manual stop-time corrections

**Files:**
- Modify: `app/route_timetable.py`
- Modify: `app/api_route_timetable.py`
- Create: `tests/test_route_timetable_adjustments.py`

- [ ] **Step 1: Write failing strategy and rollback tests**

```python
def test_shift_following_moves_selected_and_later_points(client, generated_trip):
    before = trip_times(client, generated_trip)
    target = before[1]
    response = client.patch(
        f"/api/trips/{generated_trip}/stop-times/{target['route_stop_id']}",
        json={"departure_time": "07:15", "strategy": "shift_following",
              "reason": "оперативная корректировка"},
    )
    assert response.status_code == 200, response.text
    after = trip_times(client, generated_trip)
    delta = after[1]["departure_sec"] - before[1]["departure_sec"]
    assert delta != 0
    assert after[-1]["arrival_sec"] - before[-1]["arrival_sec"] == delta
    assert after[1]["is_manual_override"] is True


def test_invalid_selected_only_is_rejected_without_partial_write(client, generated_trip):
    before = trip_times(client, generated_trip)
    response = client.patch(
        f"/api/trips/{generated_trip}/stop-times/{before[1]['route_stop_id']}",
        json={"departure_time": "23:59", "strategy": "selected_only",
              "reason": "test"},
    )
    assert response.status_code == 400
    assert trip_times(client, generated_trip) == before
```

- [ ] **Step 2: Run tests and verify PATCH is absent**

Run: `python -m pytest tests/test_route_timetable_adjustments.py -q`

Expected: FAIL with 405 or 404.

- [ ] **Step 3: Implement pure adjustment and validation**

Add:

```python
VALID_ADJUSTMENT_STRATEGIES = {
    "selected_only", "shift_following", "redistribute_remaining"
}


def adjust_stop_times(rows, *, route_stop_id, departure_sec, strategy):
    """Return a new list; never mutate the input list."""
    ...


def validate_stop_times(rows, *, minimum_run_sec=1):
    for previous, current in zip(rows, rows[1:]):
        if current["arrival_sec"] < previous["departure_sec"] + minimum_run_sec:
            raise ValueError("Время следующей остановки нарушает монотонность")
```

`redistribute_remaining` keeps the final terminal time fixed and distributes the selected delta proportionally across positive remaining run intervals. Reject impossible compression.

- [ ] **Step 4: Implement PATCH and reset endpoints**

```python
@router.patch("/trips/{trip_id}/stop-times/{route_stop_id}")
def stop_time_adjust(...): ...


@router.post("/routes/{route_id}/stop-times/reset-manual")
def stop_times_reset_manual(route_id: int, payload: dict = Body(...), ...): ...
```

Reset accepts exactly one scope: `trip_id`, `output_number`, or the full `day_type`. It recalculates selected trips from their departure time and clears override metadata. Every change is audited with before/after values and the reason.

- [ ] **Step 5: Run adjustment and matrix tests**

Run: `python -m pytest tests/test_route_timetable_adjustments.py tests/test_route_timetable_api.py -q`

Expected: PASS.

- [ ] **Step 6: Commit manual adjustments**

```powershell
git add app/route_timetable.py app/api_route_timetable.py tests/test_route_timetable_adjustments.py
git commit -m "feat(schedule): adjust stop times with controlled strategies"
```

## Task 7: Extend schedule validation for stop-time completeness

**Files:**
- Modify: `app/api_planning.py`
- Create: `tests/test_route_timetable_validation.py`

- [ ] **Step 1: Write failing schedule-check tests**

```python
def test_route_check_reports_missing_and_non_monotonic_stop_times(client, route_with_trip):
    route_id, trip_id = route_with_trip
    missing = client.get(f"/api/routes/{route_id}/check?day_type=будни").json()
    assert any(problem["kind"] == "нет поостановочного расписания"
               for problem in missing["problems"])
    seed_non_monotonic_rows(trip_id)
    invalid = client.get(f"/api/routes/{route_id}/check?day_type=будни").json()
    assert any(problem["kind"] == "нарушена последовательность остановок"
               for problem in invalid["problems"])
```

- [ ] **Step 2: Run the validation test and verify it fails**

Run: `python -m pytest tests/test_route_timetable_validation.py -q`

Expected: FAIL because the two problem kinds are absent.

- [ ] **Step 3: Add advisory problem records**

Extend the existing route check without changing old severities. For every route/day trip:

- report missing rows when the count differs from the direction trace count;
- report sequence gaps or duplicates;
- report `arrival_sec < previous departure_sec`;
- report first/last stop times inconsistent with `route_trips.dep_time/arr_time` by more than 59 seconds;
- recommend recalculation or a manual correction strategy.

- [ ] **Step 4: Run validation and all existing schedule checks**

Run: `python -m pytest tests/test_route_timetable_validation.py tests/test_schedule_api.py tests/test_424_advisory_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit validation**

```powershell
git add app/api_planning.py tests/test_route_timetable_validation.py
git commit -m "feat(schedule): validate stop level timetables"
```

## Task 8: Add generation preview and editable matrix to the SPA

**Files:**
- Modify: `static/app.js`
- Modify: `static/styles.css`
- Modify: `static/index.html`
- Create: `tests/test_route_timetable_frontend.py`

- [ ] **Step 1: Write failing static frontend tests**

```python
def test_schedule_workspace_contains_generation_and_matrix_flows():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "scheduleGenerationPreview" in source
    assert "scheduleGenerationApply" in source
    assert "scheduleStopMatrix" in source
    assert "scheduleStopTimeEdit" in source
    assert "scheduleStopOverridesReset" in source
    assert "schedule-generation/preview" in source
    assert "schedule-generation/apply" in source


def test_matrix_styles_and_cache_key_are_present():
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert ".schedule-stop-matrix" in styles
    assert ".schedule-stop-time-manual" in styles
    assert ".schedule-generation-diff" in styles
    assert "route=3.5" in index
```

- [ ] **Step 2: Run frontend tests and verify failure**

Run: `python -m pytest tests/test_route_timetable_frontend.py -q`

Expected: FAIL because the generation and matrix functions are absent.

- [ ] **Step 3: Add preview/apply controls**

Keep the old `/api/trips/generate` button under a clearly labelled compatibility action. Add:

- `scheduleGenerationPreview()` with output count and terminal layover inputs;
- a diff panel showing old/new trip counts, output demand, warnings, and first/last service times;
- Cancel and Apply buttons;
- explicit text that preview has not changed saved trips;
- refresh of trips, checks, summary, and matrix only after apply succeeds.

- [ ] **Step 4: Add the stop-time matrix**

Implement `scheduleStopMatrix(data)` with:

- direction, output, and period filters;
- sticky trip headers and sticky stop names;
- horizontally scrollable cells;
- separate arrival/departure text when dwell is non-zero;
- a manual marker and tooltip;
- click-to-edit modal with the three approved strategies and mandatory reason;
- reset buttons for trip, output, and day scopes;
- no direct DOM mutation after save: reload data from the API.

- [ ] **Step 5: Add responsive, print, and cache styles**

Add `.schedule-stop-matrix`, `.schedule-stop-cell`, `.schedule-stop-time-manual`, `.schedule-generation-diff`, `.schedule-matrix-filters`, mobile overflow, sticky headers, and print-safe rules. Change the shared route cache suffix from `route=3.4` to `route=3.5`.

- [ ] **Step 6: Run frontend and API regression tests**

Run: `python -m pytest tests/test_route_timetable_frontend.py tests/test_schedule_period_ui.py tests/test_route_timetable_api.py tests/test_schedule_api.py -q`

Expected: PASS.

- [ ] **Step 7: Check JavaScript and commit the UI**

Run: `node --check static/app.js`

Expected: exit code 0.

```powershell
git add static/app.js static/styles.css static/index.html tests/test_route_timetable_frontend.py
git commit -m "feat(schedule): add editable stop time matrix ui"
```

## Task 9: Export stop, trip, and route timetable forms

**Files:**
- Modify: `app/api_route_timetable.py`
- Create: `tests/test_route_timetable_exports.py`

- [ ] **Step 1: Write failing Excel export tests**

```python
def test_route_matrix_and_trip_exports_have_stop_headers(client, generated_route):
    route_response = client.get(
        f"/api/routes/{generated_route}/stop-times/export.xlsx?day_type=будни"
    )
    assert route_response.status_code == 200
    route_book = load_workbook(io.BytesIO(route_response.content))
    assert route_book.active["A1"].value.startswith("Поостановочное расписание")
    assert "Остановка" in [cell.value for cell in route_book.active[3]]


def test_stop_pavilion_export_lists_all_departures(client, generated_stop):
    response = client.get(
        f"/api/stops/{generated_stop}/timetable.xlsx?day_type=будни"
    )
    assert response.status_code == 200
    book = load_workbook(io.BytesIO(response.content))
    assert book.active["A1"].value.startswith("Расписание остановки")
```

- [ ] **Step 2: Run export tests and verify 404 failures**

Run: `python -m pytest tests/test_route_timetable_exports.py -q`

Expected: FAIL because export endpoints are absent.

- [ ] **Step 3: Implement three workbook forms**

Add:

- `GET /api/routes/{route_id}/stop-times/export.xlsx?day_type=&direction=&output_number=` for the route matrix;
- `GET /api/trips/{trip_id}/stop-times/export.xlsx` for the driver’s trip sheet;
- `GET /api/stops/{stop_id}/timetable.xlsx?day_type=` for all route departures through a pavilion stop.

Use `openpyxl` with merged titles, route/day metadata, frozen panes, print area, repeated header rows, landscape matrix pages, portrait stop pages, borders, alternating fills, and minute-formatted values. Do not change existing schedule exports.

- [ ] **Step 4: Run export and existing workbook tests**

Run: `python -m pytest tests/test_route_timetable_exports.py tests/test_schedule_api.py tests/test_roster_excel_export.py -q`

Expected: PASS.

- [ ] **Step 5: Commit exports**

```powershell
git add app/api_route_timetable.py tests/test_route_timetable_exports.py
git commit -m "feat(schedule): export stop level timetables"
```

## Task 10: Verify Stage 3 end to end

**Files:**
- Modify: this plan only to mark evidence-backed checkboxes after verification

- [ ] **Step 1: Run the complete test suite**

Run: `python -m pytest tests -q`

Expected: all tests pass.

- [ ] **Step 2: Run static checks**

```powershell
node --check static/app.js
node --check static/route-card.js
python -m compileall -q app
git diff --check
git status --short
```

Expected: all commands exit 0 and only intentional plan-state changes remain.

- [ ] **Step 3: Verify schema and atomic apply on a database copy**

Create an online SQLite backup of `atp.db`, set `ATP_DB` to the copy, call `db.init_db()` twice, generate a preview, verify unchanged trip counts, apply once, verify matching `route_trips`/matrix counts, reject a second apply, and run `PRAGMA integrity_check`.

Expected: `integrity_check=ok`, repeat-safe migration, no preview writes, one-time atomic apply.

- [ ] **Step 4: Verify the browser workflow on the copy**

1. Open route 104 or 44 and verify both traces have positive segment times.
2. Save morning/day/evening periods for `будни`.
3. Preview a three-output schedule and inspect the diff without changing saved trips.
4. Apply and confirm the old trip table, summary, checks, and timeline refresh.
5. Open the stop matrix and filter direction/output.
6. Try an invalid isolated correction and confirm no cell changes.
7. Apply each valid strategy and confirm the manual marker.
8. Reset one trip’s corrections and confirm recalculated times.
9. Export route, trip, and pavilion workbooks and open them.
10. Confirm orders, rosters, and waybill screens still load the applied trips.

- [ ] **Step 5: Merge only after fresh verification**

Use `superpowers:verification-before-completion`, then `superpowers:requesting-code-review`, then `superpowers:finishing-a-development-branch`. Merge into `main` only after the full suite passes on the feature branch and again on the merged result.

## Stage 3 completion gate

Stage 3 is complete only when:

- every generated trip has a complete, monotonic stop-time sequence;
- period factors and explicit segment runtimes produce deterministic tested times;
- generation preview does not mutate saved trips;
- apply is atomic, one-time, expiring, and bound to user/route/day;
- applied rows remain compatible with summaries, checks, rosters, orders, and waybills;
- all three manual strategies validate before commit and preserve audit metadata;
- reset works by trip, output, and day;
- route, trip, and pavilion exports render from persisted stop times;
- full tests, database-copy verification, static checks, and the browser workflow pass.

Stage 4 begins only after this gate and adds shift types, output structures, two-driver long runs, and shift assignments. Stage 5 adds versioned labor rules, break/layover optimization, violations, approvals, and normative exports.
