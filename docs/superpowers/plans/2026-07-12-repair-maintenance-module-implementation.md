# Repair and Maintenance Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать в существующей АТП-системе рабочий модуль ремонта и ТО от регистрации заявки до контрольного допуска, истории, склада, аналитики и отчётов.

**Architecture:** Модульный монолит сохраняет FastAPI, SQLite и текущий SPA, но выносит схему, бизнес-правила, API и отчёты ремонта в отдельные файлы. Существующие автобусы, пользователи, наряды, путевые листы, аудит и уведомления используются напрямую; все многотабличные изменения выполняются транзакционно.

**Tech Stack:** Python 3, FastAPI, SQLite, openpyxl, pytest, vanilla JavaScript, HTML/CSS.

---

## Карта файлов

- Create `app/repair_schema.py`: таблицы, индексы, миграции, справочники.
- Create `app/repair_service.py`: нумерация, права, статусы, расчёты и проверки.
- Create `app/api_repairs.py`: CRUD и команды бизнес-процесса.
- Create `app/repair_reports.py`: Excel и печатные HTML-формы.
- Create `static/repairs.js`: состояния, страницы, формы, канбан и карточка автобуса.
- Create `static/repairs.css`: адаптивный интерфейс и статусы.
- Modify `app/db.py`: запуск миграций ремонта и технические поля автобуса.
- Modify `app/auth.py`: роли и секции прав ремонта.
- Modify `app/main.py`: подключение роутера и каталога вложений.
- Modify `app/api_planning.py`: исключение ремонтируемых автобусов из кандидатов и назначений.
- Modify `app/api_waybills.py`: запрет техдопуска и путевого листа при активном ремонте.
- Modify `static/index.html`: подключение ресурсов ремонта.
- Modify `static/app.js`: меню и маршрутизация в модуль.
- Modify `static/styles.css`: только общие совместимые токены, если они нужны.
- Modify `README.md`: установка, роли, резервная копия и тесты.
- Create `docs/Ремонт_и_ТО_инструкция.md`: пользовательская инструкция.
- Create `tests/repair_helpers.py`: изолированная БД, пользователи и исходные данные.
- Create focused test files listed in tasks below.

### Task 1: Базовая схема и безопасная миграция

**Files:**
- Create: `app/repair_schema.py`
- Modify: `app/db.py`
- Create: `tests/test_repair_schema.py`

- [ ] **Step 1: Write failing migration tests**

```python
def test_repair_migration_is_idempotent(tmp_path):
    import app.db as db
    db.DB_PATH = str(tmp_path / "repair.db")
    db.init_db()
    db.init_db()
    con = db.connect()
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"repair_requests", "repair_orders", "repair_order_workers",
            "repair_operations", "repair_parts", "repair_inspections",
            "vehicle_repair_history", "repair_attachments"} <= names
    columns = {r[1] for r in con.execute("PRAGMA table_info(buses)")}
    assert {"modification", "commissioned_at", "engine_number",
            "last_to_date", "next_to_date", "warranty_status"} <= columns
```

- [ ] **Step 2: Run the test and verify expected failure**

Run: `pytest tests/test_repair_schema.py -v`

Expected: FAIL because repair tables and new bus columns do not exist.

- [ ] **Step 3: Add schema entry point**

Implement in `app/repair_schema.py`:

```python
def migrate_repairs(con):
    con.executescript(REPAIR_SCHEMA)
    for table, column, definition in BUS_MIGRATIONS:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    seed_repair_refs(con)
```

Define every table from the approved specification with foreign keys, checks for non-negative quantities/costs, unique document numbers, and indexes for status, vehicle, worker and dates. Call `migrate_repairs(con)` from `db.init_db()` after the existing schema and migrations.

- [ ] **Step 4: Verify schema tests and all existing tests**

Run: `pytest tests/test_repair_schema.py -v`

Expected: PASS.

Run: `pytest -q`

Expected: all existing tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/repair_schema.py app/db.py tests/test_repair_schema.py
git commit -m "feat: add repair database schema"
```

### Task 2: Роли, статусы и сервисные правила

**Files:**
- Create: `app/repair_service.py`
- Modify: `app/auth.py`
- Create: `tests/test_repair_service.py`

- [ ] **Step 1: Write failing unit tests for permissions and transitions**

```python
def test_worker_cannot_close_order():
    with pytest.raises(HTTPException) as exc:
        require_repair_action({"role": "слесарь"}, "close_order")
    assert exc.value.status_code == 403

def test_transition_rejects_skipping_control():
    with pytest.raises(HTTPException) as exc:
        validate_transition("в работе", "завершен", release_allowed=False)
    assert exc.value.status_code == 409
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_repair_service.py -v`

Expected: FAIL because `repair_service` does not exist.

- [ ] **Step 3: Implement minimal service contract**

Add role names to `ROLES`, extend `WRITE_ACCESS`, and implement:

```python
REPAIR_ACTIONS = {
    "create_request": {"админ", "диспетчер", "механик", "мастер ремонта"},
    "manage_order": {"админ", "мастер ремонта"},
    "work_assignment": {"админ", "слесарь"},
    "inspect": {"админ", "механик контроля"},
    "stock": {"админ", "склад"},
    "read_reports": {"админ", "руководитель"},
}

def validate_transition(current, target, *, release_allowed=False):
    if target not in ALLOWED_TRANSITIONS[current]:
        raise HTTPException(409, "Недопустимый переход статуса")
    if target == "завершен" and not release_allowed:
        raise HTTPException(409, "Нет положительного контрольного осмотра")
```

Also implement `next_document_number`, `active_repair_for_vehicle`, `vehicle_release_block_reason`, `calculate_cost`, `calculate_downtime` and `audit_change`.

- [ ] **Step 4: Verify GREEN and regression suite**

Run: `pytest tests/test_repair_service.py -v && pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/repair_service.py app/auth.py tests/test_repair_service.py
git commit -m "feat: add repair workflow rules"
```

### Task 3: Заявки на ремонт API

**Files:**
- Create: `app/api_repairs.py`
- Modify: `app/main.py`
- Create: `tests/repair_helpers.py`
- Create: `tests/test_repair_requests_api.py`

- [ ] **Step 1: Write failing request lifecycle tests**

Test authenticated creation, required vehicle/odometer/fault, list filters, read, update, cancellation without deletion, sequential `ЗР-YYYY-NNNNNN`, audit entry and dispatcher permissions.

```python
response = client.post("/api/repairs/requests", json={
    "vehicle_id": bus_id, "odometer": 12000,
    "fault_description": "Падение давления масла",
    "request_source": "диспетчер", "criticality": "высокая"
})
assert response.status_code == 201
assert response.json()["request_number"].startswith("ЗР-2026-")
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_repair_requests_api.py -v`

Expected: 404 for missing routes.

- [ ] **Step 3: Implement request endpoints**

Create router with prefix `/api/repairs`, explicit allowed update fields, server timestamps, role checks, audit and transaction boundaries. Register it in `app/main.py`.

- [ ] **Step 4: Verify GREEN and regressions**

Run: `pytest tests/test_repair_requests_api.py -v && pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/api_repairs.py app/main.py tests/repair_helpers.py tests/test_repair_requests_api.py
git commit -m "feat: add repair request API"
```

### Task 4: Заказ-наряд, исполнители и операции

**Files:**
- Modify: `app/api_repairs.py`
- Modify: `app/repair_service.py`
- Create: `tests/test_repair_orders_api.py`
- Create: `tests/test_repair_assignments_api.py`

- [ ] **Step 1: Write failing vertical-flow tests**

Cover request-to-order conversion, unique `РМ-YYYY-NNNNNN`, responsible master, multiple workers, operation creation, start/pause/complete, actual-hours aggregation, overlapping worker/post rejection and invalid status transitions.

```python
created = client.post("/api/repairs/orders", json={
    "request_id": request_id, "vehicle_id": bus_id, "odometer": 12000,
    "repair_type_id": repair_type_id, "responsible_master_id": master_id,
    "planned_start": "2026-07-12T09:00:00",
    "planned_end": "2026-07-12T18:00:00"
})
assert created.status_code == 201
assert created.json()["order_number"] == "РМ-2026-000001"
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_repair_orders_api.py tests/test_repair_assignments_api.py -v`

Expected: missing endpoint failures.

- [ ] **Step 3: Implement endpoints and checks**

Implement list/read/update orders, `/assign-workers`, `/add-operation`, assignment timer commands and atomic recalculation. Use half-open interval overlap comparison: `existing_start < new_end AND new_start < existing_end`.

- [ ] **Step 4: Verify GREEN and regressions**

Run: `pytest tests/test_repair_orders_api.py tests/test_repair_assignments_api.py -v && pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/api_repairs.py app/repair_service.py tests/test_repair_orders_api.py tests/test_repair_assignments_api.py
git commit -m "feat: add repair orders and assignments"
```

### Task 5: Контроль, закрытие и неизменяемая история

**Files:**
- Modify: `app/api_repairs.py`
- Modify: `app/repair_service.py`
- Create: `tests/test_repair_inspection_and_history.py`

- [ ] **Step 1: Write failing close/rework tests**

Cover missing workers/operations/result/control, negative inspection returning to rework, positive inspection allowing close, bus status update, immutable history snapshot, downtime/cost values and audit records.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_repair_inspection_and_history.py -v`

Expected: missing inspection/close endpoint failures.

- [ ] **Step 3: Implement atomic completion**

Implement `/complete`, `/inspection`, `/close` and `/api/vehicles/{id}/repair-history`. The close transaction inserts a history snapshot, sets `closed_at`, recalculates totals, updates `buses.status`, writes notifications and audit, then commits once.

- [ ] **Step 4: Verify GREEN and regressions**

Run: `pytest tests/test_repair_inspection_and_history.py -v && pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/api_repairs.py app/repair_service.py tests/test_repair_inspection_and_history.py
git commit -m "feat: add repair inspection and history"
```

### Task 6: Блокировка выпуска и интеграция существующих модулей

**Files:**
- Modify: `app/api_planning.py`
- Modify: `app/api_waybills.py`
- Modify: `app/repair_service.py`
- Create: `tests/test_repair_release_integration.py`

- [ ] **Step 1: Write failing integration tests**

Create an active repair and assert: bus absent from order candidates; direct order-line assignment returns 409; tech check cannot return `выпуск разрешен`; waybill precheck and creation return the repair order number in the block reason. Close with positive inspection and assert those operations become available.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_repair_release_integration.py -v`

Expected: existing APIs incorrectly allow the bus.

- [ ] **Step 3: Add shared release guard**

Call `vehicle_release_block_reason(con, bus_id)` from candidate query filters, order line mutations, tech check creation and waybill precheck. Never duplicate the active-status list in those modules.

- [ ] **Step 4: Verify GREEN and all waybill/planning tests**

Run: `pytest tests/test_repair_release_integration.py tests/test_waybill_modes_api.py tests/test_schedule_api.py -v && pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/api_planning.py app/api_waybills.py app/repair_service.py tests/test_repair_release_integration.py
git commit -m "feat: block release for buses under repair"
```

### Task 7: Запчасти, склад и стоимость

**Files:**
- Modify: `app/api_repairs.py`
- Modify: `app/repair_service.py`
- Create: `tests/test_repair_stock_api.py`
- Create: `tests/test_repair_costs.py`

- [ ] **Step 1: Write failing stock tests**

Cover request/reserve/issue/install/return, warehouse role, insufficient stock rollback, immutable issue price, non-negative quantities, stock movement audit and total cost formula.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_repair_stock_api.py tests/test_repair_costs.py -v`

Expected: endpoints and calculations missing.

- [ ] **Step 3: Implement stock commands**

Use conditional updates such as `UPDATE parts SET stock_qty=stock_qty-? WHERE id=? AND stock_qty>=?`; require `rowcount == 1`. Insert `stock_movements`, update `repair_parts`, recalculate order totals and audit in the same transaction.

- [ ] **Step 4: Verify GREEN and regressions**

Run: `pytest tests/test_repair_stock_api.py tests/test_repair_costs.py -v && pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/api_repairs.py app/repair_service.py tests/test_repair_stock_api.py tests/test_repair_costs.py
git commit -m "feat: track repair parts and costs"
```

### Task 8: Плановое ТО, повторы, простой и уведомления

**Files:**
- Modify: `app/api_repairs.py`
- Modify: `app/repair_service.py`
- Create: `tests/test_repair_maintenance.py`
- Create: `tests/test_repair_notifications.py`

- [ ] **Step 1: Write failing scheduling tests**

Cover date/mileage due rules, no duplicate automatic request, threshold notification, repeated fault within configurable days, link to previous repair, detailed downtime stages and readiness coefficient.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_repair_maintenance.py tests/test_repair_notifications.py -v`

Expected: missing maintenance and recurrence behavior.

- [ ] **Step 3: Implement deterministic evaluation endpoint**

Implement `POST /api/repairs/maintenance/evaluate` so startup does not create hidden side effects. It evaluates every active plan, creates only missing notifications/requests using unique source keys, and returns counts. Expose plan CRUD, repeats list and downtime metrics.

- [ ] **Step 4: Verify GREEN and regressions**

Run: `pytest tests/test_repair_maintenance.py tests/test_repair_notifications.py -v && pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/api_repairs.py app/repair_service.py tests/test_repair_maintenance.py tests/test_repair_notifications.py
git commit -m "feat: add maintenance planning and alerts"
```

### Task 9: Вложения с безопасным хранением

**Files:**
- Modify: `app/api_repairs.py`
- Modify: `app/main.py`
- Create: `tests/test_repair_attachments.py`

- [ ] **Step 1: Write failing upload tests**

Use temporary `ATP_REPAIR_UPLOADS`; assert allowed PDF/JPEG/DOCX/XLSX, random server name, metadata linkage, size/type rejection, path traversal neutralization and authorization on download.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_repair_attachments.py -v`

Expected: upload endpoint missing.

- [ ] **Step 3: Implement streamed upload**

Validate extension, MIME and maximum bytes before commit; generate `secrets.token_hex(16) + suffix`; write only under resolved upload root; delete the file if DB transaction fails. Serve downloads through an authenticated endpoint, not a public unrestricted mount.

- [ ] **Step 4: Verify GREEN and regressions**

Run: `pytest tests/test_repair_attachments.py -v && pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/api_repairs.py app/main.py tests/test_repair_attachments.py
git commit -m "feat: add repair attachments"
```

### Task 10: Панель, карточка автобуса и отчёты backend

**Files:**
- Create: `app/repair_reports.py`
- Modify: `app/api_repairs.py`
- Create: `tests/test_repair_dashboard.py`
- Create: `tests/test_repair_reports.py`

- [ ] **Step 1: Write failing analytics/export tests**

Seed closed, active, overdue and repeated repairs. Assert dashboard KPIs, kanban buckets, vehicle card totals, filters, workbook sheet names, frozen headers, autofilter, red/orange conditional formatting and formulas/values for costs and downtime.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_repair_dashboard.py tests/test_repair_reports.py -v`

Expected: endpoints missing.

- [ ] **Step 3: Implement parameterized queries and workbook**

Add dashboard, kanban, calendar, vehicle technical card, reports dataset, Excel export and print endpoints. All filters use SQL parameters; report timestamps and organization name come from settings.

- [ ] **Step 4: Verify GREEN and regressions**

Run: `pytest tests/test_repair_dashboard.py tests/test_repair_reports.py -v && pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/repair_reports.py app/api_repairs.py tests/test_repair_dashboard.py tests/test_repair_reports.py
git commit -m "feat: add repair analytics and reports"
```

### Task 11: SPA интерфейс ремонта

**Files:**
- Create: `static/repairs.js`
- Create: `static/repairs.css`
- Modify: `static/index.html`
- Modify: `static/app.js`
- Create: `tests/test_repair_ui.py`

- [ ] **Step 1: Write failing UI contract tests**

Assert resource inclusion, menu title, required views/tabs, request/order forms, role-gated actions, kanban columns, status classes, API paths, escaped output and responsive CSS breakpoint.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_repair_ui.py -v`

Expected: missing repair resources and UI strings.

- [ ] **Step 3: Implement UI in focused module**

Expose `window.Repairs.register(VIEWS, helpers)` from `repairs.js`. Register dashboard, request list/form, order list/detail, kanban, maintenance, vehicle card, repeats, reports and settings. Reuse `api`, `formModal`, `tbl`, `esc`, `toast` and existing reference caches. Use text plus status color and server-returned validation messages.

- [ ] **Step 4: Verify UI tests and regression suite**

Run: `pytest tests/test_repair_ui.py tests/test_summary_schedule_ui.py tests/test_order_ui_dialogs.py -v && pytest -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add static/repairs.js static/repairs.css static/index.html static/app.js tests/test_repair_ui.py
git commit -m "feat: add repair maintenance interface"
```

### Task 12: Документация, миграционная проверка и приёмка

**Files:**
- Modify: `README.md`
- Create: `docs/Ремонт_и_ТО_инструкция.md`
- Create: `tests/test_repair_acceptance.py`

- [ ] **Step 1: Write failing end-to-end acceptance test**

Automate the complete API scenario: create request → diagnose/order → assign two workers → add/complete operations → issue/install part → master complete → negative inspection/rework → positive inspection → close → verify history, released bus, audit, notification and Excel export.

- [ ] **Step 2: Verify RED before filling any remaining gap**

Run: `pytest tests/test_repair_acceptance.py -v`

Expected: FAIL at the first uncovered acceptance behavior; fix only that behavior and repeat until PASS.

- [ ] **Step 3: Update documentation**

Document backup of `atp.db`, automatic migration, new roles, upload directory, endpoints, report export, test commands, rollback by restoring DB backup, and user workflows for every role.

- [ ] **Step 4: Verify a copied production-like database**

```powershell
Copy-Item -LiteralPath .\atp.db -Destination $env:TEMP\atp-repair-verification.db -Force
$env:ATP_DB="$env:TEMP\atp-repair-verification.db"
python -c "from app import db; db.init_db(); print('migration ok')"
pytest -q
```

Expected: `migration ok`; all tests PASS. Do not run migration verification against the only production database copy.

- [ ] **Step 5: Run application smoke test**

Run: `python run.py --port 8012`

Verify in browser: login, repair menu, create request, create order, role restrictions, release blocking, positive close, history, Excel and print views. Stop the server after verification.

- [ ] **Step 6: Inspect final diff**

Run: `git status --short && git diff --check && git diff --stat`

Expected: no whitespace errors; no unrelated user files staged or changed by this implementation.

- [ ] **Step 7: Commit documentation and acceptance test**

```powershell
git add README.md docs/Ремонт_и_ТО_инструкция.md tests/test_repair_acceptance.py
git commit -m "docs: document repair maintenance workflow"
```

## Completion gate

Before claiming completion, invoke `superpowers:verification-before-completion` and confirm fresh evidence for:

- full `pytest -q` pass;
- migration on a copied `atp.db`;
- complete acceptance scenario;
- browser smoke test;
- clean `git diff --check`;
- preservation of all pre-existing uncommitted summary-schedule changes.
