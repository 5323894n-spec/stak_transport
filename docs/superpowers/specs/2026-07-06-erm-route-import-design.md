# ERM Route Import Design

## Goal

Add a route-specific Excel import for ERM workbooks so route data from files like `ERM_M001_20240101.xlsx` can be loaded into the route directory without losing detailed stop data.

## Input Format

The importer accepts `.xlsx` files with sheets named:

- `параметры` for the main route geometry and timing.
- `из парка` for pull-out/depot-to-line sections.
- `в парк` for pull-in/line-to-depot sections.

Each route section is detected by a header row where column B is `п.п.` and column F is `остановочный пункт`. Direction markers such as `прямое направление` and `обратное направление` define whether parsed stops belong to the forward or backward route.

## Route Mapping

The importer extracts the route number and name from a title like `Маршрут № 1 "Железнодорожный вокзал - Ореховая улица"`.

It creates a new route if the number is not present. If the number already exists, it updates the existing route. The route card receives:

- `number`
- `name`
- `start_point`
- `end_point`
- `stops`
- `stops_back`
- `length_km`
- `length_back_km`
- `trip_time_min`
- `trip_time_back_min`
- `notes`

The stop lists stay human-readable in the existing fields. Detailed data from all sheets is preserved as JSON in `notes`, including stop ids, streets, coordinates, distances, cumulative distances, travel times, source sheet, and section type.

## User Flow

On the `Маршруты` page, users get an extra button `Импорт ЭРМ`. Selecting a workbook uploads it to a route-specific endpoint. The UI shows a toast with created/updated route, stop counts, and route id, then reloads references.

The existing generic `Импорт из Excel` button remains unchanged for simple tabular reference imports.

## Errors

The endpoint rejects non-readable files, files without the `параметры` sheet, and workbooks where a route title or main route stops cannot be detected. Error text is returned through the existing toast path.

## Testing

Tests cover parsing a workbook shaped like the ERM sample, creating a new route, updating an existing route by route number, preserving depot sections in notes, and keeping route fields usable by schedule generation.
