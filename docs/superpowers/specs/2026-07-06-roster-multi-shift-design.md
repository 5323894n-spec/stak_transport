# Roster Multi-Shift Assignment Design

## Goal

Allow one driver to work more than one route shift on the same date from the roster screen. When a route, output, and shift are selected, the schedule for that shift must be loaded automatically, editable by trip number or time, and saved without blocking the user when overtime warnings appear.

## Current Constraint

The existing `roster` table has one row per `driver_id + date`. It is good for the day status: work, day off, reserve, absence. It cannot safely represent two separate work assignments for the same driver on the same date.

## Data Model

Keep `roster` as the day-level record.

Add a new child table `roster_assignments` with one row per work assignment:

- `id`
- `driver_id`
- `date`
- `route_id`
- `day_type`
- `output_number`
- `shift_number`
- `trip_from`
- `trip_to`
- `start_time`
- `end_time`
- `hours`
- `night_hours`
- `break_min`
- `distance_km`
- `trips_count`
- `comment`

This allows a driver to have:

- route 2, output 1, shift 1
- route 2, output 1, shift 2

on the same date.

The parent `roster` row remains status `работа` and stores a compact aggregate view for compatibility: earliest start, latest end, total hours, total night hours, total break minutes, and a comment that says how many assignments were added.

## Schedule Lookup

Add an API endpoint that returns schedule options for a roster cell:

`GET /api/roster/schedule-options?route_id=...&date=...&output_number=...&shift_number=...`

The endpoint resolves the schedule day type from the production calendar and reads `route_trips`. It returns:

- available outputs and shifts for the route/date
- trips for the selected output/shift
- suggested assignment values: first trip, last trip, start time, end time, hours, breaks, distance, trip count

If only the route is selected, it returns available outputs and shifts. If route/output/shift are selected, it returns the trip list and suggested values.

## Editing Flow

The roster cell modal becomes an assignment editor:

1. Status and day comment stay at the top.
2. A block `Назначения на день` lists existing assignments.
3. User clicks `+ смена`.
4. User chooses route, output, and shift.
5. The form loads the matching trips.
6. User can select:
   - entire shift
   - from trip number to trip number
   - custom start and end time
7. The form recalculates hours, night hours, breaks, trip count, and distance.
8. User saves the assignment.

Existing assignments can be edited or deleted.

## Overtime Handling

Saving an assignment always stores the data first, then runs the existing RTO checks around the edited date.

If violations are found:

- show a dismissible warning panel/toast
- include violation type, norm, fact, and recommendation
- allow closing the warning and continuing work

The warning does not rollback the saved assignment.

## Order Generation

When generating daily orders, use `roster_assignments` first.

For each route/output/shift:

- if an assignment exists for the date/route/output/shift, use its driver
- allow the same driver on multiple order lines when those lines came from separate roster assignments
- if no assignment exists, fall back to the old `roster` matching logic

This keeps existing data working while making the new detailed assignments authoritative.

## Compatibility

Existing roster screens and tests should continue to work:

- old `roster` rows still appear in the month grid
- auto-generation can keep writing the day-level roster row
- new manual assignments add precision without requiring old rows to be migrated immediately

## Testing

Tests must cover:

- creating multiple assignments for one driver and date
- schedule option calculation from `route_trips`
- assigning full shift by route/output/shift
- assigning a partial trip range
- saving despite overtime violations
- order generation using the new assignment table and allowing one driver on shift 1 and shift 2
