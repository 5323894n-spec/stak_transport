# Roster Multi-Shift Assignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one driver have multiple route shift assignments on the same date, with automatic schedule loading and dismissible overtime warnings.

**Architecture:** Keep `roster` as the day-level aggregate and add `roster_assignments` as child work segments. New roster APIs calculate schedule options from `route_trips`, save/delete assignments, refresh the parent aggregate, and order generation prefers the detailed assignments.

**Tech Stack:** FastAPI, SQLite, pytest/TestClient, vanilla JavaScript SPA.

---

### Task 1: Assignment Schema and Schedule Options

**Files:**
- Modify: `app/db.py`
- Modify: `app/api_planning.py`
- Create: `tests/test_roster_multi_shift_api.py`

- [ ] **Step 1: Write failing tests**

Add tests that create a route with `route_trips` for two shifts and assert:
- `GET /api/roster/schedule-options` returns the resolved day type, outputs/shifts, trips, and suggested start/end.
- A trip range recalculates start/end, trips count, distance, break minutes, and hours.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_roster_multi_shift_api.py::test_roster_schedule_options_suggests_shift_from_trips -q`

Expected: fail with `404 Not Found`.

- [ ] **Step 3: Implement schema and options API**

Add `roster_assignments` to `db.SCHEMA`. Add helpers in `api_planning.py` for selecting trips and calculating assignment metrics. Add `GET /api/roster/schedule-options`.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_roster_multi_shift_api.py::test_roster_schedule_options_suggests_shift_from_trips -q`

Expected: pass.

### Task 2: Save/Delete Assignments and Overtime Warnings

**Files:**
- Modify: `app/api_planning.py`
- Test: `tests/test_roster_multi_shift_api.py`

- [ ] **Step 1: Write failing tests**

Add tests that save two assignments for the same driver/date, verify both rows exist, verify the parent `roster` row aggregates total hours, and verify saving returns violations instead of blocking when hours exceed the norm.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_roster_multi_shift_api.py -q`

Expected: assignment tests fail because endpoints are missing.

- [ ] **Step 3: Implement assignment APIs**

Add:
- `GET /api/roster/assignments`
- `POST /api/roster/assignment`
- `DELETE /api/roster/assignment/{aid}`

Saving/deleting refreshes the parent `roster` row and returns day-level violations.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_roster_multi_shift_api.py -q`

Expected: pass.

### Task 3: Order Generation Uses Assignments

**Files:**
- Modify: `app/api_planning.py`
- Test: `tests/test_roster_multi_shift_api.py`

- [ ] **Step 1: Write failing test**

Add a test that creates one driver with two assignments on the same date for shift 1 and shift 2, generates the daily order, and asserts both order lines use the same driver.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_roster_multi_shift_api.py::test_order_generation_uses_two_assignments_for_same_driver -q`

Expected: fail because order generation still blocks reuse through `used_drivers`.

- [ ] **Step 3: Update order generation**

For each route/output/shift, look for `roster_assignments` first. If found, use that assignment driver and metrics. Only use the old `used_drivers` fallback for legacy roster rows.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_roster_multi_shift_api.py::test_order_generation_uses_two_assignments_for_same_driver -q`

Expected: pass.

### Task 4: Roster UI Assignment Editor

**Files:**
- Modify: `static/app.js`
- Modify: `static/styles.css` if layout needs compact assignment styles

- [ ] **Step 1: Replace roster cell modal**

Change `rosterCell` from a simple `formModal` to a modal with:
- day status controls
- existing assignments list
- `+ смена`
- route/output/shift controls
- trip range controls
- editable start/end time
- warning panel that can be closed

- [ ] **Step 2: Wire schedule loading**

When route/output/shift/trip range changes, call `/api/roster/schedule-options` and apply suggested start/end/hours.

- [ ] **Step 3: Wire save/delete**

Saving an assignment calls `/api/roster/assignment`. Deleting calls `/api/roster/assignment/{id}`. If violations are returned, show them in a dismissible panel and keep the modal usable.

- [ ] **Step 4: Verify JavaScript syntax**

Run: `node --check static\app.js`

Expected: exit code `0`.

### Task 5: Full Verification

**Files:**
- Test: `tests/test_roster_multi_shift_api.py`
- Test: `tests/test_schedule_api.py`
- Test: `tests/test_erm_route_import.py`

- [ ] **Step 1: Run backend tests**

Run: `python -m pytest tests/test_roster_multi_shift_api.py tests/test_schedule_api.py tests/test_erm_route_import.py -q`

Expected: all tests pass.

- [ ] **Step 2: Run JS syntax check**

Run: `node --check static\app.js`

Expected: exit code `0`.

- [ ] **Step 3: Browser check**

Open the roster screen, verify the cell modal shows assignments, route/output/shift loading works, and dismissible warnings do not block continued editing.
