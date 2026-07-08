# Schedule Breaks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add editable break categories in the schedule module and make saved breaks automatically push the next trip chain to the required start time.

**Architecture:** The backend remains the source of truth for time calculations, break normalization, automatic cascading shifts, and route validation. The frontend only exposes break categories as segmented tabs, shows break-specific colors on the timeline, and displays how many later trips were shifted after save.

**Tech Stack:** FastAPI, SQLite, pytest/TestClient, vanilla JavaScript, CSS.

---

### Task 1: Backend Tests For Break-Aware Scheduling

**Files:**
- Modify: `tests/test_schedule_api.py`

- [ ] **Step 1: Write failing test for automatic chain shift**

Add `test_trip_break_after_save_shifts_following_trip_chain` that creates three trips on one route/output/shift, updates the first trip with `arr_time=10:00`, `break_after_min=40`, `break_type="обед"`, then expects the next two trips to move by the same delta so trip 2 starts at `10:40`.

- [ ] **Step 2: Write failing test for break gap validation**

Add `test_route_check_reports_break_gap_violation` that creates an intentionally invalid sequence directly in the database: first trip arrives at `10:00` with a 40-minute lunch, second trip departs at `10:12`, then expects `/api/routes/{id}/check` to return kind `break_gap`.

- [ ] **Step 3: Run the two tests and verify RED**

Run: `pytest tests/test_schedule_api.py::test_trip_break_after_save_shifts_following_trip_chain tests/test_schedule_api.py::test_route_check_reports_break_gap_violation -q`
Expected: both tests fail because automatic shift and `break_gap` validation are not implemented yet.

### Task 2: Backend Break Logic

**Files:**
- Modify: `app/api_planning.py`

- [ ] **Step 1: Add break constants and normalization helper**

Add canonical break types `обед`, `разрыв`, `технологический перерыв`, preserve legacy aliases `обед/пересменка` and `технологический`, and keep only `обед`/`разрыв` as unpaid break types.

- [ ] **Step 2: Add automatic chain shift helper**

After saving a trip, find later trips in the same route/day/output/shift ordered by time. If the current trip has an arrival time and positive scheduled break, calculate `required_start = arr_time + break_after_min`; if the next trip is earlier, shift that next trip and every later trip in the same chain by the delta, preserving durations.

- [ ] **Step 3: Add validation for required break gap**

In `schedule_problems`, when consecutive trips on the same output do not respect the explicit break after the previous trip, add an `ошибка` with kind `break_gap`, attached to the next trip.

- [ ] **Step 4: Run backend tests and verify GREEN**

Run: `pytest tests/test_schedule_api.py -q`
Expected: all schedule tests pass.

### Task 3: Frontend Break Tabs And Timeline Colors

**Files:**
- Modify: `static/app.js`
- Modify: `static/styles.css`

- [ ] **Step 1: Add segmented field support to modal forms**

Extend `field()` and `modal()` so fields with `type: "segments"` render clickable tabs backed by a hidden input with `data-k`.

- [ ] **Step 2: Use break tabs in `tripEdit`**

Replace the break type select with tabs: `нет`, `обед`, `разрыв`, `технологический перерыв`. Normalize old values before opening the modal.

- [ ] **Step 3: Show save feedback and break colors**

After `/api/trips` save, show shifted trip count if returned. In the timeline, color lunch, split, and technological break trips with separate CSS classes.

- [ ] **Step 4: Verify JavaScript syntax**

Run: `node --check static/app.js`
Expected: exit code 0.

### Task 4: Final Verification

**Files:**
- Existing test and app files only.

- [ ] **Step 1: Run regression tests**

Run: `pytest tests/test_schedule_api.py tests/test_roster_multi_shift_api.py tests/test_erm_route_import.py -q`
Expected: all tests pass.

- [ ] **Step 2: Restart or use the local server and verify UI in browser**

Open schedule route, open a trip, confirm break tabs are visible, save a 40-minute lunch, and confirm the table/timeline reload without JavaScript errors.
