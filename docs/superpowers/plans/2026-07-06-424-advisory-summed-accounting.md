# Advisory 424 Summed Accounting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert Order 424 labor/rest control to warning-only behavior and use the configurable summed-accounting period for overtime checks.

**Architecture:** Keep `app/norms.py` as the central labor/rest rules engine. API endpoints may still block non-424 operational omissions, but they must not reject roster/order approval because of 424 warnings. Schedule checks keep physical data-quality errors, while 424-style break and output-duration issues become warnings.

**Tech Stack:** FastAPI, SQLite, pytest/TestClient, vanilla JavaScript for existing UI labels only if needed.

---

### Task 1: Warning-Only Regression Tests

**Files:**
- Create: `tests/test_424_advisory_api.py`

- [ ] **Step 1: Write tests**

Create tests for long-shift roster approval, order approval with only 424 warnings, three-month accounting-period overtime, and schedule break/lunch warning severity.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_424_advisory_api.py -q`
Expected: failures because current code returns `критично/ошибка`, blocks approvals, and does not use three-month accounting periods.

### Task 2: Normalize 424 Violations To Warnings

**Files:**
- Modify: `app/norms.py`

- [ ] **Step 1: Add warning normalizer**

Add a helper that rewrites all `severity` values from `check_driver_roster` to `предупреждение` before returning.

- [ ] **Step 2: Use configured accounting period**

Replace the hard-coded monthly overtime check with a configurable period check using `accounting_period_months` and `month_norm_hours` over the full requested period.

- [ ] **Step 3: Improve driving-time source**

For a roster entry, calculate driving time from matching `route_trips` or `roster_assignments` when possible; otherwise keep the current fallback.

### Task 3: Remove 424 Approval Blocks

**Files:**
- Modify: `app/api_planning.py`

- [ ] **Step 1: Roster approval**

Let `/api/roster/approve` approve even when warnings exist; return warning counts for display/audit.

- [ ] **Step 2: Order approval**

Let `/api/orders/{oid}/status` approve when the only issues are 424 warnings. Keep missing driver/bus blockers.

- [ ] **Step 3: Schedule warnings**

Change 424-style schedule checks (`break_gap`, `short_rest`, `long_output_without_shift_split`, `missing_lunch`) to severity `предупреждение`.

### Task 4: Verification

**Files:**
- Existing tests and app files.

- [ ] **Step 1: Run focused tests**

Run: `python -m pytest tests/test_424_advisory_api.py tests/test_roster_multi_shift_api.py tests/test_schedule_api.py -q`
Expected: all pass.

- [ ] **Step 2: Run regression tests**

Run: `python -m pytest tests/test_schedule_api.py tests/test_roster_multi_shift_api.py tests/test_erm_route_import.py tests/test_424_advisory_api.py -q`
Expected: all pass.

- [ ] **Step 3: Compile Python**

Run: `python -m py_compile app/norms.py app/api_planning.py`
Expected: exit code 0.
