# Modern Schedule and ERM Exports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add modern, printable schedule and ERM Excel documents plus editable depot-out/depot-in route sections and separate day/night runtimes.

**Architecture:** Store depot sections in a dedicated normalized table and migrate the existing route runtime into explicit day/night columns without changing current trip generation. Build neutral document data in a focused service, render both workbooks through shared OpenPyXL style helpers, and expose the feature through a new route-documents API and route-card UI.

**Tech Stack:** Python 3, FastAPI, SQLite, OpenPyXL, vanilla JavaScript/CSS, pytest, `@oai/artifact-tool` for final visual verification.

---

## File map

- Modify `app/route_schema.py`: schema, idempotent columns, and runtime backfill.
- Create `app/route_depot.py`: validation, normalized storage, cumulative calculations, and legacy-notes fallback.
- Create `app/api_route_documents.py`: depot CRUD and both document-download endpoints.
- Create `app/route_document_data.py`: neutral schedule/ERM data models read from SQLite.
- Create `app/route_document_xlsx.py`: shared style system and workbook builders.
- Modify `app/main.py`: register the route-documents router.
- Modify `app/api_refs.py`: persist imported ERM depot sections in normalized storage.
- Modify `app/api_route_network.py`: expose day/night runtimes and protect stops used by depot sections.
- Modify `static/route-card.js`: depot editor and export dialog.
- Modify `static/styles.css`: responsive depot editor and export-dialog styling.
- Modify `static/index.html`: frontend cache-key bumps.
- Create `tests/test_route_document_schema.py`: migration coverage.
- Create `tests/test_route_depot_api.py`: CRUD, validation, authorization, and legacy fallback.
- Modify `tests/test_erm_route_import.py`: normalized depot import coverage.
- Create `tests/test_route_document_data.py`: neutral data-model coverage.
- Create `tests/test_route_schedule_document.py`: schedule workbook contract.
- Create `tests/test_route_erm_export.py`: ERM workbook contract.
- Create `tests/test_route_documents_frontend.py`: UI/static contract coverage.

### Task 1: Persist depot sections and day/night runtimes

**Files:**
- Modify: `app/route_schema.py:28-49,250-290`
- Create: `tests/test_route_document_schema.py`

- [ ] **Step 1: Write the failing schema migration test**

```python
def test_route_document_schema_backfills_day_and_night_runtime(tmp_path, monkeypatch):
    from app import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "schema.db"))
    db.init_db()
    con = db.connect()
    route_id = con.execute("INSERT INTO routes(number,name) VALUES('44','Test')").lastrowid
    stop_id = con.execute("INSERT INTO stops(name) VALUES('A')").lastrowid
    con.execute(
        "INSERT INTO route_stops(route_id,direction,stop_id,sequence,run_time_sec) "
        "VALUES(?,?,?,?,?)",
        (route_id, "forward", stop_id, 1, 125),
    )
    con.commit()
    from app.route_schema import migrate_route_network
    migrate_route_network(con)
    row = con.execute(
        "SELECT run_time_day_sec,run_time_night_sec FROM route_stops WHERE route_id=?",
        (route_id,),
    ).fetchone()
    assert tuple(row) == (125, 125)
    depot_columns = {r[1] for r in con.execute("PRAGMA table_info(route_depot_stops)")}
    assert {"direction", "run_time_day_sec", "run_time_night_sec"} <= depot_columns
```

- [ ] **Step 2: Run the new test and confirm RED**

Run: `python -m pytest tests/test_route_document_schema.py -q`

Expected: FAIL because `run_time_day_sec`, `run_time_night_sec`, and `route_depot_stops` do not exist.

- [ ] **Step 3: Add the idempotent schema and deterministic backfill**

Add to `ROUTE_NETWORK_SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS route_depot_stops(
  id INTEGER PRIMARY KEY,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  direction TEXT NOT NULL CHECK(direction IN ('depot_out','depot_in')),
  stop_id INTEGER NOT NULL REFERENCES stops(id),
  sequence INTEGER NOT NULL,
  distance_from_prev_km REAL NOT NULL DEFAULT 0 CHECK(distance_from_prev_km >= 0),
  run_time_day_sec INTEGER NOT NULL DEFAULT 0 CHECK(run_time_day_sec >= 0),
  run_time_night_sec INTEGER NOT NULL DEFAULT 0 CHECK(run_time_night_sec >= 0),
  source TEXT NOT NULL DEFAULT 'manual',
  source_detail TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(route_id,direction,sequence)
);
CREATE INDEX IF NOT EXISTS idx_route_depot_stops_stop
  ON route_depot_stops(stop_id);
```

In `migrate_route_network` add:

```python
_add_column(con, "route_stops", "run_time_day_sec", "INTEGER NOT NULL DEFAULT 0 CHECK(run_time_day_sec >= 0)")
_add_column(con, "route_stops", "run_time_night_sec", "INTEGER NOT NULL DEFAULT 0 CHECK(run_time_night_sec >= 0)")
con.execute(
    "UPDATE route_stops SET run_time_day_sec=run_time_sec "
    "WHERE run_time_day_sec=0 AND run_time_sec>0"
)
con.execute(
    "UPDATE route_stops SET run_time_night_sec=run_time_sec "
    "WHERE run_time_night_sec=0 AND run_time_sec>0"
)
```

- [ ] **Step 4: Run schema and existing route migration tests**

Run: `python -m pytest tests/test_route_document_schema.py tests/test_route_migration.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/route_schema.py tests/test_route_document_schema.py
git commit -m "feat(routes): store depot sections and runtime variants"
```

### Task 2: Add depot-section domain service and API

**Files:**
- Create: `app/route_depot.py`
- Create: `app/api_route_documents.py`
- Modify: `app/main.py:8-34,67-92`
- Modify: `app/api_route_network.py:129-145,172-230`
- Create: `tests/test_route_depot_api.py`

- [ ] **Step 1: Write failing API tests for GET, PUT, validation, and permissions**

```python
def test_replace_and_read_depot_out(client, admin_headers, route_with_stops):
    route_id, stop_ids = route_with_stops
    payload = {"items": [
        {"stop_id": stop_ids[0], "sequence": 1, "distance_from_prev_km": 0,
         "run_time_day_sec": 0, "run_time_night_sec": 0},
        {"stop_id": stop_ids[1], "sequence": 2, "distance_from_prev_km": 1.25,
         "run_time_day_sec": 180, "run_time_night_sec": 150},
    ]}
    saved = client.put(
        f"/api/routes/{route_id}/depot-stops/depot_out",
        json=payload,
        headers=admin_headers,
    )
    assert saved.status_code == 200
    assert saved.json()["items"][1]["cumulative_km"] == 1.25
    assert saved.json()["items"][1]["cumulative_day_sec"] == 180
    assert saved.json()["items"][1]["cumulative_night_sec"] == 150
    loaded = client.get(
        f"/api/routes/{route_id}/depot-stops?direction=depot_out",
        headers=admin_headers,
    )
    assert loaded.json()["items"][1]["stop"]["id"] == stop_ids[1]

def test_depot_validation_rejects_negative_values(client, admin_headers, route_with_stops):
    route_id, stop_ids = route_with_stops
    response = client.put(
        f"/api/routes/{route_id}/depot-stops/depot_in",
        json={"items": [{"stop_id": stop_ids[0], "sequence": 1,
                         "distance_from_prev_km": -1,
                         "run_time_day_sec": 0, "run_time_night_sec": 0}]},
        headers=admin_headers,
    )
    assert response.status_code == 400
    assert "Расстояние" in response.json()["detail"]
```

Also assert that a read-only user receives `403` on PUT and that deletion of a stop used by `route_depot_stops` returns `409`.

- [ ] **Step 2: Run the API tests and confirm RED**

Run: `python -m pytest tests/test_route_depot_api.py -q`

Expected: FAIL with `404` for the missing endpoints.

- [ ] **Step 3: Implement the focused depot service**

In `app/route_depot.py` define the stable interface:

```python
DIRECTIONS = ("depot_out", "depot_in")

def validate_direction(direction):
    if direction not in DIRECTIONS:
        raise ValueError("Направление должно быть depot_out или depot_in")

def normalize_items(items):
    if not isinstance(items, list):
        raise ValueError("Поле items должно быть списком")
    result, km, day, night = [], 0.0, 0, 0
    for expected, source in enumerate(items, 1):
        if int(source.get("sequence") or expected) != expected:
            raise ValueError("Последовательность остановок должна быть непрерывной")
        distance = float(source.get("distance_from_prev_km") or 0)
        day_sec = int(source.get("run_time_day_sec") or 0)
        night_sec = int(source.get("run_time_night_sec") or 0)
        if distance < 0 or day_sec < 0 or night_sec < 0:
            raise ValueError("Расстояние и время не могут быть отрицательными")
        km, day, night = km + distance, day + day_sec, night + night_sec
        result.append({**source, "sequence": expected,
                       "distance_from_prev_km": round(distance, 3),
                       "cumulative_km": round(km, 3),
                       "cumulative_day_sec": day,
                       "cumulative_night_sec": night})
    return result
```

Add `get_depot_rows(con, route_id, direction, legacy_fallback=True)` and `replace_depot_rows(con, route_id, direction, items, source="manual")`. The getter joins `stops`, computes cumulative fields with `normalize_items`, and uses parsed `routes.notes.details.sheets` only when the normalized table is empty.

- [ ] **Step 4: Add the router and register it**

Implement in `app/api_route_documents.py`:

```python
router = APIRouter(prefix="/api")

@router.get("/routes/{route_id}/depot-stops")
def depot_stops(route_id: int, direction: str, user=Depends(current_user)):
    ...

@router.put("/routes/{route_id}/depot-stops/{direction}")
def depot_stops_replace(route_id: int, direction: str,
                        payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "routes")
    ...
```

Register `route_documents_router` in `app/main.py`. Extend the stop deletion guard in `app/api_route_network.py` to check both `route_stops` and `route_depot_stops`. Return saved rows only after `con.commit()` succeeds.

- [ ] **Step 5: Run depot API and route-network tests**

Run: `python -m pytest tests/test_route_depot_api.py tests/test_route_network_api.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/route_depot.py app/api_route_documents.py app/main.py app/api_route_network.py tests/test_route_depot_api.py
git commit -m "feat(routes): edit depot route sections"
```

### Task 3: Normalize ERM depot data during import

**Files:**
- Modify: `app/api_refs.py:112-170`
- Modify: `tests/test_erm_route_import.py:71-170`

- [ ] **Step 1: Extend the import test and confirm RED**

Add assertions after the existing ERM import request:

```python
con = db.connect()
rows = db.rows(con.execute(
    "SELECT direction,sequence,run_time_day_sec,run_time_night_sec "
    "FROM route_depot_stops WHERE route_id=? ORDER BY direction,sequence",
    (data["route_id"],),
))
assert [row["direction"] for row in rows] == ["depot_in", "depot_in", "depot_out", "depot_out"]
assert rows[-1]["run_time_day_sec"] == 300
assert rows[-1]["run_time_night_sec"] == 300
```

Run: `python -m pytest tests/test_erm_route_import.py -q`

Expected: FAIL because the importer only stores depot sections in `routes.notes`.

- [ ] **Step 2: Persist imported depot sections after route creation/update**

Map parsed section kinds to normalized directions:

```python
direction_by_kind = {"из парка": "depot_out", "в парк": "depot_in"}
for sheet in parsed["details"]["sheets"].values():
    for section in sheet.get("sections", []):
        direction = direction_by_kind.get(section.get("kind"))
        if not direction:
            continue
        items = depot_items_from_erm(section.get("stops") or [], con)
        replace_depot_rows(con, route_id, direction, items, source="erm_import")
```

Resolve stops by external code first and by normalized name/address second; create a stop only when neither lookup matches. Copy the imported runtime into both day and night values.

- [ ] **Step 3: Verify create and update import behavior**

Run: `python -m pytest tests/test_erm_route_import.py tests/test_route_depot_api.py -q`

Expected: PASS, including replacement rather than duplication on repeated import.

- [ ] **Step 4: Commit**

```bash
git add app/api_refs.py tests/test_erm_route_import.py
git commit -m "feat(routes): normalize imported ERM depot sections"
```

### Task 4: Expose day/night runtimes and build the depot editor

**Files:**
- Modify: `app/api_route_network.py:35-78,172-230`
- Modify: `static/route-card.js:1-160,617-650`
- Modify: `static/styles.css`
- Modify: `static/index.html:36-39`
- Create: `tests/test_route_documents_frontend.py`
- Modify: `tests/test_route_network_api.py`

- [ ] **Step 1: Write failing backend and frontend contract tests**

```python
def test_route_network_returns_and_saves_runtime_variants(client, route_network):
    route_id, stop_ids = route_network
    payload = {"items": [{"stop_id": stop_ids[0], "sequence": 1,
                          "distance_from_prev_km": 0, "run_time_sec": 0,
                          "run_time_day_sec": 70, "run_time_night_sec": 55}]}
    response = client.put(f"/api/routes/{route_id}/stops/forward", json=payload)
    assert response.json()["items"][0]["run_time_day_sec"] == 70
    assert response.json()["items"][0]["run_time_night_sec"] == 55

def test_route_card_contains_depot_editor_and_export_dialog():
    source = Path("static/route-card.js").read_text(encoding="utf-8")
    assert 'tab: "depot"' in source
    assert "/depot-stops" in source
    assert "Нулевые рейсы" in source
    assert "Экспорт документов" in source
```

Run: `python -m pytest tests/test_route_network_api.py tests/test_route_documents_frontend.py -q`

Expected: FAIL on missing runtime persistence and UI strings.

- [ ] **Step 2: Save runtime variants with backward compatibility**

Extend route-stop INSERT/SELECT payloads with `run_time_day_sec` and `run_time_night_sec`. When the client omits a new value, use `run_time_sec`; continue storing `run_time_sec` so the current timetable generator remains unchanged.

- [ ] **Step 3: Add the depot editor tab**

Add state and explicit functions in `static/route-card.js`:

```javascript
async function routeCardLoadDepot(state, direction = state.depotDirection || "depot_out") {
  state.depotDirection = direction;
  const data = await api(`/api/routes/${state.routeId}/depot-stops?direction=${direction}`);
  state.depotDrafts[direction] = data.items.map(item => ({ ...item, stop_id: item.stop.id }));
}

async function routeCardSaveDepot() {
  const state = window._routeCard;
  const direction = state.depotDirection;
  const items = state.depotDrafts[direction].map((item, index) => ({
    stop_id: +item.stop_id,
    sequence: index + 1,
    distance_from_prev_km: +item.distance_from_prev_km || 0,
    run_time_day_sec: +item.run_time_day_sec || 0,
    run_time_night_sec: +item.run_time_night_sec || 0,
  }));
  await api(`/api/routes/${state.routeId}/depot-stops/${direction}`, {
    method: "PUT", body: { items },
  });
  await routeCardLoadDepot(state, direction);
  renderRouteCard(state);
}
```

Reuse the existing stop picker and sortable row conventions. Provide visible labels and inline errors for distance, day runtime, and night runtime.

- [ ] **Step 4: Add responsive and print-safe styles and bump cache keys**

Use `.route-depot-tabs`, `.route-depot-grid`, `.route-depot-row`, and `.route-document-dialog`; collapse numeric fields to a second row under 760 px. Bump both `app.js` route cache and `route-card.js` cache query values in `static/index.html`.

- [ ] **Step 5: Run focused tests and syntax checks**

Run:

```bash
python -m pytest tests/test_route_network_api.py tests/test_route_documents_frontend.py -q
node --check static/route-card.js
node --check static/app.js
```

Expected: PASS with no syntax output.

- [ ] **Step 6: Commit**

```bash
git add app/api_route_network.py static/route-card.js static/styles.css static/index.html tests/test_route_network_api.py tests/test_route_documents_frontend.py
git commit -m "feat(routes): edit depot sections in route card"
```

### Task 5: Build neutral route-document data models and shared Excel styling

**Files:**
- Create: `app/route_document_data.py`
- Create: `app/route_document_xlsx.py`
- Create: `tests/test_route_document_data.py`

- [ ] **Step 1: Write failing tests for document parameters and neutral data**

```python
def test_parse_document_options():
    from app.route_document_data import parse_document_options
    options = parse_document_options("winter", "2025-12-01")
    assert options.season_label == "ЗИМНИЙ ПЕРИОД"
    assert options.file_token == "ЗИМА"
    assert options.effective_date.isoformat() == "2025-12-01"

@pytest.mark.parametrize("season,date", [("autumn", "2025-12-01"), ("winter", "01.12.2025")])
def test_parse_document_options_rejects_invalid_input(season, date):
    with pytest.raises(ValueError):
        parse_document_options(season, date)
```

Add a fixture-backed assertion that `load_route_document_data` returns route identity, forward/backward/depot sections, both day types, ordered outputs, trips, and version without OpenPyXL objects.

- [ ] **Step 2: Run and confirm RED**

Run: `python -m pytest tests/test_route_document_data.py -q`

Expected: FAIL because the data module does not exist.

- [ ] **Step 3: Implement typed option parsing and data loaders**

Define dataclasses `DocumentOptions`, `RouteSection`, `ScheduleTrip`, `ScheduleOutput`, and `RouteDocumentData`. Use bounded SQL queries ordered by direction/sequence and output/trip. Keep all time values as seconds in the neutral model and all distances as numeric kilometres.

- [ ] **Step 4: Implement the shared workbook style surface**

In `app/route_document_xlsx.py` define reusable constants and functions:

```python
NAVY = "17324D"
BLUE = "DCE8F3"
PALE_BLUE = "EEF4FB"
PALE_GREEN = "E9F6EE"
PALE_AMBER = "FFF3DD"

def apply_sheet_setup(ws, *, landscape=True):
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.oddFooter.center.text = "Страница &P из &N"

def write_excel_time(cell, seconds):
    cell.value = seconds / 86400 if seconds is not None else None
    cell.number_format = "[h]:mm"
```

Also define `write_title_band`, `write_section_header`, `write_table_header`, `apply_warning_cell`, `set_print_area`, and `_xlsx_download_response`. Do not expose endpoint concerns from this module.

- [ ] **Step 5: Run model tests and style smoke test**

Run: `python -m pytest tests/test_route_document_data.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/route_document_data.py app/route_document_xlsx.py tests/test_route_document_data.py
git commit -m "feat(routes): prepare modern document data and styles"
```

### Task 6: Generate the modern schedule workbook

**Files:**
- Modify: `app/route_document_xlsx.py`
- Modify: `app/api_route_documents.py`
- Create: `tests/test_route_schedule_document.py`

- [ ] **Step 1: Write the failing workbook contract test**

```python
def test_schedule_document_has_modern_three_sheet_contract(client, populated_route):
    route_id = populated_route
    response = client.get(
        f"/api/routes/{route_id}/schedule-document.xlsx"
        "?season=winter&effective_date=2025-12-01"
    )
    assert response.status_code == 200
    assert "Расписание_М044_20251201_ЗИМА.xlsx" in unquote(
        response.headers["content-disposition"]
    )
    wb = load_workbook(io.BytesIO(response.content), data_only=False)
    assert wb.sheetnames == ["Рабочие дни", "Выходные дни", "Хронометраж"]
    ws = wb["Рабочие дни"]
    assert ws.sheet_view.showGridLines is False
    assert ws.page_setup.orientation == "landscape"
    assert ws.freeze_panes is not None
    assert ws["A1"].value == "МАРШРУТНОЕ РАСПИСАНИЕ"
    assert any(cell.data_type == "f" for row in ws.iter_rows() for cell in row)
```

Add tests for a missing weekend schedule, real Excel time number formats, long route names, hidden helper columns, and the absence of formula-error literals.

- [ ] **Step 2: Run and confirm RED**

Run: `python -m pytest tests/test_route_schedule_document.py -q`

Expected: FAIL with `404` for `schedule-document.xlsx`.

- [ ] **Step 3: Implement the schedule sheets**

Add `build_schedule_workbook(data, options)` that creates exactly three worksheets. For each day-type sheet:

1. write the title band and five KPI cells;
2. group chronological trips by output;
3. create enough paired terminal columns for the longest output;
4. write depot-out/depot-in cells and output totals;
5. write source metrics to hidden helper columns beyond the print range;
6. write KPI/output formulas against those helpers;
7. add the explicit empty-state block when no trips exist;
8. apply print titles, freeze panes, widths, row heights, and print area.

Build `Хронометраж` from forward, backward, depot-out, and depot-in sections. Use formulas for cumulative day/night time and distance.

- [ ] **Step 4: Add the download endpoint**

```python
@router.get("/routes/{route_id}/schedule-document.xlsx")
def schedule_document(route_id: int, season: str, effective_date: str,
                      user=Depends(current_user)):
    try:
        options = parse_document_options(season, effective_date)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    con = db.connect()
    try:
        data = load_route_document_data(con, route_id)
        wb = build_schedule_workbook(data, options)
        return workbook_response(wb, schedule_filename(data.route, options))
    finally:
        con.close()
```

- [ ] **Step 5: Run schedule document and legacy export tests**

Run: `python -m pytest tests/test_route_schedule_document.py tests/test_schedule_api.py -q`

Expected: PASS; the legacy `schedule-export.xlsx` remains unchanged.

- [ ] **Step 6: Commit**

```bash
git add app/route_document_xlsx.py app/api_route_documents.py tests/test_route_schedule_document.py
git commit -m "feat(routes): export modern schedule workbook"
```

### Task 7: Generate the modern ERM workbook

**Files:**
- Modify: `app/route_document_xlsx.py`
- Modify: `app/api_route_documents.py`
- Create: `tests/test_route_erm_export.py`

- [ ] **Step 1: Write the failing ERM workbook contract test**

```python
def test_erm_export_has_parameters_and_depot_sheets(client, populated_route):
    response = client.get(
        f"/api/routes/{populated_route}/erm-export.xlsx"
        "?season=summer&effective_date=2026-06-01"
    )
    assert response.status_code == 200
    wb = load_workbook(io.BytesIO(response.content), data_only=False)
    assert wb.sheetnames == ["Параметры", "Из парка", "В парк"]
    assert wb["Параметры"]["A1"].value == "ЭЛЕКТРОННАЯ МОДЕЛЬ МАРШРУТА"
    headings = [cell.value for cell in wb["Параметры"][5]]
    assert headings == ["№", "ID", "Остановочный пункт", "Улица", "Широта",
                        "Долгота", "День между ОП", "День нарастающим",
                        "Ночь между ОП", "Ночь нарастающим",
                        "Расстояние между ОП", "Расстояние нарастающим"]
```

Add tests for numeric coordinates, formula-based cumulative values, warning fills for missing ID/coordinates, and `Нулевой рейс не заполнен` on an empty depot sheet.

- [ ] **Step 2: Run and confirm RED**

Run: `python -m pytest tests/test_route_erm_export.py -q`

Expected: FAIL with `404` for `erm-export.xlsx`.

- [ ] **Step 3: Implement the ERM workbook and endpoint**

Add `build_erm_workbook(data, options)` and a matching endpoint. Write forward and backward sections consecutively on `Параметры`; use the same `write_route_section_table` helper for all three sheets. Apply warning fill plus `Примечание: отсутствуют технические данные` when external ID or coordinates are missing. Do not write zero coordinates for missing values.

- [ ] **Step 4: Run ERM export and import regression tests**

Run: `python -m pytest tests/test_route_erm_export.py tests/test_erm_route_import.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/route_document_xlsx.py app/api_route_documents.py tests/test_route_erm_export.py
git commit -m "feat(routes): export modern ERM workbook"
```

### Task 8: Add the route-card export dialog

**Files:**
- Modify: `static/route-card.js`
- Modify: `static/styles.css`
- Modify: `static/index.html`
- Modify: `tests/test_route_documents_frontend.py`

- [ ] **Step 1: Extend frontend contracts and confirm RED**

```python
def test_route_document_dialog_validates_and_uses_new_endpoints():
    source = Path("static/route-card.js").read_text(encoding="utf-8")
    assert "routeCardDocumentDialog" in source
    assert "schedule-document.xlsx" in source
    assert "erm-export.xlsx" in source
    assert 'value="winter"' in source
    assert 'value="summer"' in source
    assert 'type="date"' in source
```

Run: `python -m pytest tests/test_route_documents_frontend.py -q`

Expected: FAIL because the export dialog is not implemented.

- [ ] **Step 2: Implement the dialog and download action**

```javascript
function routeCardDocumentDownload() {
  const routeId = window._routeCard.routeId;
  const kind = document.querySelector("[data-document-kind]:checked").value;
  const season = document.querySelector("[data-document-season]").value;
  const effectiveDate = document.querySelector("[data-document-date]").value;
  if (!effectiveDate) return toast("Укажите дату начала действия", true);
  const endpoint = kind === "erm" ? "erm-export.xlsx" : "schedule-document.xlsx";
  const query = new URLSearchParams({ season, effective_date: effectiveDate });
  openWin(`/api/routes/${routeId}/${endpoint}?${query}`);
}
```

Default to `winter` and today, keep one primary download button, and close the modal after the browser starts the download.

- [ ] **Step 3: Style the dialog, bump cache keys, and check keyboard behavior**

Ensure labels are associated with controls, focus enters the dialog, Escape/cancel closes without download, and the layout becomes one column on narrow screens.

- [ ] **Step 4: Run frontend tests and syntax checks**

Run:

```bash
python -m pytest tests/test_route_documents_frontend.py -q
node --check static/route-card.js
node --check static/app.js
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add static/route-card.js static/styles.css static/index.html tests/test_route_documents_frontend.py
git commit -m "feat(routes): download schedule and ERM documents"
```

### Task 9: End-to-end verification and visual QA

**Files:**
- Modify only if verification finds a defect in files already listed above.

- [ ] **Step 1: Run all focused tests**

Run:

```bash
python -m pytest tests/test_route_document_schema.py tests/test_route_depot_api.py tests/test_route_document_data.py tests/test_route_schedule_document.py tests/test_route_erm_export.py tests/test_route_documents_frontend.py tests/test_erm_route_import.py tests/test_route_network_api.py -q
```

Expected: all focused tests PASS.

- [ ] **Step 2: Generate realistic verification workbooks**

Start the local app with a temporary database, create a route containing long stop names, missing coordinates, both day types, uneven trip counts, and both depot sections, then download both endpoints for `season=winter&effective_date=2025-12-01` into a conversation-specific output directory.

- [ ] **Step 3: Inspect and render every sheet with the spreadsheet workflow**

Import each generated workbook with `@oai/artifact-tool`, inspect key ranges and formulas, scan for `#REF!|#DIV/0!|#VALUE!|#NAME\?|#N/A`, and render all six sheets. Confirm title bands, KPI visibility, table headers, warnings, widths, row heights, print area, and absence of clipping. Fix only concrete visual defects and repeat the affected render.

- [ ] **Step 4: Perform browser verification**

Open a route card, edit and save both depot directions, reload to verify persistence, open the export dialog, validate the missing-date error, and download both document types. Confirm that navigation away from the card leaves no stale modal or draft state.

- [ ] **Step 5: Run the complete project verification**

Run:

```bash
python -m pytest tests -q
python -m compileall -q app
node --check static/app.js
node --check static/route-card.js
git diff --check
git status --short --branch
```

Expected: full test suite PASS, both syntax checks emit no output, `git diff --check` emits no output, and the worktree is clean after the final commit.

- [ ] **Step 6: Request final code review and commit any verified corrections**

Review the complete range from the pre-plan base commit through HEAD against `docs/superpowers/specs/2026-07-27-modern-schedule-erm-exports-design.md`. Fix every Critical or Important finding with a failing regression test first, rerun the complete verification, and use a focused commit message describing the correction.
