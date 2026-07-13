# Vehicle Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full vehicle dossier with repair details, parts, workers, costs, maintenance, incidents, damages, photos, timeline, printable PDF-ready HTML, and a styled Excel export.

**Architecture:** Keep existing buses and repair records as sources of truth. Add focused incident/damage tables and extend existing repair attachments for the vehicle gallery; expose one lightweight summary plus lazy-loaded tab APIs. Keep reporting in a separate module and preserve the closed-repair snapshot for immutable history.

**Tech Stack:** FastAPI, SQLite, vanilla JavaScript SPA, HTML/CSS print styles, openpyxl, pytest, FastAPI TestClient.

---

## File map

- Modify `app/repair_schema.py`: idempotent incident, damage, media, and snapshot migrations.
- Modify `app/repair_service.py`: vehicle-card permissions and shared validation.
- Create `app/api_vehicle_incidents.py`: incident and damage lifecycle.
- Create `app/api_vehicle_card.py`: summary, lazy-loaded tabs, costs, and timeline.
- Create `app/api_vehicle_media.py`: gallery upload, metadata, cover selection, cancellation, and download reuse.
- Create `app/vehicle_card_reports.py`: print/PDF-ready HTML and vehicle-specific Excel.
- Modify `app/api_repair_dashboard.py`: remove the old minimal card endpoint after its replacement is registered.
- Modify `app/main.py`: register the three focused routers and report router.
- Modify `static/app.js`: full-page card route, tabs, forms, uploads, search, and exports.
- Modify `static/styles.css`: card, gallery, timeline, tabs, incident, and print-friendly layout.
- Modify `static/index.html`: increment the client cache key after UI changes.
- Create `tests/test_vehicle_card_schema.py`, `tests/test_vehicle_incidents_api.py`, `tests/test_vehicle_card_api.py`, `tests/test_vehicle_media.py`, `tests/test_vehicle_card_reports.py`, and `tests/test_vehicle_card_ui.py`.
- Modify `docs/Ремонт_и_ТО_инструкция.md` and `README.md`: operator workflow and storage/backup notes.

### Task 1: Idempotent incident, damage, media, and snapshot schema

**Files:**
- Modify: `app/repair_schema.py`
- Create: `tests/test_vehicle_card_schema.py`

- [ ] **Step 1: Write the failing schema test**

```python
# tests/test_vehicle_card_schema.py
from app.repair_schema import migrate_repairs


def test_vehicle_card_schema_is_idempotent(tmp_path):
    import app.db as db

    db.DB_PATH = str(tmp_path / "vehicle-card.db")
    db.init_db()
    con = db.connect()
    try:
        migrate_repairs(con)
        migrate_repairs(con)
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"vehicle_incidents", "vehicle_damages"} <= tables
        incident_columns = {row[1] for row in con.execute("PRAGMA table_info(vehicle_incidents)")}
        assert {"bus_id", "incident_type", "occurred_at", "repair_request_id", "repair_order_id", "actual_damage_cost", "cancel_reason"} <= incident_columns
        damage_columns = {row[1] for row in con.execute("PRAGMA table_info(vehicle_damages)")}
        assert {"incident_id", "area", "description", "severity", "resolved", "repair_order_id"} <= damage_columns
        media_columns = {row[1] for row in con.execute("PRAGMA table_info(repair_attachments)")}
        assert {"incident_id", "damage_id", "caption", "captured_at", "is_cover", "cancelled_at", "cancel_reason"} <= media_columns
        history_columns = {row[1] for row in con.execute("PRAGMA table_info(vehicle_repair_history)")}
        assert {"labor_cost", "parts_cost", "external_cost", "other_cost", "master_name"} <= history_columns
    finally:
        con.close()
```

- [ ] **Step 2: Run the test and verify the expected failure**

Run: `python -m pytest tests/test_vehicle_card_schema.py -q`

Expected: FAIL because `vehicle_incidents` and `vehicle_damages` do not exist.

- [ ] **Step 3: Add the two tables and indexes to `REPAIR_SCHEMA`**

```sql
CREATE TABLE IF NOT EXISTS vehicle_incidents(
  id INTEGER PRIMARY KEY,
  bus_id INTEGER NOT NULL,
  incident_type TEXT NOT NULL CHECK(incident_type IN('ДТП','повреждение','вандализм','страховой случай')),
  occurred_at TEXT NOT NULL,
  place TEXT,
  route_id INTEGER,
  waybill_id INTEGER,
  driver_id INTEGER,
  circumstances TEXT NOT NULL,
  participants TEXT,
  other_vehicle TEXT,
  fault_status TEXT DEFAULT 'не установлена',
  police_document_number TEXT,
  insurer TEXT,
  insurance_case_number TEXT,
  responsible_user_id INTEGER,
  status TEXT DEFAULT 'зарегистрировано',
  estimated_damage_cost REAL DEFAULT 0 CHECK(estimated_damage_cost>=0),
  actual_damage_cost REAL DEFAULT 0 CHECK(actual_damage_cost>=0),
  repair_request_id INTEGER,
  repair_order_id INTEGER,
  comment TEXT,
  created_by INTEGER,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT,
  cancelled_at TEXT,
  cancel_reason TEXT,
  FOREIGN KEY(bus_id) REFERENCES buses(id),
  FOREIGN KEY(route_id) REFERENCES routes(id),
  FOREIGN KEY(waybill_id) REFERENCES waybills(id),
  FOREIGN KEY(driver_id) REFERENCES drivers(id),
  FOREIGN KEY(responsible_user_id) REFERENCES users(id),
  FOREIGN KEY(repair_request_id) REFERENCES repair_requests(id),
  FOREIGN KEY(repair_order_id) REFERENCES repair_orders(id),
  FOREIGN KEY(created_by) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS vehicle_damages(
  id INTEGER PRIMARY KEY,
  incident_id INTEGER NOT NULL,
  area TEXT NOT NULL,
  description TEXT NOT NULL,
  severity TEXT DEFAULT 'средняя' CHECK(severity IN('незначительная','средняя','тяжёлая','критическая')),
  repair_required INTEGER DEFAULT 1 CHECK(repair_required IN(0,1)),
  resolved INTEGER DEFAULT 0 CHECK(resolved IN(0,1)),
  resolved_at TEXT,
  repair_order_id INTEGER,
  comment TEXT,
  FOREIGN KEY(incident_id) REFERENCES vehicle_incidents(id) ON DELETE CASCADE,
  FOREIGN KEY(repair_order_id) REFERENCES repair_orders(id)
);
CREATE INDEX IF NOT EXISTS idx_vehicle_incidents_bus_date ON vehicle_incidents(bus_id,occurred_at);
CREATE INDEX IF NOT EXISTS idx_vehicle_incidents_order ON vehicle_incidents(repair_order_id);
CREATE INDEX IF NOT EXISTS idx_vehicle_damages_incident ON vehicle_damages(incident_id,resolved);
```

- [ ] **Step 4: Add safe column migrations**

```python
CARD_MIGRATIONS = [
    ("repair_attachments", "incident_id", "INTEGER REFERENCES vehicle_incidents(id)"),
    ("repair_attachments", "damage_id", "INTEGER REFERENCES vehicle_damages(id)"),
    ("repair_attachments", "caption", "TEXT"),
    ("repair_attachments", "captured_at", "TEXT"),
    ("repair_attachments", "is_cover", "INTEGER DEFAULT 0 CHECK(is_cover IN(0,1))"),
    ("repair_attachments", "cancelled_at", "TEXT"),
    ("repair_attachments", "cancel_reason", "TEXT"),
    ("vehicle_repair_history", "labor_cost", "REAL DEFAULT 0"),
    ("vehicle_repair_history", "parts_cost", "REAL DEFAULT 0"),
    ("vehicle_repair_history", "external_cost", "REAL DEFAULT 0"),
    ("vehicle_repair_history", "other_cost", "REAL DEFAULT 0"),
    ("vehicle_repair_history", "master_name", "TEXT"),
]

def add_missing_columns(con, migrations):
    for table, column, definition in migrations:
        columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

# inside migrate_repairs, after executescript:
add_missing_columns(con, BUS_MIGRATIONS)
add_missing_columns(con, CARD_MIGRATIONS)
con.execute("CREATE INDEX IF NOT EXISTS idx_repair_attachments_incident ON repair_attachments(incident_id)")
con.execute("CREATE INDEX IF NOT EXISTS idx_repair_attachments_cover ON repair_attachments(bus_id,is_cover,cancelled_at)")
```

- [ ] **Step 5: Run schema tests**

Run: `python -m pytest tests/test_vehicle_card_schema.py tests/test_repair_schema.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the schema slice**

```powershell
git add app/repair_schema.py tests/test_vehicle_card_schema.py
git commit -m "feat: add vehicle incident and gallery schema"
```

### Task 2: Incident and damage lifecycle

**Files:**
- Modify: `app/repair_service.py`
- Create: `app/api_vehicle_incidents.py`
- Modify: `app/main.py`
- Create: `tests/test_vehicle_incidents_api.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
# tests/test_vehicle_incidents_api.py
from tests.test_repair_requests_api import make_client


def incident_payload():
    return {
        "incident_type": "ДТП",
        "occurred_at": "2026-07-13T08:30:00",
        "place": "ул. Центральная, 10",
        "circumstances": "Касательное столкновение при выезде",
        "fault_status": "не установлена",
        "estimated_damage_cost": 150000,
        "create_repair_request": True,
        "damages": [
            {"area": "правый борт", "description": "Вмятина и царапины", "severity": "средняя"},
            {"area": "передняя дверь", "description": "Разбито стекло", "severity": "тяжёлая"},
        ],
    }


def test_incident_creates_damages_and_linked_repair_request(tmp_path):
    client, bus_id = make_client(tmp_path)
    response = client.post(f"/api/repairs/vehicles/{bus_id}/incidents", json=incident_payload())
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["incident_type"] == "ДТП"
    assert item["repair_request_id"]
    assert [row["area"] for row in item["damages"]] == ["правый борт", "передняя дверь"]
    request = client.get(f"/api/repairs/requests/{item['repair_request_id']}").json()
    assert request["incident_id"] == item["id"]


def test_incident_cancel_requires_reason_and_keeps_record(tmp_path):
    client, bus_id = make_client(tmp_path)
    incident = client.post(f"/api/repairs/vehicles/{bus_id}/incidents", json=incident_payload()).json()
    assert client.post(f"/api/repairs/incidents/{incident['id']}/cancel", json={"reason": ""}).status_code == 400
    cancelled = client.post(f"/api/repairs/incidents/{incident['id']}/cancel", json={"reason": "Дублирующая запись"})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "отменено"
    listed = client.get(f"/api/repairs/vehicles/{bus_id}/incidents?include_cancelled=true").json()["items"]
    assert any(row["id"] == incident["id"] for row in listed)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_vehicle_incidents_api.py -q`

Expected: FAIL with 404 because the incident routes are not registered.

- [ ] **Step 3: Add permission actions**

```python
# app/repair_service.py
REPAIR_ACTIONS.update({
    "manage_incident": {"админ", "мастер ремонта", "диспетчер", "механик"},
    "add_vehicle_media": {"админ", "мастер ремонта", "диспетчер", "механик", "слесарь"},
})
# extend existing read_reports:
REPAIR_ACTIONS["read_reports"] = {"админ", "руководитель", "бухгалтер"}
```

- [ ] **Step 4: Implement the focused router**

Create `app/api_vehicle_incidents.py` with these public routes and transactional behavior:

```python
router = APIRouter(prefix="/api/repairs", tags=["vehicle-incidents"])

@router.get("/vehicles/{bus_id}/incidents")
def list_incidents(bus_id: int, include_cancelled: bool = False, user=Depends(current_user)):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM buses WHERE id=?", (bus_id,)).fetchone():
            raise HTTPException(404, "Автобус не найден")
        where = "vi.bus_id=?" if include_cancelled else "vi.bus_id=? AND vi.cancelled_at IS NULL"
        items = db.rows(con.execute(
            "SELECT vi.*,d.fio driver_name,u.full_name responsible_name,ro.number repair_order_number "
            "FROM vehicle_incidents vi LEFT JOIN drivers d ON d.id=vi.driver_id "
            "LEFT JOIN users u ON u.id=vi.responsible_user_id "
            "LEFT JOIN repair_orders ro ON ro.id=vi.repair_order_id "
            f"WHERE {where} ORDER BY vi.occurred_at DESC,vi.id DESC", (bus_id,),
        ))
        for item in items:
            item["damages"] = db.rows(con.execute(
                "SELECT * FROM vehicle_damages WHERE incident_id=? ORDER BY id", (item["id"],)
            ))
        return {"items": items}
    finally:
        con.close()

@router.post("/vehicles/{bus_id}/incidents", status_code=201)
def create_incident(bus_id: int, payload: dict = Body(...), user=Depends(current_user)):
    require_repair_action(user, "manage_incident")
    if payload.get("incident_type") not in {"ДТП", "повреждение", "вандализм", "страховой случай"}:
        raise HTTPException(400, "Неверный тип события")
    if not payload.get("occurred_at") or not str(payload.get("circumstances") or "").strip():
        raise HTTPException(400, "Укажите дату и обстоятельства события")
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM buses WHERE id=?", (bus_id,)).fetchone():
            raise HTTPException(404, "Автобус не найден")
        incident_id = con.execute(
            "INSERT INTO vehicle_incidents(bus_id,incident_type,occurred_at,place,route_id,waybill_id,driver_id,circumstances,participants,other_vehicle,fault_status,police_document_number,insurer,insurance_case_number,responsible_user_id,status,estimated_damage_cost,comment,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (bus_id, payload["incident_type"], payload["occurred_at"], payload.get("place"), payload.get("route_id"), payload.get("waybill_id"), payload.get("driver_id"), payload["circumstances"].strip(), payload.get("participants"), payload.get("other_vehicle"), payload.get("fault_status") or "не установлена", payload.get("police_document_number"), payload.get("insurer"), payload.get("insurance_case_number"), payload.get("responsible_user_id"), "зарегистрировано", float(payload.get("estimated_damage_cost") or 0), payload.get("comment"), user["id"]),
        ).lastrowid
        for damage in payload.get("damages") or []:
            if not str(damage.get("area") or "").strip() or not str(damage.get("description") or "").strip():
                raise HTTPException(400, "Укажите зону и описание повреждения")
            con.execute(
                "INSERT INTO vehicle_damages(incident_id,area,description,severity,repair_required,comment) VALUES(?,?,?,?,?,?)",
                (incident_id, damage["area"].strip(), damage["description"].strip(), damage.get("severity") or "средняя", 1 if damage.get("repair_required", True) else 0, damage.get("comment")),
            )
        if payload.get("create_repair_request"):
            number = next_document_number(con, "request", "ЗР")
            request_id = con.execute(
                "INSERT INTO repair_requests(number,created_by,bus_id,source,incident_id,driver_id,status,priority,odometer,description,location) SELECT ?,?,?,?, ?,?,'новая','высокая',odometer,?,? FROM buses WHERE id=?",
                (number, user["id"], bus_id, "ДТП", incident_id, payload.get("driver_id"), f"{payload['incident_type']}: {payload['circumstances'].strip()}", payload.get("place"), bus_id),
            ).lastrowid
            con.execute("UPDATE vehicle_incidents SET repair_request_id=? WHERE id=?", (request_id, incident_id))
        item = incident_details(con, incident_id)
        audit_change(con, user, "регистрация события автобуса", "vehicle_incident", incident_id, new=item)
        con.commit()
        return item
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
```

Also implement `PATCH /incidents/{incident_id}`, `POST /incidents/{incident_id}/damages`, `PATCH /damages/{damage_id}`, and `POST /incidents/{incident_id}/cancel`. Every link to a request/order must verify the same `bus_id`; linking an order updates `actual_damage_cost` from `repair_orders.total_cost`; cancellation requires a non-empty reason and never deletes rows.

- [ ] **Step 5: Register the router and run focused tests**

```python
# app/main.py
from .api_vehicle_incidents import router as vehicle_incidents_router
# after the repair routers:
app.include_router(vehicle_incidents_router)
```

Run: `python -m pytest tests/test_vehicle_incidents_api.py tests/test_repair_requests_api.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the incident slice**

```powershell
git add app/repair_service.py app/api_vehicle_incidents.py app/main.py tests/test_vehicle_incidents_api.py
git commit -m "feat: add vehicle incident and damage lifecycle"
```

### Task 3: Full summary and lazy-loaded card tabs

**Files:**
- Create: `app/api_vehicle_card.py`
- Modify: `app/api_repair_dashboard.py`
- Modify: `app/main.py`
- Create: `tests/test_vehicle_card_api.py`

- [ ] **Step 1: Write failing card-detail tests**

```python
# tests/test_vehicle_card_api.py
from tests.test_repair_operations_api import create_order
from tests.test_repair_requests_api import make_client


def test_vehicle_card_exposes_summary_and_detailed_tabs(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    import app.db as db
    con = db.connect()
    try:
        master_id = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        part_id = con.execute("INSERT INTO parts(code,name,stock_qty,unit_price) VALUES('P-1','Фара',10,2500)").lastrowid
        con.execute("UPDATE repair_orders SET responsible_master_id=?,labor_cost=1000,parts_cost=2500,external_cost=300,other_cost=200,total_cost=4000 WHERE id=?", (master_id, order["id"]))
        con.execute("INSERT INTO repair_operations(order_id,name,status,actual_hours,price,result) VALUES(?,?,?,?,?,?)", (order["id"], "Замена фары", "завершена", 2, 1000, "Фара заменена"))
        con.execute("INSERT INTO repair_order_workers(order_id,worker_id,role,status,actual_hours,hourly_rate) VALUES(?,?,?,?,?,?)", (order["id"], master_id, "мастер", "завершен", 1, 1000))
        con.execute("INSERT INTO repair_parts(order_id,part_id,requested_qty,issued_qty,installed_qty,unit_price,status) VALUES(?,?,?,?,?,?,?)", (order["id"], part_id, 1, 1, 1, 2500, "установлено"))
        con.commit()
    finally:
        con.close()

    card = client.get(f"/api/repairs/vehicles/{bus_id}/card").json()
    assert card["vehicle"]["garage_number"] == "Р-101"
    assert card["totals"]["cost"] == 4000
    assert card["totals"]["open_damages"] == 0
    assert "cover" in card

    repairs = client.get(f"/api/repairs/vehicles/{bus_id}/card/repairs").json()["items"]
    assert repairs[0]["responsible_master_name"] == "Администратор системы"
    costs = client.get(f"/api/repairs/vehicles/{bus_id}/card/costs").json()
    assert costs["totals"] == {"labor": 1000, "parts": 2500, "external": 300, "other": 200, "total": 4000}
    assert client.get(f"/api/repairs/vehicles/{bus_id}/card/parts").json()["items"][0]["name"] == "Фара"
    assert client.get(f"/api/repairs/vehicles/{bus_id}/card/workers").json()["items"][0]["role"] == "мастер"
    assert client.get(f"/api/repairs/vehicles/{bus_id}/card/timeline").status_code == 200
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m pytest tests/test_vehicle_card_api.py -q`

Expected: FAIL because the detail routes and new total fields do not exist.

- [ ] **Step 3: Implement `app/api_vehicle_card.py`**

Expose these routes under `APIRouter(prefix="/api/repairs/vehicles/{bus_id}/card")`:

```python
@router.get("")
def card_summary(bus_id: int, user=Depends(current_user)):
    con = db.connect()
    try:
        vehicle = require_vehicle(con, bus_id)
        totals = db.one(con.execute(
            "SELECT COUNT(*) repairs,COALESCE(SUM(total_cost),0) cost,COALESCE(SUM(downtime_hours),0) downtime_hours,COALESCE(SUM(labor_cost),0) labor_cost,COALESCE(SUM(parts_cost),0) parts_cost,COALESCE(SUM(external_cost),0) external_cost,COALESCE(SUM(other_cost),0) other_cost FROM repair_orders WHERE bus_id=? AND status='завершен'",
            (bus_id,),
        ))
        totals["incidents"] = con.execute("SELECT COUNT(*) FROM vehicle_incidents WHERE bus_id=? AND cancelled_at IS NULL", (bus_id,)).fetchone()[0]
        totals["open_damages"] = con.execute("SELECT COUNT(*) FROM vehicle_damages vd JOIN vehicle_incidents vi ON vi.id=vd.incident_id WHERE vi.bus_id=? AND vi.cancelled_at IS NULL AND vd.resolved=0", (bus_id,)).fetchone()[0]
        cover = db.one(con.execute("SELECT id,original_name,mime_type,caption FROM repair_attachments WHERE bus_id=? AND is_cover=1 AND cancelled_at IS NULL ORDER BY id DESC LIMIT 1", (bus_id,)))
        return {
            "vehicle": vehicle,
            "totals": totals,
            "cover": cover,
            "active_order": active_order(con, bus_id),
            "next_maintenance": next_maintenance(con, bus_id),
            "open_damages": open_damages(con, bus_id),
        }
    finally:
        con.close()
```

Implement the tab routes with stable `ORDER BY` clauses:

- `GET /repairs`: orders joined to requests, repair types, master, aggregated worker/operation counts.
- `GET /operations`: operations joined to order and assigned worker.
- `GET /parts`: repair parts joined to part and order, returning quantity and line cost.
- `GET /workers`: assignments joined to user and order, returning role, hours and labor cost.
- `GET /maintenance`: plans and events ordered by due date.
- `GET /costs`: totals plus monthly rows grouped by `substr(COALESCE(closed_at,created_at),1,7)`.
- `GET /timeline`: normalized repair, maintenance, incident, and photo rows sorted by `event_at DESC,event_type,id DESC`.

Use a shared `require_vehicle(con, bus_id)` that raises 404 and never interpolates user input into SQL.

- [ ] **Step 4: Replace the old endpoint and register the router**

Remove only `vehicle_card()` from `app/api_repair_dashboard.py`. Register `vehicle_card_router` in `app/main.py` after the dashboard router so there is exactly one handler for `/api/repairs/vehicles/{bus_id}/card`.

- [ ] **Step 5: Run focused and regression tests**

Run: `python -m pytest tests/test_vehicle_card_api.py tests/test_repair_dashboard.py tests/test_repair_operations_api.py tests/test_repair_stock_api.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the card API slice**

```powershell
git add app/api_vehicle_card.py app/api_repair_dashboard.py app/main.py tests/test_vehicle_card_api.py tests/test_repair_dashboard.py
git commit -m "feat: add detailed vehicle card APIs"
```

### Task 4: Vehicle gallery, event photos, cover, and cancellation

**Files:**
- Create: `app/api_vehicle_media.py`
- Modify: `app/main.py`
- Create: `tests/test_vehicle_media.py`

- [ ] **Step 1: Write failing media tests**

```python
# tests/test_vehicle_media.py
from tests.test_repair_requests_api import make_client


def test_vehicle_gallery_upload_cover_and_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv("ATP_REPAIR_UPLOADS", str(tmp_path / "uploads"))
    client, bus_id = make_client(tmp_path)
    uploaded = client.post(
        f"/api/repairs/vehicles/{bus_id}/media",
        files={"file": ("автобус.jpg", b"\xff\xd8\xff test", "image/jpeg")},
        data={"category": "общий вид", "caption": "Вид спереди", "captured_at": "2026-07-13"},
    )
    assert uploaded.status_code == 201, uploaded.text
    media_id = uploaded.json()["id"]
    assert client.post(f"/api/repairs/media/{media_id}/cover").status_code == 200
    gallery = client.get(f"/api/repairs/vehicles/{bus_id}/media").json()["items"]
    assert gallery[0]["is_cover"] == 1
    assert gallery[0]["caption"] == "Вид спереди"
    assert client.post(f"/api/repairs/media/{media_id}/cancel", json={"reason": "Неверный ракурс"}).status_code == 200
    assert client.get(f"/api/repairs/vehicles/{bus_id}/media").json()["items"] == []


def test_incident_photo_must_belong_to_same_bus(tmp_path, monkeypatch):
    monkeypatch.setenv("ATP_REPAIR_UPLOADS", str(tmp_path / "uploads"))
    client, bus_id = make_client(tmp_path)
    response = client.post(
        f"/api/repairs/vehicles/{bus_id}/media",
        files={"file": ("damage.png", b"\x89PNG test", "image/png")},
        data={"category": "повреждение", "incident_id": "999999"},
    )
    assert response.status_code == 409
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_vehicle_media.py -q`

Expected: FAIL with 404 because media routes are absent.

- [ ] **Step 3: Implement `app/api_vehicle_media.py`**

Reuse `ALLOWED`, `MAX_BYTES`, and `upload_root` from `app/api_repair_attachments.py`. Implement:

- `GET /api/repairs/vehicles/{bus_id}/media?include_cancelled=false`.
- `POST /api/repairs/vehicles/{bus_id}/media` with `file`, `category`, `caption`, `captured_at`, optional `request_id`, `order_id`, `incident_id`, and `damage_id` form fields.
- `PATCH /api/repairs/media/{media_id}` for category, caption, and captured date.
- `POST /api/repairs/media/{media_id}/cover`, which clears the prior cover for the same bus in the same transaction.
- `POST /api/repairs/media/{media_id}/cancel`, requiring a reason.

The upload route must validate every supplied related object belongs to `bus_id`, write the file in 1 MiB chunks, enforce 10 MiB, roll back the database and unlink a partially written file on failure, and call `audit_change`. Keep physical download through the existing authenticated `/api/repairs/attachments/{id}/download` route; make it return 404 for cancelled rows.

- [ ] **Step 4: Register the router and run media regressions**

Run: `python -m pytest tests/test_vehicle_media.py tests/test_repair_attachments.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the gallery slice**

```powershell
git add app/api_vehicle_media.py app/api_repair_attachments.py app/main.py tests/test_vehicle_media.py tests/test_repair_attachments.py
git commit -m "feat: add vehicle photo gallery"
```

### Task 5: Immutable closed-repair cost and master snapshot

**Files:**
- Modify: `app/api_repair_control.py`
- Modify: `tests/test_repair_inspection_and_history.py`

- [ ] **Step 1: Extend the existing history test before production code**

Add assertions after closing an order:

```python
history = client.get(f"/api/vehicles/{bus_id}/repair-history").json()["items"][0]
assert history["master_name"] == "Администратор системы"
assert history["labor_cost"] == 1000
assert history["parts_cost"] == 2500
assert history["external_cost"] == 300
assert history["other_cost"] == 200
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_repair_inspection_and_history.py -q`

Expected: FAIL because snapshot cost breakdown and master are empty.

- [ ] **Step 3: Extend the close-order snapshot insert**

Before inserting history, query the master name and insert `labor_cost`, `parts_cost`, `external_cost`, `other_cost`, and `master_name` with the existing operation/worker/part JSON. Preserve `INSERT ... ON CONFLICT(order_id) DO NOTHING` semantics so a retry cannot rewrite history.

- [ ] **Step 4: Run closure and acceptance tests**

Run: `python -m pytest tests/test_repair_inspection_and_history.py tests/test_repair_acceptance.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the snapshot slice**

```powershell
git add app/api_repair_control.py tests/test_repair_inspection_and_history.py
git commit -m "feat: preserve full vehicle repair cost snapshot"
```

### Task 6: Printable dossier and vehicle Excel workbook

**Files:**
- Create: `app/vehicle_card_reports.py`
- Modify: `app/main.py`
- Create: `tests/test_vehicle_card_reports.py`

- [ ] **Step 1: Write failing print and workbook tests**

```python
# tests/test_vehicle_card_reports.py
import io
from openpyxl import load_workbook
from tests.test_repair_operations_api import create_order
from tests.test_repair_requests_api import make_client


def test_vehicle_dossier_print_contains_required_sections(tmp_path):
    client, bus_id = make_client(tmp_path)
    create_order(client, bus_id)
    response = client.get(f"/api/repairs/vehicles/{bus_id}/print?date_from=2026-01-01&date_to=2026-12-31")
    assert response.status_code == 200
    for text in ("ТЕХНИЧЕСКОЕ ДОСЬЕ АВТОБУСА", "Ремонты", "Запчасти", "Исполнители", "ДТП и повреждения", "Ответственный мастер"):
        assert text in response.text
    assert "@media print" in response.text


def test_vehicle_dossier_excel_has_all_sheets_and_money_formats(tmp_path):
    client, bus_id = make_client(tmp_path)
    create_order(client, bus_id)
    response = client.get(f"/api/repairs/vehicles/{bus_id}/export.xlsx")
    assert response.status_code == 200
    wb = load_workbook(io.BytesIO(response.content), data_only=True)
    assert wb.sheetnames == ["Паспорт", "Сводка", "Ремонты", "Операции", "Запчасти", "Исполнители", "ДТП", "Повреждения", "ТО", "Затраты", "Фотографии"]
    assert wb["Паспорт"]["A1"].value == "ТЕХНИЧЕСКОЕ ДОСЬЕ АВТОБУСА"
    assert wb["Ремонты"].freeze_panes == "A2"
    assert wb["Ремонты"].auto_filter.ref
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_vehicle_card_reports.py -q`

Expected: FAIL with 404 for both report routes.

- [ ] **Step 3: Implement report data collection once**

In `app/vehicle_card_reports.py`, create `collect_vehicle_dossier(con, bus_id, date_from, date_to)` returning `vehicle`, `summary`, `repairs`, `operations`, `parts`, `workers`, `incidents`, `damages`, `maintenance`, `costs`, and `media`. Validate ISO dates and reject `date_from > date_to` with 400. Every query must filter by the selected `bus_id`; date filters apply to event/order dates without excluding passport data.

- [ ] **Step 4: Implement print and Excel endpoints**

```python
router = APIRouter(prefix="/api/repairs/vehicles", tags=["vehicle-card-reports"])

@router.get("/{bus_id}/print", response_class=HTMLResponse)
def print_vehicle_card(bus_id: int, date_from: str = "", date_to: str = "", user=Depends(current_user)):
    require_repair_action(user, "read_reports")
    con = db.connect()
    try:
        data = collect_vehicle_dossier(con, bus_id, date_from, date_to)
        return HTMLResponse(render_vehicle_dossier(data))
    finally:
        con.close()

@router.get("/{bus_id}/export.xlsx")
def export_vehicle_card(bus_id: int, date_from: str = "", date_to: str = "", user=Depends(current_user)):
    require_repair_action(user, "read_reports")
    con = db.connect()
    try:
        data = collect_vehicle_dossier(con, bus_id, date_from, date_to)
    finally:
        con.close()
    stream = build_vehicle_workbook(data)
    filename = f"vehicle_{data['vehicle']['garage_number']}_dossier.xlsx"
    return StreamingResponse(stream, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
```

Escape all HTML values with `html.escape`. Use `add_table`-equivalent styling locally: dark-blue bold header, borders, wrapping, freeze `A2`, auto-filter, widths 10–42, `#,##0.00` for money/hours, and `dd.mm.yyyy` for dates. The print HTML includes cover image through the authenticated download URL, page-break avoidance for event blocks, and signature lines.

- [ ] **Step 5: Run report tests and existing report regressions**

Run: `python -m pytest tests/test_vehicle_card_reports.py tests/test_repair_reports.py tests/test_repair_print.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the reporting slice**

```powershell
git add app/vehicle_card_reports.py app/main.py tests/test_vehicle_card_reports.py
git commit -m "feat: export printable vehicle dossier"
```

### Task 7: Full-page SPA card, tabs, incident form, and gallery

**Files:**
- Modify: `static/app.js`
- Modify: `static/styles.css`
- Modify: `static/index.html`
- Create: `tests/test_vehicle_card_ui.py`

- [ ] **Step 1: Write failing static UI tests**

```python
# tests/test_vehicle_card_ui.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_vehicle_card_has_route_tabs_search_incidents_gallery_and_exports():
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    for marker in (
        'VIEWS.vehicleCard', '"Обзор"', '"Ремонты"', '"Запчасти"', '"Исполнители"',
        '"ДТП и повреждения"', '"ТО"', '"Фотографии и документы"', '"Затраты"', '"История"',
        "vehicleCardIncident", "vehicleCardUpload", "/media", "/incidents",
        "/export.xlsx", "/print", "гаражному номеру, госномеру или VIN",
    ):
        assert marker in js
    css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert ".vehicle-card" in css
    assert ".vehicle-gallery" in css
    assert ".vehicle-timeline" in css


def test_index_uses_vehicle_card_cache_version():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert '/static/app.js?v=3.2' in html
    assert '/static/styles.css?v=3.2' in html
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_vehicle_card_ui.py -q`

Expected: FAIL because the full-page route and styles are absent.

- [ ] **Step 3: Make hash routing support a bus id**

Change `route()` to parse `#/vehicleCard/17` without breaking existing routes:

```javascript
function route() {
  const parts = (location.hash || "#/dashboard").slice(2).split("/");
  const view = parts[0] || "dashboard";
  const args = parts.slice(1).map(decodeURIComponent);
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.dataset.view === view));
  $("page-title").textContent = TITLES[view] || (view === "vehicleCard" ? "Карточка автобуса" : view);
  const fn = VIEWS[view] || VIEWS.dashboard;
  $("content").innerHTML = "<div class='muted'>Загрузка…</div>";
  fn(...args).catch(e => { $("content").innerHTML = `<div class="vio"><b>Ошибка</b>${esc(e.message)}</div>`; });
}

function openVehicleCard(busId) {
  location.hash = `#/vehicleCard/${busId}`;
}
```

- [ ] **Step 4: Implement the full-page card state and lazy tabs**

```javascript
const VEHICLE_CARD_TABS = [
  ["overview", "Обзор"], ["repairs", "Ремонты"], ["parts", "Запчасти"],
  ["workers", "Исполнители"], ["incidents", "ДТП и повреждения"],
  ["maintenance", "ТО"], ["media", "Фотографии и документы"],
  ["costs", "Затраты"], ["timeline", "История"],
];

function vehicleCardState(busId) {
  if (!window._vehicleCard || window._vehicleCard.busId !== +busId) {
    window._vehicleCard = {busId: +busId, tab: "overview", summary: null, tabs: {}};
  }
  return window._vehicleCard;
}

VIEWS.vehicleCard = async function (busId) {
  const state = vehicleCardState(busId);
  state.summary = await api(`/api/repairs/vehicles/${state.busId}/card`);
  await renderVehicleCard(state);
};

async function vehicleCardTab(name) {
  const state = window._vehicleCard;
  state.tab = name;
  if (name !== "overview" && !state.tabs[name]) {
    const path = name === "incidents" ? `/api/repairs/vehicles/${state.busId}/incidents` :
      name === "media" ? `/api/repairs/vehicles/${state.busId}/media` :
      `/api/repairs/vehicles/${state.busId}/card/${name}`;
    state.tabs[name] = await api(path);
  }
  await renderVehicleCard(state);
}
```

`renderVehicleCard` must render the cover, vehicle identity, active repair/next TO, five KPI cards, unresolved-damage warning, action buttons, accessible text tabs, and the selected tab. Render repair rows with expandable operations/parts/workers, incident rows with their damage list, cost breakdown and monthly rows, gallery thumbnails with captions and cover control, and a chronological timeline. Every interpolated server value must pass through `esc`.

- [ ] **Step 5: Add incident and photo forms**

Implement `vehicleCardIncident()` using `formModal` for event fields, then a repeatable in-modal damage-row list with `area`, `description`, `severity`, and `repair_required`. Submit JSON to `/api/repairs/vehicles/{id}/incidents`; keep the form open and show the server error on failure.

Implement `vehicleCardUpload()` with a real `FormData`, `accept="image/jpeg,image/png,application/pdf,.docx,.xlsx"`, category, caption, captured date, and optional incident/order link. After success, clear only the media tab cache and re-render it.

- [ ] **Step 6: Replace the old modal card and add entry points**

Replace `repairVehicleCard(busId)` body with `openVehicleCard(busId)`. In the buses view, create a dedicated `busView()` wrapper instead of `VIEWS.buses = refView("buses")`; preserve edit/import/export controls, add the search placeholder `Поиск по гаражному номеру, госномеру или VIN`, and add a `Карточка` button per row. Add a vehicle-card search/select control to the repair toolbar.

- [ ] **Step 7: Add focused CSS and bump cache**

Add `.vehicle-card`, `.vehicle-card-head`, `.vehicle-cover`, `.vehicle-tabs`, `.vehicle-tab`, `.vehicle-gallery`, `.vehicle-photo`, `.vehicle-timeline`, `.vehicle-timeline-item`, `.incident-card`, `.damage-list`, responsive rules below 900px, and `@media print` hiding navigation/actions. Bump both static URLs in `static/index.html` from `3.1` to `3.2`.

- [ ] **Step 8: Run UI checks**

Run: `python -m pytest tests/test_vehicle_card_ui.py tests/test_repair_ui.py -q`

Run: `node --check static/app.js`

Expected: both commands exit 0.

- [ ] **Step 9: Commit the UI slice**

```powershell
git add static/app.js static/styles.css static/index.html tests/test_vehicle_card_ui.py tests/test_repair_ui.py
git commit -m "feat: add full vehicle dossier interface"
```

### Task 8: Authorization, audit, validation, and full acceptance path

**Files:**
- Modify: `tests/test_vehicle_incidents_api.py`
- Modify: `tests/test_vehicle_media.py`
- Create: `tests/test_vehicle_card_acceptance.py`

- [ ] **Step 1: Add failing permission and linkage tests**

Add tests that create a non-authorized user and assert 403 for incident mutation, that an order from a second bus cannot be linked to the first bus incident (409), that report access works for `бухгалтер`, and that audit rows exist for incident creation/cancellation, photo upload/cover/cancellation, and export.

Use real database rows and authenticated TestClient sessions; do not mock `current_user` or database calls.

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_vehicle_incidents_api.py tests/test_vehicle_media.py -q`

Expected: at least one new permission, linkage, or audit assertion fails.

- [ ] **Step 3: Make minimal authorization and audit corrections**

Apply `require_repair_action` before every mutating route, validate related bus ids before writes, and call `audit_change` in the same transaction. For exports, record the audit row before building the response and commit it. Do not loosen existing role permissions beyond the approved matrix.

- [ ] **Step 4: Write the end-to-end acceptance test**

```python
# tests/test_vehicle_card_acceptance.py
import io
from openpyxl import load_workbook
from tests.test_repair_requests_api import make_client


def test_vehicle_dossier_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("ATP_REPAIR_UPLOADS", str(tmp_path / "uploads"))
    client, bus_id = make_client(tmp_path)
    incident = client.post(f"/api/repairs/vehicles/{bus_id}/incidents", json={
        "incident_type": "ДТП", "occurred_at": "2026-07-13T08:30:00",
        "place": "ул. Центральная, 10", "circumstances": "Боковое столкновение",
        "create_repair_request": True,
        "damages": [
            {"area": "правый борт", "description": "Вмятина", "severity": "средняя"},
            {"area": "передняя дверь", "description": "Разбито стекло", "severity": "тяжёлая"},
        ],
    })
    assert incident.status_code == 201, incident.text
    incident = incident.json()
    refs = client.get("/api/repairs/references").json()
    order = client.post("/api/repairs/orders", json={
        "request_id": incident["repair_request_id"], "vehicle_id": bus_id,
        "repair_type_id": refs["repair_types"][0]["id"],
        "repair_post_id": refs["repair_posts"][0]["id"],
        "diagnosis": "Повреждение борта и двери",
    })
    assert order.status_code == 201, order.text
    order_id = order.json()["id"]
    linked = client.patch(f"/api/repairs/incidents/{incident['id']}", json={"repair_order_id": order_id})
    assert linked.status_code == 200, linked.text

    me = client.get("/api/me").json()
    worker = client.post(f"/api/repairs/orders/{order_id}/workers", json={
        "worker_id": me["id"], "role": "мастер", "planned_hours": 2, "hourly_rate": 1000,
    })
    assert worker.status_code == 201, worker.text
    worker_id = worker.json()["id"]
    assert client.post(f"/api/repairs/workers/{worker_id}/start").status_code == 200
    assert client.post(f"/api/repairs/workers/{worker_id}/finish", json={"actual_hours": 2}).status_code == 200
    operation = client.post(f"/api/repairs/orders/{order_id}/operations", json={
        "name": "Восстановление правого борта", "norm_hours": 2,
    })
    assert operation.status_code == 201, operation.text
    operation_id = operation.json()["id"]
    assert client.post(f"/api/repairs/operations/{operation_id}/start").status_code == 200
    assert client.post(f"/api/repairs/operations/{operation_id}/complete", json={
        "actual_hours": 2, "result": "Борт восстановлен",
    }).status_code == 200

    photo = client.post(
        f"/api/repairs/vehicles/{bus_id}/media",
        files={"file": ("damage.jpg", b"\\xff\\xd8\\xff test", "image/jpeg")},
        data={"category": "повреждение", "caption": "До ремонта", "incident_id": str(incident["id"])},
    )
    assert photo.status_code == 201, photo.text
    assert client.post(f"/api/repairs/media/{photo.json()['id']}/cover").status_code == 200

    for status in ("диагностика", "готов к работе", "в работе", "контроль"):
        changed = client.post(f"/api/repairs/orders/{order_id}/status", json={"status": status})
        assert changed.status_code == 200, changed.text
    inspected = client.post(f"/api/repairs/orders/{order_id}/inspection", json={
        "result": "годен", "release_allowed": True,
    })
    assert inspected.status_code == 201, inspected.text
    closed = client.post(f"/api/repairs/orders/{order_id}/close", json={"result": "Повреждения устранены"})
    assert closed.status_code == 200, closed.text

    card = client.get(f"/api/repairs/vehicles/{bus_id}/card").json()
    assert card["vehicle"]["garage_number"] == "Р-101"
    assert card["cover"]["id"] == photo.json()["id"]
    assert client.get(f"/api/repairs/vehicles/{bus_id}/card/workers").json()["items"][0]["role"] == "мастер"
    assert client.get(f"/api/repairs/vehicles/{bus_id}/card/operations").json()["items"][0]["result"] == "Борт восстановлен"
    incidents = client.get(f"/api/repairs/vehicles/{bus_id}/incidents").json()["items"]
    assert [row["area"] for row in incidents[0]["damages"]] == ["правый борт", "передняя дверь"]
    timeline = client.get(f"/api/repairs/vehicles/{bus_id}/card/timeline").json()["items"]
    assert {row["event_type"] for row in timeline} >= {"ремонт", "ДТП", "фотография"}

    printed = client.get(f"/api/repairs/vehicles/{bus_id}/print")
    assert printed.status_code == 200, printed.text
    assert "Р-101" in printed.text and "Вмятина" in printed.text and "Разбито стекло" in printed.text
    exported = client.get(f"/api/repairs/vehicles/{bus_id}/export.xlsx")
    assert exported.status_code == 200, exported.text
    wb = load_workbook(io.BytesIO(exported.content), data_only=True)
    assert wb["Паспорт"]["B2"].value == "Р-101"
    assert wb["Ремонты"].max_row >= 2
    assert wb["Исполнители"].max_row >= 2
    assert wb["ДТП"].max_row >= 2
    assert wb["Повреждения"].max_row == 3
```

- [ ] **Step 5: Run the acceptance and repair suites**

Run: `python -m pytest tests/test_vehicle_card_acceptance.py tests/test_repair_acceptance.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the hardening slice**

```powershell
git add app/repair_service.py app/api_vehicle_incidents.py app/api_vehicle_media.py app/vehicle_card_reports.py tests/test_vehicle_incidents_api.py tests/test_vehicle_media.py tests/test_vehicle_card_acceptance.py
git commit -m "test: cover vehicle dossier acceptance and permissions"
```

### Task 9: Documentation, migration rehearsal, and final verification

**Files:**
- Modify: `docs/Ремонт_и_ТО_инструкция.md`
- Modify: `README.md`

- [ ] **Step 1: Document the operator workflow**

Add exact steps for opening a card from both sections, registering an incident and damages, creating/linking a repair request, uploading and categorizing photos, choosing a cover, resolving damages, filtering the report period, printing/saving PDF, and exporting Excel. Document permissions and the fact that cancellation preserves history.

- [ ] **Step 2: Document media storage and backup**

Document `ATP_REPAIR_UPLOADS`, the default `repair_uploads` path, 10 MiB limit, allowed MIME types, and that a complete backup contains both SQLite database and upload directory.

- [ ] **Step 3: Rehearse migration on a database copy**

Copy the configured SQLite database to a temporary path without modifying the original. Point `app.db.DB_PATH` at the copy, run `db.init_db()` twice, and query `PRAGMA foreign_key_check`; expected result is an empty list. Confirm original buses, repair orders, and attachments counts are unchanged on the migrated copy.

- [ ] **Step 4: Run complete automated verification**

Run: `python -m pytest -q`

Expected: all tests pass with zero failures.

Run: `node --check static/app.js`

Expected: exit 0.

Run: `git diff --check`

Expected: exit 0; line-ending notices are acceptable, whitespace errors are not.

- [ ] **Step 5: Restart and smoke-test the live application**

Restart `python run.py --port 8001`. Authenticate as admin and verify HTTP 200 for:

```text
/api/repairs/vehicles/{bus_id}/card
/api/repairs/vehicles/{bus_id}/card/repairs
/api/repairs/vehicles/{bus_id}/card/parts
/api/repairs/vehicles/{bus_id}/card/workers
/api/repairs/vehicles/{bus_id}/incidents
/api/repairs/vehicles/{bus_id}/media
/api/repairs/vehicles/{bus_id}/print
/api/repairs/vehicles/{bus_id}/export.xlsx
```

Open `http://127.0.0.1:8001/?v=32#/vehicleCard/{bus_id}` and verify tabs, incident form, gallery, cover, print, and Excel manually.

- [ ] **Step 6: Commit documentation**

```powershell
git add README.md docs/Ремонт_и_ТО_инструкция.md
git commit -m "docs: explain vehicle dossier workflow"
```
