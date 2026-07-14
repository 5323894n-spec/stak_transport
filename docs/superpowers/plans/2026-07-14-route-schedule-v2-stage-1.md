# Route Schedule V2 — Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the route foundation: normalized stops, ordered route traces, segment data, safe migration of existing routes, ERM/Excel/CSV preview imports, and optional OSRM distance previews while preserving current schedule consumers.

**Architecture:** Keep `routes` and legacy stop text fields as the compatibility boundary. Add focused schema, domain, migration, import, and API modules; all calculated/imported changes use preview-then-apply transactions. Add a dedicated route-card frontend file while leaving the generic reference UI available during rollout.

**Tech Stack:** Python 3, FastAPI, SQLite, openpyxl, standard-library CSV/urllib, vanilla JavaScript SPA, pytest/TestClient.

---

## File map

- Create `app/route_schema.py`: idempotent Stage 1 tables, indexes, and schema migration.
- Create `app/route_network.py`: stop normalization, trace validation, totals, and legacy-field synchronization.
- Create `app/route_migration.py`: repeat-safe conversion of legacy text/ERM JSON into normalized records.
- Create `app/route_import.py`: Excel/CSV preview parser and normalized import plan.
- Create `app/osrm.py`: isolated OSRM client and response validation.
- Create `app/api_route_network.py`: authenticated Stage 1 API endpoints.
- Create `static/route-card.js`: route list/card, tabs, stop and segment editing, import and OSRM previews.
- Create `tests/test_route_schema.py`: schema and cascade/index tests.
- Create `tests/test_route_network_api.py`: stop/trace CRUD and validation tests.
- Create `tests/test_route_migration.py`: legacy and ERM migration tests.
- Create `tests/test_route_import_v2.py`: CSV/XLSX preview/apply tests.
- Create `tests/test_route_osrm.py`: mocked OSRM preview/apply tests.
- Modify `app/db.py`: call the Stage 1 schema migration.
- Modify `app/main.py`: register the Stage 1 router.
- Modify `app/api_refs.py`: route ERM writes through the normalized import service after legacy route save.
- Modify `static/index.html`: load `route-card.js` and bump static cache versions.
- Modify `static/app.js`: open route cards from the route list and register `#/routeCard/{id}`.
- Modify `static/styles.css`: route-card, stop grid, map placeholder, and diff-table layouts.
- Modify `tests/test_erm_route_import.py`: assert normalized stops/traces as well as legacy compatibility.

## Task 1: Establish the Stage 1 schema contract

**Files:**
- Create: `tests/test_route_schema.py`
- Create: `app/route_schema.py`
- Modify: `app/db.py:5,319-327`

- [ ] **Step 1: Write the failing schema test**

```python
def test_route_schema_is_idempotent_and_has_required_tables(tmp_path):
    import app.db as db

    db.DB_PATH = str(tmp_path / "route-schema.db")
    db.init_db()
    db.init_db()
    con = db.connect()
    try:
        tables = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"stops", "route_stops", "route_migration_log"} <= tables
        indexes = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert "uq_route_stops_direction_sequence" in indexes
        assert "uq_stops_external_code" in indexes
    finally:
        con.close()
```

- [ ] **Step 2: Run the test and verify the missing-table failure**

Run: `python -m pytest tests/test_route_schema.py -q`

Expected: FAIL because `stops` does not exist.

- [ ] **Step 3: Implement the idempotent schema migration**

Create `app/route_schema.py` with `migrate_route_network(con)` executing tables for:

```sql
CREATE TABLE IF NOT EXISTS stops(
  id INTEGER PRIMARY KEY,
  external_code TEXT,
  name TEXT NOT NULL,
  latitude REAL,
  longitude REAL,
  address TEXT,
  stop_kind TEXT DEFAULT 'обычная',
  is_terminal INTEGER DEFAULT 0,
  has_dispatcher INTEGER DEFAULT 0,
  municipality TEXT,
  registry_flags TEXT DEFAULT '{}',
  source TEXT DEFAULT 'manual',
  active INTEGER DEFAULT 1,
  notes TEXT,
  created_at TEXT,
  updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_stops_external_code
  ON stops(external_code) WHERE external_code IS NOT NULL AND external_code <> '';
CREATE TABLE IF NOT EXISTS route_stops(
  id INTEGER PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  direction TEXT NOT NULL CHECK(direction IN ('forward','backward')),
  stop_id INTEGER NOT NULL REFERENCES stops(id),
  sequence INTEGER NOT NULL,
  distance_from_prev_km REAL DEFAULT 0 CHECK(distance_from_prev_km >= 0),
  cumulative_km REAL DEFAULT 0 CHECK(cumulative_km >= 0),
  run_time_sec INTEGER DEFAULT 0 CHECK(run_time_sec >= 0),
  dwell_time_sec INTEGER DEFAULT 0 CHECK(dwell_time_sec >= 0),
  distance_source TEXT DEFAULT 'manual',
  boarding_allowed INTEGER DEFAULT 1,
  alighting_allowed INTEGER DEFAULT 1,
  is_timing_point INTEGER DEFAULT 0,
  source_detail TEXT,
  created_at TEXT,
  updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_route_stops_direction_sequence
  ON route_stops(route_id,direction,sequence);
CREATE INDEX IF NOT EXISTS idx_route_stops_stop ON route_stops(stop_id);
CREATE TABLE IF NOT EXISTS route_migration_log(
  id INTEGER PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  source_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(route_id,source_hash)
);
```

Import `migrate_route_network` in `app/db.py` and call it after `migrate(con)` and before `con.commit()`.

- [ ] **Step 4: Run schema and full regression tests**

Run: `python -m pytest tests/test_route_schema.py -q`

Expected: PASS.

Run: `python -m pytest tests -q`

Expected: all existing 104 tests plus the new test pass.

- [ ] **Step 5: Commit the schema foundation**

```powershell
git add app/route_schema.py app/db.py tests/test_route_schema.py
git commit -m "feat(routes): add normalized stop and trace schema"
```

## Task 2: Implement stop normalization and trace calculations

**Files:**
- Create: `app/route_network.py`
- Create: `tests/test_route_network_unit.py`

- [ ] **Step 1: Write failing unit tests**

```python
from app.route_network import normalize_stop_name, recalculate_trace


def test_normalize_stop_name_collapses_case_spacing_and_quotes():
    assert normalize_stop_name('  ОП «Автовокзал» ') == 'оп "автовокзал"'


def test_recalculate_trace_builds_cumulative_distance():
    rows = recalculate_trace([
        {"sequence": 1, "distance_from_prev_km": 0},
        {"sequence": 2, "distance_from_prev_km": 1.25},
        {"sequence": 3, "distance_from_prev_km": 0.75},
    ])
    assert [r["cumulative_km"] for r in rows] == [0.0, 1.25, 2.0]
```

- [ ] **Step 2: Verify both tests fail because the module is absent**

Run: `python -m pytest tests/test_route_network_unit.py -q`

Expected: collection FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement pure domain functions**

Implement in `app/route_network.py`:

```python
def normalize_stop_name(value: str) -> str:
    return " ".join((value or "").strip().lower().replace("«", '"').replace("»", '"').split())


def recalculate_trace(items: list[dict]) -> list[dict]:
    ordered = sorted(items, key=lambda item: int(item["sequence"]))
    cumulative = 0.0
    for expected, item in enumerate(ordered, start=1):
        if int(item["sequence"]) != expected:
            raise ValueError("Последовательность остановок должна начинаться с 1 и не иметь пропусков")
        distance = float(item.get("distance_from_prev_km") or 0)
        if distance < 0:
            raise ValueError("Расстояние перегона не может быть отрицательным")
        if expected == 1 and distance != 0:
            raise ValueError("У первой остановки расстояние от предыдущей должно быть равно 0")
        cumulative = round(cumulative + distance, 3)
        item["cumulative_km"] = cumulative
    return ordered
```

Also implement `trace_to_legacy_names(con, route_id, direction)` and `sync_route_legacy_fields(con, route_id)` so compatibility text and total length are derived from normalized rows.

- [ ] **Step 4: Run unit tests**

Run: `python -m pytest tests/test_route_network_unit.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the route-domain functions**

```powershell
git add app/route_network.py tests/test_route_network_unit.py
git commit -m "feat(routes): add trace validation and legacy synchronization"
```

## Task 3: Add stop and route-trace APIs

**Files:**
- Create: `app/api_route_network.py`
- Modify: `app/main.py:7-31,57-80`
- Create: `tests/test_route_network_api.py`

- [ ] **Step 1: Write failing API tests**

Add authenticated tests for:

```python
def test_create_stop_and_replace_route_trace(client, route_id):
    first = client.post("/api/stops", json={"name": "Вокзал", "external_code": "100"})
    second = client.post("/api/stops", json={"name": "Автовокзал", "external_code": "101"})
    assert first.status_code == second.status_code == 200
    response = client.put(f"/api/routes/{route_id}/stops/forward", json={"items": [
        {"stop_id": first.json()["id"], "sequence": 1, "distance_from_prev_km": 0},
        {"stop_id": second.json()["id"], "sequence": 2, "distance_from_prev_km": 1.5},
    ]})
    assert response.status_code == 200
    assert response.json()["total_km"] == 1.5
    route = client.get("/api/refs/routes").json()["items"][0]
    assert route["stops"] == "Вокзал, Автовокзал"


def test_replace_trace_rejects_duplicate_sequence(client, route_id, stop_id):
    response = client.put(f"/api/routes/{route_id}/stops/forward", json={"items": [
        {"stop_id": stop_id, "sequence": 1, "distance_from_prev_km": 0},
        {"stop_id": stop_id, "sequence": 1, "distance_from_prev_km": 1},
    ]})
    assert response.status_code == 400
```

- [ ] **Step 2: Run the API tests and verify 404 failures**

Run: `python -m pytest tests/test_route_network_api.py -q`

Expected: FAIL because `/api/stops` and `/api/routes/{id}/stops/{direction}` are absent.

- [ ] **Step 3: Implement the Stage 1 router**

Create an `/api` router with:

- `GET /stops?q=&active=`;
- `POST /stops`;
- `PUT /stops/{stop_id}`;
- `DELETE /stops/{stop_id}` returning 409 when referenced;
- `GET /routes/{route_id}/network` returning route, both directions, totals, and warnings;
- `PUT /routes/{route_id}/stops/{direction}` replacing one direction in one transaction.

Every write calls `require_write(user, "routes")`, validates route/stop existence, uses parameterized SQL, calls `recalculate_trace`, synchronizes legacy fields, writes `db.audit`, commits on success, and rolls back on failure.

Register `route_network_router` in `app/main.py`.

- [ ] **Step 4: Run focused and regression tests**

Run: `python -m pytest tests/test_route_network_api.py tests/test_schedule_api.py -q`

Expected: PASS; legacy schedule creation still reads route fields.

- [ ] **Step 5: Commit the API layer**

```powershell
git add app/api_route_network.py app/main.py tests/test_route_network_api.py
git commit -m "feat(routes): add stop and route trace api"
```

## Task 4: Migrate legacy text stops repeat-safely

**Files:**
- Create: `app/route_migration.py`
- Create: `tests/test_route_migration.py`
- Modify: `app/route_schema.py`

- [ ] **Step 1: Write failing legacy migration tests**

```python
def test_migrate_legacy_route_is_repeat_safe(db_connection, legacy_route_id):
    from app.route_migration import migrate_route

    first = migrate_route(db_connection, legacy_route_id)
    second = migrate_route(db_connection, legacy_route_id)
    assert first["status"] == "migrated"
    assert second["status"] == "unchanged"
    count = db_connection.execute(
        "SELECT COUNT(*) FROM route_stops WHERE route_id=?", (legacy_route_id,)
    ).fetchone()[0]
    assert count == 5


def test_migration_does_not_merge_ambiguous_same_name_stops(db_connection, ambiguous_route_id):
    from app.route_migration import migrate_route

    result = migrate_route(db_connection, ambiguous_route_id)
    assert result["status"] == "needs_review"
    assert result["ambiguous"]
```

- [ ] **Step 2: Verify the migration tests fail**

Run: `python -m pytest tests/test_route_migration.py -q`

Expected: FAIL because `migrate_route` is absent.

- [ ] **Step 3: Implement source hashing and matching**

`migrate_route(con, route_id)` must:

1. load the route;
2. build a canonical JSON source from `stops`, `stops_back`, and ERM `notes.details`;
3. calculate SHA-256 and return `unchanged` when already logged successfully;
4. prefer ERM external code, coordinates, distance, and runtime;
5. match by external code, then normalized name plus coordinates within 50 metres;
6. mark multiple name-only candidates as `needs_review` without writing a trace;
7. insert/update stops and both directions in one transaction;
8. record counts and warnings in `route_migration_log`.

Add `POST /api/routes/{route_id}/migrate-network` and `POST /api/routes/migrate-network` to the Stage 1 router. The bulk endpoint returns per-route status and never hides partial failures.

- [ ] **Step 4: Run migration and legacy API tests**

Run: `python -m pytest tests/test_route_migration.py tests/test_erm_route_import.py -q`

Expected: PASS.

- [ ] **Step 5: Commit legacy migration**

```powershell
git add app/route_migration.py app/api_route_network.py tests/test_route_migration.py
git commit -m "feat(routes): migrate legacy route stops safely"
```

## Task 5: Normalize ERM imports without losing legacy behavior

**Files:**
- Modify: `app/api_refs.py:111-169`
- Modify: `app/erm_import.py:214-270`
- Modify: `tests/test_erm_route_import.py`

- [ ] **Step 1: Extend the failing ERM assertions**

After the existing import assertions, require:

```python
network = client.get(f"/api/routes/{data['route_id']}/network").json()
assert [row["stop"]["external_code"] for row in network["forward"]] == ["100", "101", "102"]
assert network["forward"][-1]["cumulative_km"] == 1.5
assert network["backward"][-1]["cumulative_km"] == 1.3
```

- [ ] **Step 2: Run and verify the normalized rows are missing**

Run: `python -m pytest tests/test_erm_route_import.py -q`

Expected: FAIL because the normalized network is empty.

- [ ] **Step 3: Apply ERM details through the migration service**

After the route insert/update and before commit, call `import_erm_network(con, route_id, parsed["details"])`. Keep legacy fields and JSON notes exactly as before. If normalized application fails, roll back the route update too and return HTTP 400 with a user-facing message.

Ensure the parser returns external stop IDs as strings and exposes address, coordinates, segment distance, cumulative distance, runtime, source sheet, and section kind.

- [ ] **Step 4: Run ERM and schedule regression tests**

Run: `python -m pytest tests/test_erm_route_import.py tests/test_schedule_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit normalized ERM import**

```powershell
git add app/api_refs.py app/erm_import.py app/route_migration.py tests/test_erm_route_import.py
git commit -m "feat(routes): populate normalized trace from erm import"
```

## Task 6: Add Excel/CSV preview and transactional apply

**Files:**
- Create: `app/route_import.py`
- Create: `tests/test_route_import_v2.py`
- Modify: `app/api_route_network.py`

- [ ] **Step 1: Write failing preview/apply tests**

Use a CSV with headers `direction,sequence,external_code,name,latitude,longitude,address,distance_km,run_time_sec,dwell_time_sec`. Assert preview changes no database rows, reports created/updated/conflicts, and apply creates both directions and synchronizes legacy fields.

```python
preview = client.post(f"/api/routes/{route_id}/network-import/preview", files={
    "file": ("stops.csv", csv_bytes, "text/csv")
})
assert preview.status_code == 200
assert preview.json()["summary"]["created_stops"] == 3
assert client.get(f"/api/routes/{route_id}/network").json()["forward"] == []

apply = client.post(f"/api/routes/{route_id}/network-import/apply", json={
    "preview_token": preview.json()["preview_token"]
})
assert apply.status_code == 200
assert len(client.get(f"/api/routes/{route_id}/network").json()["forward"]) == 3
```

- [ ] **Step 2: Run tests and verify endpoint absence**

Run: `python -m pytest tests/test_route_import_v2.py -q`

Expected: FAIL with 404.

- [ ] **Step 3: Implement deterministic import plans**

Parse CSV with `utf-8-sig`; parse XLSX from the active sheet. Validate required columns, directions, contiguous sequences, coordinates, non-negative distances/times, and first-segment distance zero. Store the canonical preview JSON in a new `route_import_previews` table with token, route, username, created time, expiry time, source name, and payload. Apply only a non-expired preview owned by the current user, in one transaction, then mark it applied.

- [ ] **Step 4: Run import and schema tests**

Run: `python -m pytest tests/test_route_import_v2.py tests/test_route_schema.py -q`

Expected: PASS.

- [ ] **Step 5: Commit tabular import**

```powershell
git add app/route_import.py app/route_schema.py app/api_route_network.py tests/test_route_import_v2.py
git commit -m "feat(routes): add previewed excel and csv trace import"
```

## Task 7: Add isolated OSRM preview with manual fallback

**Files:**
- Create: `app/osrm.py`
- Create: `tests/test_route_osrm.py`
- Modify: `app/api_route_network.py`

- [ ] **Step 1: Write mocked OSRM tests**

Test successful geometry/distance preview, missing coordinates, upstream timeout mapped to 503, malformed response mapped to 502, preview not mutating rows, and apply changing `distance_source` to `auto_osrm`.

```python
def test_osrm_preview_does_not_apply_until_confirmed(client, route_with_coordinates, monkeypatch):
    monkeypatch.setattr("app.osrm.request_route", lambda coordinates: {
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "legs": [{"distance": 1500, "duration": 240}],
    })
    preview = client.post(f"/api/routes/{route_with_coordinates}/osrm/preview/forward")
    assert preview.status_code == 200
    assert preview.json()["diff"][0]["new_distance_km"] == 1.5
```

- [ ] **Step 2: Run tests and verify endpoint absence**

Run: `python -m pytest tests/test_route_osrm.py -q`

Expected: FAIL with 404.

- [ ] **Step 3: Implement the OSRM boundary**

Use `urllib.request` with a configurable base URL from setting `osrm_base_url`, a 10-second timeout, and `overview=full&geometries=geojson&steps=false`. Validate `code == "Ok"`, number of legs, numeric non-negative distances/durations, and LineString geometry. Never call OSRM during database startup or ordinary route reads.

Expose preview/apply endpoints. Apply uses the stored preview token, updates distances and runtimes, marks source `auto_osrm`, recalculates cumulative values, synchronizes legacy fields, and audits the diff. Manual edits later set source back to `manual`.

- [ ] **Step 4: Run OSRM tests**

Run: `python -m pytest tests/test_route_osrm.py tests/test_route_network_api.py -q`

Expected: PASS without real network access.

- [ ] **Step 5: Commit OSRM support**

```powershell
git add app/osrm.py app/api_route_network.py tests/test_route_osrm.py
git commit -m "feat(routes): add optional osrm trace preview"
```

## Task 8: Build the route-card frontend

**Files:**
- Create: `static/route-card.js`
- Modify: `static/app.js:1416-1478`
- Modify: `static/index.html:5,34-35`
- Modify: `static/styles.css`

- [ ] **Step 1: Add a route-card smoke test**

Create `tests/test_route_card_frontend.py` that logs in, asserts `/` references `route-card.js`, asserts the file is served, and checks the source contains `VIEWS.routeCard`, `routeCardOpen`, and API paths `/api/routes/` and `/network`.

- [ ] **Step 2: Run the test and verify the missing script failure**

Run: `python -m pytest tests/test_route_card_frontend.py -q`

Expected: FAIL because `route-card.js` is absent.

- [ ] **Step 3: Implement the card and route list entry point**

Replace `VIEWS.routes = refView("routes")` with a route-specific list containing actions `Карточка`, `изменить`, and delete. `Карточка` navigates to `#/routeCard/{id}`.

`static/route-card.js` registers `VIEWS.routeCard` and renders tabs:

- passport;
- stops/directions;
- map/trace;
- segments/time;
- migration/import history.

The stops tab edits an in-memory ordered list and sends the whole direction through `PUT /api/routes/{id}/stops/{direction}`. The map tab displays a coordinate-aware schematic in Stage 1 and the returned OSRM GeoJSON summary; it must not claim to show a geographic basemap until one is integrated. Import and OSRM always render a diff table and require a second explicit Apply action.

- [ ] **Step 4: Add responsive styles and run frontend/API tests**

Add `.route-card`, `.route-card-head`, `.route-tabs`, `.route-stop-row`, `.route-map`, `.route-diff`, status badge, narrow-screen stacking, and print-safe rules using existing CSS variables.

Run: `python -m pytest tests/test_route_card_frontend.py tests/test_route_network_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the route-card UI**

```powershell
git add static/route-card.js static/app.js static/index.html static/styles.css tests/test_route_card_frontend.py
git commit -m "feat(routes): add route network card ui"
```

## Task 9: Verify migration on a database copy and browser workflow

**Files:**
- Modify: `docs/superpowers/plans/2026-07-14-route-schedule-v2-stage-1.md` checkbox state only

- [ ] **Step 1: Run the full automated suite from the project root**

Run: `python -m pytest tests -q`

Expected: all tests pass; do not run bare `pytest -q` while nested worktrees exist.

- [ ] **Step 2: Create and exercise a database copy**

Copy `atp.db` to a temporary file, set `ATP_DB` for a one-shot `db.init_db()` plus bulk network migration, then query counts and per-route statuses. Never run the first migration experiment against the live database.

Expected: no failed routes; ambiguous routes are reported as `needs_review`; second run reports `unchanged` and counts remain stable.

- [ ] **Step 3: Run the manual browser scenario**

1. Start the app on an available local port.
2. Log in as admin.
3. Open «Маршруты» and a route card.
4. Verify both directions and totals.
5. Add, edit, reorder, and remove a stop in a test route.
6. Preview CSV/XLSX import and cancel; verify no data changed.
7. Preview again and apply; verify legacy route fields changed consistently.
8. Test OSRM unavailable state and confirm manual editing still works.
9. Open «Расписание маршрутов» and verify the route remains selectable and generation still works.

- [ ] **Step 4: Run final Git and formatting checks**

Run: `git diff --check`

Expected: no whitespace errors.

Run: `git status --short`

Expected: only intentional Stage 1 changes before the final commit.

- [ ] **Step 5: Commit Stage 1 verification notes if any tracked evidence changed**

```powershell
git add docs/superpowers/plans/2026-07-14-route-schedule-v2-stage-1.md
git commit -m "test(routes): verify stage one migration and workflow"
```

## Stage 1 completion gate

Stage 1 is complete only when:

- the full test suite passes;
- legacy route and schedule tests pass unchanged;
- migration is repeat-safe on a copy of the working database;
- ambiguous matches are reported, not silently merged;
- ERM, CSV, XLSX, and OSRM changes use preview then transactional apply;
- manual operation remains available without OSRM;
- the route card is verified in the browser;
- the live database is backed up before the production migration.

After this gate, write `2026-07-14-route-schedule-v2-stage-2.md` for periods, interval transitions, templates, and bus-demand calculations before modifying Stage 2 code.
