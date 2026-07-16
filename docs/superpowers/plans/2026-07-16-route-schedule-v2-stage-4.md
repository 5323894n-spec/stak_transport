# Route Schedule V2 Stage 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable shift types, deterministic output-to-shift splitting, two-driver long outputs, manual shift boundaries, and compatibility with existing roster assignments.

**Architecture:** Keep `route_trips` and `roster_assignments` as the compatibility sources used by orders and waybills. Add persisted `output_shifts` that group contiguous trips of one output, preview/apply generation bound to route/day/user, and nullable links from legacy rows. Stage 4 creates shift structure and driver slots; versioned labor rules, optimized breaks, violations, approvals, and normative exports remain Stage 5.

**Tech Stack:** Python 3.14, FastAPI, SQLite, vanilla JavaScript SPA, openpyxl, pytest.

---

## Scope and compatibility rules

- A shift covers a contiguous range of trips on exactly one route, day type, and output.
- Applying a shift preview updates `route_trips.shift_number` and `route_trips.output_shift_id` atomically.
- Existing roster, order, summary, and waybill consumers continue to use `route_trips` and `roster_assignments`.
- `roster_assignments.output_shift_id` links a dated driver assignment to the generated structural shift without replacing the existing assignment record.
- A normal shift has one driver slot. A two-driver shift has two driver slots but remains one structural shift and one output.
- Automatic splitting occurs only between trips and only where the available gap is at least the configured handover duration.
- A manual locked shift boundary survives preview/apply until explicitly reset.
- Preview never writes `output_shifts`, `route_trips`, or `roster_assignments`.
- Stage 4 does not decide legal compliance. It reports structural conflicts such as uncovered trips, overlap, excessive configured duration, or missing handover gaps.

## File map

- Modify `app/route_schema.py`: shift tables, preview table, and additive compatibility columns.
- Create `app/route_shifts.py`: pure grouping, duration, boundary scoring, and validation.
- Create `app/api_route_shifts.py`: shift type CRUD, settings, preview/apply, manual boundary, reset, and export.
- Modify `app/main.py`: register the Stage 4 router.
- Modify `app/api_planning.py`: expose structural shifts through schedule/roster options and persist `output_shift_id` in assignments.
- Modify `static/app.js`: shift settings, preview/apply, output-shift cards, and driver-slot indicators.
- Modify `static/styles.css` and `static/index.html`: shift timeline styles and cache key `route=3.6`.
- Add focused tests per task below.

## Task 1: Add repeat-safe shift schema and defaults

**Files:**
- Modify: `app/route_schema.py`
- Create: `tests/test_route_shift_schema.py`

- [ ] **Step 1: Write the failing repeat-safe schema test**

```python
def test_stage_four_shift_schema_and_defaults_are_repeat_safe(tmp_path):
    from app import db
    db.DB_PATH = str(tmp_path / "stage-four.db")
    db.init_db()
    db.init_db()
    con = db.connect()
    try:
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"shift_types", "route_shift_settings", "output_shifts",
                "shift_generation_previews"} <= tables
        assert con.execute("SELECT COUNT(*) FROM shift_types").fetchone()[0] == 4
        trip_columns = {row[1] for row in con.execute(
            "PRAGMA table_info(route_trips)"
        )}
        roster_columns = {row[1] for row in con.execute(
            "PRAGMA table_info(roster_assignments)"
        )}
        assert "output_shift_id" in trip_columns
        assert "output_shift_id" in roster_columns
    finally:
        con.close()
```

- [ ] **Step 2: Run the schema test and verify the expected failure**

Run: `python -m pytest tests/test_route_shift_schema.py -q`

Expected: FAIL because `shift_types` is absent.

- [ ] **Step 3: Add Stage 4 tables**

Append to `ROUTE_NETWORK_SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS shift_types(
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  work_pattern TEXT NOT NULL DEFAULT 'custom',
  planned_duration_min INTEGER NOT NULL CHECK(planned_duration_min > 0),
  max_duration_min INTEGER NOT NULL CHECK(max_duration_min >= planned_duration_min),
  driver_slots INTEGER NOT NULL DEFAULT 1 CHECK(driver_slots IN (1,2)),
  allow_split INTEGER NOT NULL DEFAULT 0,
  color TEXT NOT NULL DEFAULT '#2563eb',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS route_shift_settings(
  id INTEGER PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  day_type TEXT NOT NULL,
  default_shift_type_id INTEGER NOT NULL REFERENCES shift_types(id),
  long_shift_type_id INTEGER REFERENCES shift_types(id),
  handover_min INTEGER NOT NULL DEFAULT 10 CHECK(handover_min >= 0),
  long_run_threshold_min INTEGER NOT NULL DEFAULT 720 CHECK(long_run_threshold_min > 0),
  auto_split INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  UNIQUE(route_id,day_type)
);

CREATE TABLE IF NOT EXISTS output_shifts(
  id INTEGER PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  day_type TEXT NOT NULL,
  output_number INTEGER NOT NULL,
  shift_number INTEGER NOT NULL,
  shift_type_id INTEGER NOT NULL REFERENCES shift_types(id),
  trip_from_id INTEGER NOT NULL REFERENCES route_trips(id) ON DELETE CASCADE,
  trip_to_id INTEGER NOT NULL REFERENCES route_trips(id) ON DELETE CASCADE,
  start_sec INTEGER NOT NULL,
  end_sec INTEGER NOT NULL CHECK(end_sec > start_sec),
  driver_slots INTEGER NOT NULL DEFAULT 1 CHECK(driver_slots IN (1,2)),
  handover_after_min INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'generated',
  is_manual_locked INTEGER NOT NULL DEFAULT 0,
  manual_reason TEXT,
  generation_key TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(route_id,day_type,output_number,shift_number)
);
CREATE INDEX IF NOT EXISTS idx_output_shifts_scope
  ON output_shifts(route_id,day_type,output_number,shift_number);

CREATE TABLE IF NOT EXISTS shift_generation_previews(
  token TEXT PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  day_type TEXT NOT NULL,
  username TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  applied_at TEXT
);
```

Use `_add_column` to add:

```python
_add_column(con, "route_trips", "output_shift_id",
            "INTEGER REFERENCES output_shifts(id)")
_add_column(con, "roster_assignments", "output_shift_id",
            "INTEGER REFERENCES output_shifts(id)")
```

Seed exactly these codes with `INSERT OR IGNORE`: `single_8h`, `single_12h`, `split`, and `two_driver_long`. Use 480/600, 720/780, 480/600, and 900/1080 planned/max minutes respectively; `two_driver_long.driver_slots=2`.

- [ ] **Step 4: Run schema and migration regression tests**

Run: `python -m pytest tests/test_route_shift_schema.py tests/test_route_timetable_schema.py tests/test_route_migration.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the schema**

```powershell
git add app/route_schema.py tests/test_route_shift_schema.py
git commit -m "feat(schedule): add route shift schema"
```

## Task 2: Build the pure output-shift splitter

**Files:**
- Create: `app/route_shifts.py`
- Create: `tests/test_route_shifts_unit.py`

- [ ] **Step 1: Write failing pure-domain tests**

```python
from app.route_shifts import build_output_shifts, validate_output_shift_plan


def trip(trip_id, dep, arr):
    return {"id": trip_id, "dep_sec": dep, "arr_sec": arr,
            "output_number": 1}


def test_splitter_uses_valid_handover_gap_and_covers_every_trip():
    trips = [
        trip(1, 6*3600, 9*3600),
        trip(2, 9*3600+15*60, 13*3600),
        trip(3, 13*3600+20*60, 17*3600),
    ]
    shifts = build_output_shifts(
        trips, shift_type={"id": 1, "planned_duration_min": 480,
                           "max_duration_min": 600, "driver_slots": 1},
        handover_min=10,
    )
    assert [(row["trip_from_id"], row["trip_to_id"])
            for row in shifts] == [(1, 2), (3, 3)]
    assert validate_output_shift_plan(trips, shifts) == []


def test_splitter_rejects_long_output_without_valid_handover():
    trips = [trip(1, 6*3600, 12*3600),
             trip(2, 12*3600+5*60, 18*3600)]
    with pytest.raises(ValueError, match="пересмен"):
        build_output_shifts(
            trips, shift_type={"id": 1, "planned_duration_min": 480,
                               "max_duration_min": 600, "driver_slots": 1},
            handover_min=10,
        )


def test_two_driver_type_keeps_long_output_as_one_shift():
    trips = [trip(1, 6*3600, 12*3600), trip(2, 12*3600+10*60, 20*3600)]
    shifts = build_output_shifts(
        trips, shift_type={"id": 4, "planned_duration_min": 900,
                           "max_duration_min": 1080, "driver_slots": 2},
        handover_min=10,
    )
    assert len(shifts) == 1
    assert shifts[0]["driver_slots"] == 2
```

- [ ] **Step 2: Run the unit tests and verify import failure**

Run: `python -m pytest tests/test_route_shifts_unit.py -q`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement deterministic splitting**

Create `app/route_shifts.py` with two pure functions. `build_output_shifts(trips, *, shift_type, handover_min)` returns contiguous shift dictionaries without mutating its arguments. `validate_output_shift_plan(trips, shifts)` returns structured conflicts for gaps, overlaps, duplicate coverage, uncovered trips, invalid ranges, and mismatched output numbers.

Implementation requirements:

1. sort by `dep_sec,id` and reject overlapping trips;
2. if `driver_slots == 2`, return one shift only when total span is within `max_duration_min`;
3. otherwise choose the latest boundary not exceeding `max_duration_min`, preferring the boundary closest to `planned_duration_min`;
4. a boundary is valid only when the next departure minus the current arrival is at least `handover_min`;
5. each result contains `shift_number`, `trip_from_id`, `trip_to_id`, `start_sec`, `end_sec`, `driver_slots`, and `handover_after_min`;
6. reject plans that do not cover every trip exactly once.

- [ ] **Step 4: Run unit tests**

Run: `python -m pytest tests/test_route_shifts_unit.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the splitter**

```powershell
git add app/route_shifts.py tests/test_route_shifts_unit.py
git commit -m "feat(schedule): split outputs into driver shifts"
```

## Task 3: Add shift types and route settings API

**Files:**
- Create: `app/api_route_shifts.py`
- Modify: `app/main.py`
- Create: `tests/test_route_shift_settings_api.py`

- [ ] **Step 1: Write failing authenticated CRUD tests**

Test `GET/POST /api/shift-types` and `GET/PUT /api/routes/{route_id}/shift-settings/{day_type}`. Assert code uniqueness, positive duration, `max_duration_min >= planned_duration_min`, `driver_slots in (1,2)`, referenced active types, and audit records.

- [ ] **Step 2: Run tests and verify 404 failures**

Run: `python -m pytest tests/test_route_shift_settings_api.py -q`

Expected: FAIL because the router is absent.

- [ ] **Step 3: Register the router and implement contracts**

Implement these contracts:

- `GET /api/shift-types?active_only=true` returns an `items` array ordered by name and code.
- `POST /api/shift-types` accepts code, name, pattern, planned/max duration, driver slots, split flag, color, and active flag; it returns the saved row.
- `GET /api/routes/{route_id}/shift-settings/{day_type}` returns the stored settings or computed defaults without creating a row.
- `PUT /api/routes/{route_id}/shift-settings/{day_type}` accepts default/long type identifiers, handover minutes, long-run threshold, and auto-split; it returns the persisted settings with expanded type names.

Use `require_write(user, "trips")`, return 404 for missing route/type, 400 for invalid durations, and audit every mutation.

- [ ] **Step 4: Run API and permission tests**

Run: `python -m pytest tests/test_route_shift_settings_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit settings API**

```powershell
git add app/api_route_shifts.py app/main.py tests/test_route_shift_settings_api.py
git commit -m "feat(schedule): configure route shift types"
```

## Task 4: Preview and atomically apply output shifts

**Files:**
- Modify: `app/api_route_shifts.py`
- Create: `tests/test_route_shift_generation.py`

- [ ] **Step 1: Write failing preview/apply tests**

Cover:

- preview groups every route/day output without writes;
- preview returns conflicts instead of claiming success when no handover exists;
- two-driver type produces `driver_slots=2`;
- apply is route/day/user-bound, expiring, and one-time;
- apply inserts `output_shifts` and updates every covered `route_trips.shift_number` and `output_shift_id` in one transaction;
- a deliberately corrupted preview rolls back old shifts and trip links.

- [ ] **Step 2: Run tests and verify 404 failures**

Run: `python -m pytest tests/test_route_shift_generation.py -q`

Expected: FAIL because generation endpoints are absent.

- [ ] **Step 3: Implement preview**

Implement `POST /api/routes/{route_id}/shift-generation/preview`. The body contains `day_type` and optional `preserve_locked` (default `true`). The response contains token, expiry, route/day scope, per-output proposed shifts, conflicts, old/new shift counts, and old/new driver-slot totals.

Load `route_trips` ordered by output and service-day seconds, resolve settings/types, call `build_output_shifts` per output, preserve locked shifts when requested, calculate old/new counts and driver-slot totals, and store the plan for 30 minutes under a 32-character token.

- [ ] **Step 4: Implement atomic apply**

Implement `POST /api/routes/{route_id}/shift-generation/apply`. The body contains `day_type` and `token`; the response contains applied shift/trip counts and driver-slot total.

Within one transaction, validate token scope/expiry/use, clear nonlocked route/day shifts, insert generated rows, update covered trips, verify every route/day trip has exactly one `output_shift_id`, mark the preview applied, audit the diff, and commit. Roll back on all validation and integrity failures.

- [ ] **Step 5: Run generation and downstream compatibility tests**

Run: `python -m pytest tests/test_route_shift_generation.py tests/test_schedule_api.py tests/test_roster_multi_shift_api.py tests/test_waybill_modes_api.py -q`

Expected: PASS.

- [ ] **Step 6: Commit generation**

```powershell
git add app/api_route_shifts.py tests/test_route_shift_generation.py
git commit -m "feat(schedule): preview and apply output shifts"
```

## Task 5: Add manual boundaries and locked shifts

**Files:**
- Modify: `app/route_shifts.py`
- Modify: `app/api_route_shifts.py`
- Create: `tests/test_route_shift_manual.py`

- [ ] **Step 1: Write failing boundary tests**

Test moving a boundary to another trip, changing a shift type, mandatory reason, contiguous coverage, overlap rejection with rollback, locking, preview preservation, and reset by one shift, output, or whole day. Conflicting reset scopes must return 400.

- [ ] **Step 2: Run tests and verify endpoint absence**

Run: `python -m pytest tests/test_route_shift_manual.py -q`

Expected: FAIL with 404.

- [ ] **Step 3: Add pure replacement validation**

Add `replace_shift_boundaries(trips, shifts, *, shift_id, trip_from_id, trip_to_id, shift_type)`. It returns a copied plan, replaces only the selected shift range/type, recalculates start/end/driver slots from covered trips, renumbers shifts in trip order, and rejects any result for which `validate_output_shift_plan` reports a conflict. It must never mutate input rows.

- [ ] **Step 4: Add mutation and reset endpoints**

Implement `PATCH /api/output-shifts/{shift_id}` with body fields `trip_from_id`, `trip_to_id`, `shift_type_id`, and required `reason`. Implement `POST /api/routes/{route_id}/output-shifts/reset-manual` with required `day_type`, optional `shift_id`, and optional `output_number`.

Every manual mutation requires a nonblank reason, locks the affected shift, updates trip links/numbers atomically, and audits before/after. Reset accepts only one optional selector: `shift_id` or `output_number`; neither means the full `day_type`.

- [ ] **Step 5: Run manual and generation tests**

Run: `python -m pytest tests/test_route_shift_manual.py tests/test_route_shift_generation.py -q`

Expected: PASS.

- [ ] **Step 6: Commit manual boundaries**

```powershell
git add app/route_shifts.py app/api_route_shifts.py tests/test_route_shift_manual.py
git commit -m "feat(schedule): edit and lock output shift boundaries"
```

## Task 6: Integrate structural shifts with roster assignments

**Files:**
- Modify: `app/api_planning.py`
- Modify: `app/norms.py`
- Create: `tests/test_route_shift_roster_integration.py`

- [ ] **Step 1: Write failing roster integration tests**

Assert schedule options return `output_shift_id`, shift type, structural start/end, and required driver slots. Saving a roster assignment must validate that its route/day/output/shift matches the linked structural shift and persist `output_shift_id`. For two-driver shifts, two distinct assignments are allowed; duplicate driver/slot overlap remains rejected by existing checks.

- [ ] **Step 2: Run tests and verify missing fields**

Run: `python -m pytest tests/test_route_shift_roster_integration.py -q`

Expected: FAIL because structural links are absent from responses and saves.

- [ ] **Step 3: Extend schedule options and assignment save**

Join `output_shifts` and `shift_types` in `/api/roster/schedule-options`, use structural trip ranges as defaults, validate optional `output_shift_id`, and store it in `roster_assignments`. Do not create a second driver-assignment table.

- [ ] **Step 4: Run roster, order, and waybill regressions**

Run: `python -m pytest tests/test_route_shift_roster_integration.py tests/test_roster_multi_shift_api.py tests/test_order_excel_export.py tests/test_waybill_modes_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit roster compatibility**

```powershell
git add app/api_planning.py app/norms.py tests/test_route_shift_roster_integration.py
git commit -m "feat(roster): link assignments to output shifts"
```

## Task 7: Add shift workspace to the schedule SPA

**Files:**
- Modify: `static/app.js`
- Modify: `static/styles.css`
- Modify: `static/index.html`
- Create: `tests/test_route_shift_frontend.py`

- [ ] **Step 1: Write failing static frontend tests**

Assert `scheduleShiftSettings`, `scheduleShiftPreview`, `scheduleShiftApply`, `scheduleOutputShifts`, `scheduleShiftEdit`, `/shift-generation/preview`, `/shift-generation/apply`, `.schedule-output-shifts`, `.schedule-driver-slots`, and cache key `route=3.6` are present.

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/test_route_shift_frontend.py -q`

Expected: FAIL because Stage 4 UI functions are absent.

- [ ] **Step 3: Add settings and preview controls**

Add shift type/default/long-type/handover controls, old/new shift and driver-slot diff, conflicts, Cancel, and Apply. Preview text must state that saved shifts and trip numbers have not changed.

- [ ] **Step 4: Add output-shift cards and manual editor**

Render shifts grouped by output with type color, trip range, time span, duration, one/two driver-slot icons, manual lock marker, roster assignment count, edit boundary action, and reset actions. Reload all schedule data after mutations.

- [ ] **Step 5: Add responsive and print styles**

Add `.schedule-output-shifts`, `.schedule-shift-card`, `.schedule-driver-slots`, `.schedule-shift-locked`, `.schedule-shift-conflict`, responsive stacking, print-safe colors, and update both shared asset URLs from `route=3.5` to `route=3.6`.

- [ ] **Step 6: Run frontend and API tests**

Run: `python -m pytest tests/test_route_shift_frontend.py tests/test_route_timetable_frontend.py tests/test_schedule_period_ui.py tests/test_route_shift_generation.py -q`

Expected: PASS.

- [ ] **Step 7: Check JavaScript and commit**

Run: `node --check static/app.js`

Expected: exit code 0.

```powershell
git add static/app.js static/styles.css static/index.html tests/test_route_shift_frontend.py
git commit -m "feat(schedule): add output shift workspace"
```

## Task 8: Export output and shift sheets

**Files:**
- Modify: `app/api_route_shifts.py`
- Create: `tests/test_route_shift_exports.py`

- [ ] **Step 1: Write failing workbook tests**

Test `GET /api/routes/{route_id}/output-shifts/export.xlsx?day_type=`. Assert title, metadata, headers on row 3, one row per structural shift, driver-slot count, manual marker, frozen panes, repeated print rows, A4 landscape settings, and no replacement of existing stop-time exports.

- [ ] **Step 2: Run tests and verify 404**

Run: `python -m pytest tests/test_route_shift_exports.py -q`

Expected: FAIL because the endpoint is absent.

- [ ] **Step 3: Implement the workbook**

Use the established dark-blue report style. Include route/day, output, shift, type, trip range, start/end, duration, driver slots, handover, source, manual reason, and dated roster-assignment counts. Preserve typed numeric duration/slot cells and print-ready widths.

- [ ] **Step 4: Run export regressions**

Run: `python -m pytest tests/test_route_shift_exports.py tests/test_route_timetable_exports.py tests/test_roster_excel_export.py -q`

Expected: PASS.

- [ ] **Step 5: Commit exports**

```powershell
git add app/api_route_shifts.py tests/test_route_shift_exports.py
git commit -m "feat(schedule): export output shift sheets"
```

## Task 9: Verify Stage 4 end to end

**Files:**
- Modify: this plan only with evidence-backed completion marks

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

Expected: exit code 0 and only intentional plan-state changes.

- [ ] **Step 3: Verify migration and apply on an online database copy**

Run `db.init_db()` twice, seed one short and one long output, confirm preview makes no writes, apply once, verify every trip has one `output_shift_id`, verify a second apply returns 409, and run `PRAGMA integrity_check`.

Expected: repeat-safe migration, unchanged preview counts, complete trip coverage, one-time apply, `integrity_check=ok`.

- [ ] **Step 4: Verify browser workflow**

Open `/schedule`, configure the default and two-driver shift types, preview, inspect conflicts, apply, move and lock one boundary, reset it, and verify the roster assignment dialog receives structural defaults.

- [ ] **Step 5: Review and merge**

Use `superpowers:verification-before-completion`, `superpowers:requesting-code-review`, and `superpowers:finishing-a-development-branch`. Merge into `main` only after the full suite passes on the branch and again on the merged result.

## Stage 4 completion gate

Stage 4 is complete only when:

- every generated route/day trip belongs to exactly one structural output shift;
- normal shifts split only at valid handover gaps;
- two-driver types expose exactly two driver slots without duplicating trips;
- preview is write-free and apply is atomic, scoped, expiring, and one-time;
- manual locked boundaries survive regeneration until reset;
- `route_trips.shift_number` remains compatible with summaries, rosters, orders, and waybills;
- roster assignments link to structural shifts without a second competing assignment model;
- UI and Excel expose shift type, duration, trip range, driver slots, and manual state;
- full tests, static checks, database-copy verification, review, and merged-main tests pass.
