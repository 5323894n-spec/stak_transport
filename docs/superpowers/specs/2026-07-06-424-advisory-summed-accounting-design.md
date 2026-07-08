# Advisory 424 Rules With Summed Accounting Design

## Goal
The system must support summed working-time accounting for drivers and treat violations of Order 424 labor/rest rules as warnings only. Users may continue editing, assigning, approving rosters, and approving orders after reading the warnings.

## Scope
This change applies to schedule checks, roster checks, driver assignment warnings, roster approval, order approval, and overtime checks. It does not remove non-424 operational blockers such as missing driver or missing bus in an order line.

## Rules
- All 424 labor/rest violations returned by `app.norms` use severity `предупреждение`.
- Schedule problems related to 424-style breaks and output length use severity `предупреждение`.
- Roster approval never rejects because of 424 violations.
- Order approval never rejects because of 424 violations.
- `accounting_period_months` remains editable in Norms and is used for overtime checks.
- Default accounting period remains one month.
- Driving time is calculated from scheduled trips when route/output/shift or roster assignments are available. If exact trips cannot be found, the engine falls back to shift hours minus prep/final minutes.

## Checks Kept As Warnings
- Shift longer than normal 10 hours and summed-accounting 12 hours.
- Daily driving over 9 hours and over 10 hours.
- More than two 10-hour driving extensions per week.
- Weekly driving over 56 hours.
- Two-week driving over 90 hours.
- Missing 45-minute break after 4.5 hours of driving.
- Insufficient intershift rest.
- Missing weekly rest of at least 42 consecutive hours.
- Overtime above the configured accounting-period norm.
- Schedule break gap and missing lunch/break warnings.

## Evidence And Safety
The runtime could not fetch the current official legal text due network restrictions, so the values remain data-driven in the Norms section rather than hard-coded. The defaults already match the application's current 424 baseline and can be adjusted by an administrator.

## Tests
Add regression tests that prove:
- A too-long driver shift is returned as `предупреждение` and roster approval succeeds without `force_comment`.
- Order approval succeeds when the only issue is a 424 warning.
- A three-month accounting period is used for overtime warnings.
- Schedule break/lunch violations are warnings.
