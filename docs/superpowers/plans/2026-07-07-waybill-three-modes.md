# Waybill Three Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three administrator-controlled waybill issue modes and mass printing of all waybills in an order.

**Architecture:** Keep the decision in backend settings so UI and API cannot diverge. Split waybill validation into blocking `problems` and non-blocking `warnings`; reuse the same check for single and bulk creation. Keep print generation in `app/api_waybills.py` and add a combined order print endpoint that renders the existing waybill pages for all waybills in a date order.

**Tech Stack:** FastAPI, SQLite, vanilla JavaScript frontend, pytest/FastAPI TestClient.

---

### File Structure

- Modify: `app/db.py` — add default `waybill_issue_mode=strict_med_tech` to `ORG_DEFAULTS`.
- Modify: `app/api_waybills.py` — add mode constants, split checks into `problems`/`warnings`, allow nullable med/tech ids by mode, return warnings, add combined order waybill print endpoint.
- Modify: `static/app.js` — add settings selector, current-mode release hint, warnings in toasts, and buttons for printing all PЛ and forming+printing all PЛ.
- Create: `tests/test_waybill_modes_api.py` — API tests for all three modes, bulk creation, and combined print.

### Task 1: Backend Validation Tests

**Files:**
- Create: `tests/test_waybill_modes_api.py`

- [ ] **Step 1: Write failing tests for strict, medical-only, advisory, and print behavior**

Create `tests/test_waybill_modes_api.py` with helpers that create a temporary DB, insert a valid driver, bus, route, approved order, and order line directly through `app.db`, then exercise the public API.

Key tests:

```python
def test_strict_mode_blocks_without_medical_and_tech(tmp_path):
    client, ctx = make_client_with_line(tmp_path)
    response = client.post(f"/api/waybills/from-line/{ctx['line_id']}")
    assert response.status_code == 409
    assert "Нет предрейсового медицинского осмотра" in response.text
    assert "Нет предрейсового технического контроля" in response.text


def test_medical_only_blocks_without_medical_but_warns_without_tech(tmp_path):
    client, ctx = make_client_with_line(tmp_path)
    set_mode(client, "medical_only")
    response = client.post(f"/api/waybills/from-line/{ctx['line_id']}")
    assert response.status_code == 409
    assert "Нет предрейсового медицинского осмотра" in response.text
    add_medical(client, ctx["driver_id"])
    created = client.post(f"/api/waybills/from-line/{ctx['line_id']}")
    assert created.status_code == 200, created.text
    assert created.json()["warnings"] == ["Нет предрейсового технического контроля"]


def test_medical_only_blocks_explicit_tech_ban(tmp_path):
    client, ctx = make_client_with_line(tmp_path)
    set_mode(client, "medical_only")
    add_medical(client, ctx["driver_id"])
    add_tech(client, ctx["bus_id"], result="выпуск запрещен")
    response = client.post(f"/api/waybills/from-line/{ctx['line_id']}")
    assert response.status_code == 409
    assert "Техконтроль: выпуск запрещён" in response.text


def test_advisory_mode_creates_without_medical_and_tech_with_warnings(tmp_path):
    client, ctx = make_client_with_line(tmp_path)
    set_mode(client, "advisory")
    response = client.post(f"/api/waybills/from-line/{ctx['line_id']}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "Нет предрейсового медицинского осмотра" in body["warnings"]
    assert "Нет предрейсового технического контроля" in body["warnings"]


def test_bulk_order_creation_returns_warnings_and_prints_all_waybills(tmp_path):
    client, ctx = make_client_with_line(tmp_path)
    set_mode(client, "advisory")
    response = client.post(f"/api/waybills/from-order/{DATE}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["created"]) == 1
    assert body["blocked"] == []
    assert len(body["warnings"]) == 1
    printed = client.get(f"/api/orders/waybills/print?date={DATE}")
    assert printed.status_code == 200, printed.text
    assert "ПУТЕВОЙ ЛИСТ АВТОБУСА" in printed.text
    assert "Печать всех ПЛ" in printed.text
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_waybill_modes_api.py -q`

Expected: FAIL because `waybill_issue_mode` is not used, `warnings` is absent, and `/api/orders/waybills/print` does not exist.

### Task 2: Backend Implementation

**Files:**
- Modify: `app/db.py`
- Modify: `app/api_waybills.py`
- Test: `tests/test_waybill_modes_api.py`

- [ ] **Step 1: Add default setting**

Add to `ORG_DEFAULTS` in `app/db.py`:

```python
"waybill_issue_mode": "strict_med_tech",
```

- [ ] **Step 2: Add mode helpers and split validation**

In `app/api_waybills.py`, replace `waybill_blockers(con, line)` with helpers:

```python
WAYBILL_MODE_STRICT = "strict_med_tech"
WAYBILL_MODE_MEDICAL_ONLY = "medical_only"
WAYBILL_MODE_ADVISORY = "advisory"
WAYBILL_MODES = {WAYBILL_MODE_STRICT, WAYBILL_MODE_MEDICAL_ONLY, WAYBILL_MODE_ADVISORY}


def waybill_issue_mode(con):
    mode = db.get_settings(con).get("waybill_issue_mode") or WAYBILL_MODE_STRICT
    return mode if mode in WAYBILL_MODES else WAYBILL_MODE_STRICT


def waybill_check(con, line, mode=None):
    mode = mode or waybill_issue_mode(con)
    problems = []
    warnings = []
    # existing common checks remain blocking
    # med/tech checks are routed to problems or warnings according to the spec
    return {"mode": mode, "problems": problems, "warnings": warnings, "medical": med, "tech": tech}
```

Keep common data-quality blockers in `problems` for all modes. In `medical_only`, only missing/non-admitted medical blocks; missing tech warns; explicit `выпуск запрещен` blocks. In `advisory`, medical and tech issues warn only.

- [ ] **Step 3: Update single creation**

Make `waybill_create` use `check = waybill_check(con, line)`. Raise 409 only when `check["problems"]` is non-empty. Insert `medical_check_id` as `check["medical"]["id"] if check["medical"] and check["medical"]["result"] == "допущен" else None`; insert `tech_check_id` similarly only for `выпуск разрешен`. Return:

```python
{"id": cur.lastrowid, "number": num, "warnings": check["warnings"], "mode": check["mode"]}
```

- [ ] **Step 4: Update precheck and bulk creation**

Make `waybill_precheck` return `mode`, `problems`, `warnings`. Make `waybills_from_order` append warnings per line to a top-level `warnings` list, while blocking only on `problems`.

- [ ] **Step 5: Add combined print endpoint**

Extract existing single print HTML building into a helper that can render one waybill without incrementing print count repeatedly for bulk. Add:

```python
@router.get("/orders/waybills/print", response_class=HTMLResponse)
def order_waybills_print(date: str, user=Depends(current_user)):
    # load non-cancelled waybills for the order date ordered by route/output/shift/number
    # return HTML with one print button and each waybill page set appended
```

The combined endpoint should increment `print_count` for each included waybill once and audit `печать всех ПЛ наряда`.

- [ ] **Step 6: Run backend tests and verify GREEN**

Run: `python -m pytest tests/test_waybill_modes_api.py -q`

Expected: PASS.

### Task 3: Frontend Controls

**Files:**
- Modify: `static/app.js`
- Test: `node --check static/app.js`

- [ ] **Step 1: Add settings selector**

In `VIEWS.settings`, add a panel for admins:

```html
<div class="panel"><h3>Правила оформления путевых листов</h3>
  <label class="f">Режим оформления ПЛ
    <select data-set="waybill_issue_mode">
      <option value="strict_med_tech">Медик и механик обязательны</option>
      <option value="medical_only">Обязателен только медик</option>
      <option value="advisory">Свободное оформление с предупреждениями</option>
    </select>
  </label>
</div>
```

After rendering, set the select value from `st.waybill_issue_mode || "strict_med_tech"`.

- [ ] **Step 2: Update order toolbar and waybill toasts**

Add buttons in `VIEWS.order`:

```javascript
<button class="btn sec" onclick="openWin('/api/orders/waybills/print?date=${date}')">Печать всех ПЛ</button>
<button class="btn" onclick="wbFromOrderAndPrint('${date}')">Сформировать и печатать все ПЛ</button>
```

Update `wbCreate` and `wbFromOrder` to show warnings separately from blockers. Add `wbFromOrderAndPrint(date)` that awaits `wbFromOrder(date, true)` and opens `/api/orders/waybills/print?date=${date}`.

- [ ] **Step 3: Update release hint**

In `VIEWS.release`, fetch `/api/settings` and show hint text based on `waybill_issue_mode`.

- [ ] **Step 4: Run JS syntax check**

Run: `node --check static/app.js`

Expected: exit 0.

### Task 4: Regression Verification

**Files:**
- Verify all modified files.

- [ ] **Step 1: Run targeted API tests**

Run: `python -m pytest tests/test_waybill_modes_api.py tests/test_schedule_api.py tests/test_roster_multi_shift_api.py tests/test_erm_route_import.py tests/test_424_advisory_api.py -q`

Expected: all selected tests pass.

- [ ] **Step 2: Run syntax checks**

Run: `python -m py_compile app\api_waybills.py app\db.py app\api_planning.py`

Expected: exit 0.

Run: `node --check static\app.js`

Expected: exit 0.

- [ ] **Step 3: Restart server**

Stop the old PID from `server-manual-check.pid` if present, then start `python run.py` hidden in the project root and write the new PID back to `server-manual-check.pid`.

- [ ] **Step 4: Browser smoke check**

Reload `http://127.0.0.1:8000/#/order` in the in-app browser. Confirm the app loads without a blank page.
