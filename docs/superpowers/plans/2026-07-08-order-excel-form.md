# Order Excel Form Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the existing order-day Excel export with a polished printable workbook form.

**Architecture:** Keep the existing `/api/orders/export.xlsx` URL and add a specialized `order_xlsx_response` helper in `app/xl.py` so the generic `xlsx_response` remains unchanged for other exports. `app/api_planning.py::order_export` gathers order, settings, and line data, then delegates workbook layout and styling to the helper. A focused API test opens the generated workbook with `openpyxl` and verifies structure, print settings, and key content.

**Tech Stack:** FastAPI, SQLite, openpyxl, pytest, FastAPI TestClient.

---

### Task 1: RED Test For Formatted Order Export

**Files:**
- Create: `tests/test_order_excel_export.py`

- [ ] **Step 1: Write failing test**

Create a test that builds a temporary approved order, calls `/api/orders/export.xlsx`, loads the workbook with `openpyxl`, and asserts:

```python
assert ws["A2"].value == "НАРЯД НА ВЫПУСК АВТОБУСОВ"
assert "Отметки" in headers
assert ws.page_setup.orientation == "landscape"
assert ws.freeze_panes == f"A{header_row + 1}"
assert ws.auto_filter.ref == f"A{header_row}:R{header_row + 1}"
assert "Диспетчер" in all_text
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_order_excel_export.py -q`

Expected: FAIL because the current export uses generic `xlsx_response` and has no printable form header at `A2`.

### Task 2: Workbook Helper Implementation

**Files:**
- Modify: `app/xl.py`

- [ ] **Step 1: Add `order_xlsx_response`**

Add a helper that creates one sheet `Наряд YYYY-MM-DD` with:

```python
HEADER_ROW = 9
DATA_ROW = 10
headers = ["Маршрут", "Выход", "Смена", "Водитель", "Таб.№", "Автобус (гар.)", "Госномер", "Явка", "Выезд", "Начало", "Окончание", "Заезд", "Часы", "Рейсов", "Пробег, км", "План. топливо, л", "Статус", "Отметки"]
```

The helper must set title rows, summary formulas, filters, freeze panes, page setup, widths, wraps, status fills, warning fills for missing driver/bus, and signature rows.

- [ ] **Step 2: Keep generic export unchanged**

Do not change behavior of existing `xlsx_response`; other exports continue to use it.

### Task 3: API Wiring

**Files:**
- Modify: `app/api_planning.py`

- [ ] **Step 1: Import helper**

Change import to:

```python
from .xl import xlsx_response, order_xlsx_response
```

- [ ] **Step 2: Use helper in `order_export`**

Load settings with `db.get_settings(con)`, include `dispatcher_note` in the SELECT result, and return:

```python
return order_xlsx_response(o, settings, lines, filename=f"naryad_{date}.xlsx")
```

### Task 4: Verification And Git

**Files:**
- Verify: `app/xl.py`, `app/api_planning.py`, `tests/test_order_excel_export.py`

- [ ] **Step 1: Verify GREEN**

Run: `python -m pytest tests/test_order_excel_export.py -q`

Expected: PASS.

- [ ] **Step 2: Regression checks**

Run: `python -m pytest tests/test_order_excel_export.py tests/test_waybill_modes_api.py tests/test_schedule_api.py -q`

Expected: all selected tests pass.

Run: `python -m py_compile app\xl.py app\api_planning.py`

Expected: exit 0.

- [ ] **Step 3: Commit and push**

Commit implementation and push the branch to GitHub. If local `main` needs updating, merge/fast-forward after verification and push `main` too.
