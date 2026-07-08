# ERM Route Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a route-specific import that reads ERM `.xlsx` workbooks and loads all route information into the existing route directory.

**Architecture:** Add a focused parser module for ERM workbook structure, call it from `app/api_refs.py`, store detailed imported sections in route `notes`, and add a dedicated `Импорт ЭРМ` control to the existing routes view.

**Tech Stack:** FastAPI, SQLite, openpyxl, vanilla JavaScript SPA, pytest/TestClient.

---

### Task 1: Parser Test and Parser Module

**Files:**
- Create: `app/erm_import.py`
- Test: `tests/test_erm_route_import.py`

- [ ] **Step 1: Write the failing parser test**

Create a workbook in memory with sheets `параметры`, `из парка`, `в парк`. Assert that `parse_erm_route_workbook()` returns route number `1`, route name, forward/backward stops, distances, times, and all sections.

- [ ] **Step 2: Run parser test to verify RED**

Run: `python -m pytest tests/test_erm_route_import.py::test_parse_erm_route_workbook_extracts_route_and_sections -q`

Expected: fail with `ModuleNotFoundError: No module named 'app.erm_import'`.

- [ ] **Step 3: Implement minimal parser**

Add `parse_erm_route_workbook(data: bytes) -> dict` in `app/erm_import.py`.

The parser should:
- Open bytes with `openpyxl.load_workbook(..., data_only=True, read_only=True)`.
- Require sheet `параметры`.
- Extract route title using regex `Маршрут № <number> "<name>"`.
- Parse each header section where column B equals `п.п.`.
- Stop a section when the next header, direction marker, or blank body begins.
- Convert cumulative meters to kilometers and `HH:MM:SS`/time values to minutes.
- Return route fields and a JSON-ready `details` object.

- [ ] **Step 4: Run parser test to verify GREEN**

Run: `python -m pytest tests/test_erm_route_import.py::test_parse_erm_route_workbook_extracts_route_and_sections -q`

Expected: pass.

### Task 2: API Import Endpoint

**Files:**
- Modify: `app/api_refs.py`
- Test: `tests/test_erm_route_import.py`

- [ ] **Step 1: Write failing endpoint tests**

Add tests for `POST /api/import/routes/erm`:
- creating a route from ERM workbook
- updating an existing route with the same number
- preserving depot section data in `notes`

- [ ] **Step 2: Run endpoint tests to verify RED**

Run: `python -m pytest tests/test_erm_route_import.py -q`

Expected: fail with `404 Not Found` for `/api/import/routes/erm`.

- [ ] **Step 3: Implement endpoint**

Add `@router.post("/import/routes/erm")` to `app/api_refs.py`.

Behavior:
- Require write access to `routes`.
- Read uploaded file.
- Parse with `parse_erm_route_workbook`.
- Upsert route by `number`.
- Increment version on update.
- Write route fields and JSON import payload into `notes`.
- Audit as `импорт ЭРМ`.
- Return `{route_id, created, updated, route, summary}`.

- [ ] **Step 4: Run endpoint tests to verify GREEN**

Run: `python -m pytest tests/test_erm_route_import.py -q`

Expected: pass.

### Task 3: Routes UI Button

**Files:**
- Modify: `static/app.js`

- [ ] **Step 1: Add dedicated ERM upload control**

In `refView(kind)`, render an extra file input only when `kind === "routes"`:

`Импорт ЭРМ`

- [ ] **Step 2: Add `routeErmImport(input)`**

Upload selected file to `/api/import/routes/erm` with `FormData`, show route number/name and created/updated status in toast, reload references, and rerender.

- [ ] **Step 3: Verify JavaScript syntax**

Run: `node --check static\app.js`

Expected: exit code `0`.

### Task 4: Regression Verification

**Files:**
- Test: `tests/test_schedule_api.py`
- Test: `tests/test_erm_route_import.py`

- [ ] **Step 1: Run focused tests**

Run: `python -m pytest tests/test_erm_route_import.py tests/test_schedule_api.py -q`

Expected: all tests pass.

- [ ] **Step 2: Manual browser check**

Open `http://127.0.0.1:8000/#/routes`, verify the `Импорт ЭРМ` button is visible, upload the sample workbook, verify the route appears/updates, and open the route edit modal to confirm route fields are populated.

- [ ] **Step 3: Final status**

Report changed files, test results, and whether the running app server was restarted.
