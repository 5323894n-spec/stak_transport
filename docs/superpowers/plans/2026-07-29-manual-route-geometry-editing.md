# Manual Route Geometry Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в карточку маршрута безопасное ручное редактирование и постоянное хранение линии трассы отдельно для прямого и обратного направлений.

**Architecture:** SQLite хранит по одной версионируемой GeoJSON `LineString` на маршрут и направление. Отдельный Python-модуль отвечает за валидацию, обязательные якоря остановок и оптимистическую блокировку; существующий API маршрута предоставляет чтение, сохранение, сброс и атомарное применение OSRM. Чистая JavaScript-модель редактора отделена от Leaflet-привязки, чтобы операции над вершинами проверялись исполняемыми Node-тестами.

**Tech Stack:** Python 3, FastAPI, SQLite, pytest, JavaScript ES2020 без сборщика, Leaflet 1.9.4, Node.js test harness, HTML/CSS.

---

## Карта файлов

- `app/route_schema.py` — идемпотентная таблица `route_geometries` и индексы.
- `app/route_geometry.py` — единая серверная модель GeoJSON: чтение, валидация, привязка к остановкам, версии, сохранение, сброс и нормализация OSRM.
- `app/api_route_network.py` — HTTP-контракты геометрии, включение геометрий в network-ответ, аудит и атомарное применение OSRM.
- `static/route-geometry-editor.js` — чистые операции над черновиком и индексами вершин без зависимости от DOM/Leaflet.
- `static/route-card.js` — состояние редактора, команды UI, запросы API, защита несохранённых изменений и Leaflet-слои.
- `static/app.js` — сохранение HTTP-статуса в ошибке API и безопасный выход из карточки.
- `static/styles.css` — маркеры контрольных точек, выбранное состояние, панель команд и OSRM-предпросмотр.
- `static/index.html` — подключение нового JavaScript-файла и обновление cache-key изменённых ресурсов.
- `tests/test_route_geometry_schema.py` — миграция, ограничения и каскадное удаление.
- `tests/test_route_geometry_api.py` — CRUD, права, валидация, версии, аудит и согласованность остановок.
- `tests/test_route_osrm.py` — OSRM-якоря и атомарная запись расстояний вместе с геометрией.
- `tests/js/route_geometry_editor_behavior.js` — исполняемые сценарии чистой модели и пользовательских команд.
- `tests/test_route_geometry_frontend.py` — pytest-обёртка Node-сценариев и статические проверки интеграции.
- `tests/test_route_card_map_frontend.py` — обновлённые ожидания Leaflet-карты и cache-key.

### Task 1: Схема хранения геометрии

**Files:**
- Modify: `app/route_schema.py:1-130`
- Create: `tests/test_route_geometry_schema.py`

- [ ] **Step 1: Написать падающие тесты миграции**

```python
# tests/test_route_geometry_schema.py
import sqlite3

from app import db
from app.route_schema import migrate_route_network


def test_route_geometry_migration_is_idempotent(tmp_path):
    db.DB_PATH = str(tmp_path / "geometry-schema.db")
    db.init_db()
    con = db.connect()
    try:
        migrate_route_network(con)
        migrate_route_network(con)
        columns = {row[1] for row in con.execute("PRAGMA table_info(route_geometries)")}
        assert columns == {
            "id", "route_id", "direction", "geometry_json", "source",
            "version", "updated_by", "created_at", "updated_at",
        }
    finally:
        con.close()


def test_route_geometry_is_unique_per_direction_and_cascades(tmp_path):
    db.DB_PATH = str(tmp_path / "geometry-cascade.db")
    db.init_db()
    con = db.connect()
    try:
        route_id = con.execute("INSERT INTO routes(number) VALUES('12')").lastrowid
        values = (route_id, "forward", '{"type":"LineString","coordinates":[[1,1],[2,2]]}', "manual", 1, "admin")
        con.execute("INSERT INTO route_geometries(route_id,direction,geometry_json,source,version,updated_by) VALUES(?,?,?,?,?,?)", values)
        with __import__("pytest").raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO route_geometries(route_id,direction,geometry_json,source,version,updated_by) VALUES(?,?,?,?,?,?)", values)
        con.execute("DELETE FROM routes WHERE id=?", (route_id,))
        assert con.execute("SELECT COUNT(*) FROM route_geometries").fetchone()[0] == 0
    finally:
        con.close()
```

- [ ] **Step 2: Запустить тест и подтвердить ожидаемое падение**

Run: `python -m pytest tests/test_route_geometry_schema.py -q`

Expected: FAIL с `no such table: route_geometries`.

- [ ] **Step 3: Добавить таблицу в `ROUTE_NETWORK_SCHEMA`**

```sql
CREATE TABLE IF NOT EXISTS route_geometries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  route_id INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
  direction TEXT NOT NULL CHECK(direction IN ('forward','backward')),
  geometry_json TEXT NOT NULL,
  source TEXT NOT NULL CHECK(source IN ('manual','osrm')),
  version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
  updated_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(route_id,direction)
);
CREATE INDEX IF NOT EXISTS idx_route_geometries_route
  ON route_geometries(route_id,direction);
```

- [ ] **Step 4: Запустить тест схемы**

Run: `python -m pytest tests/test_route_geometry_schema.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Зафиксировать миграцию**

```bash
git add app/route_schema.py tests/test_route_geometry_schema.py
git commit -m "feat(routes): add route geometry storage"
```

### Task 2: Серверная модель и строгая валидация GeoJSON

**Files:**
- Create: `app/route_geometry.py`
- Create: `tests/test_route_geometry_api.py`

- [ ] **Step 1: Создать фикстуры маршрута и падающие unit-тесты валидатора**

```python
# начало tests/test_route_geometry_api.py
import json
import math

import pytest
from fastapi.testclient import TestClient

from app.route_geometry import GeometryValidationError, validate_geometry


ANCHORS = [(35.901, 56.801), (35.902, 56.802)]


def line(*coordinates):
    return {"type": "LineString", "coordinates": list(coordinates)}


@pytest.fixture
def geometry_client(tmp_path):
    import app.db as db
    from app.auth import ensure_admin
    from app.main import app

    db.DB_PATH = str(tmp_path / "route-geometry-api.db")
    db.init_db()
    con = db.connect()
    try:
        ensure_admin(con)
    finally:
        con.close()
    client = TestClient(app)
    token = client.post("/api/login", json={"username": "admin", "password": "admin"}).json()["token"]
    client.headers.update({"Authorization": "Bearer " + token})
    route_id = client.post("/api/refs/routes", json={"number": "G-1", "name": "Геометрия"}).json()["id"]
    forward = [(35.901, 56.801), (35.902, 56.802)]
    backward = list(reversed(forward))
    stop_ids = []
    for index, point in enumerate(forward):
        response = client.post("/api/stops", json={
            "name": f"Остановка {index + 1}", "external_code": f"G{index + 1}",
            "longitude": point[0], "latitude": point[1],
        })
        stop_ids.append(response.json()["id"])
    for direction, ordered_ids in (("forward", stop_ids), ("backward", list(reversed(stop_ids)))):
        response = client.put(f"/api/routes/{route_id}/stops/{direction}", json={"items": [
            {"stop_id": stop_id, "sequence": index + 1, "distance_from_prev_km": 0 if index == 0 else 1}
            for index, stop_id in enumerate(ordered_ids)
        ]})
        assert response.status_code == 200, response.text
    return client, route_id, forward, backward


def test_validate_geometry_accepts_ordered_stop_anchors():
    geometry = line(ANCHORS[0], (35.9015, 56.8015), ANCHORS[1])
    assert validate_geometry(geometry, ANCHORS) == geometry


@pytest.mark.parametrize("geometry", [
    {},
    {"type": "Point", "coordinates": list(ANCHORS)},
    line(ANCHORS[0]),
    line(ANCHORS[0], ANCHORS[0], ANCHORS[1]),
    line(ANCHORS[1], ANCHORS[0]),
    line((181, 56.8), ANCHORS[1]),
    line((35.9, math.inf), ANCHORS[1]),
])
def test_validate_geometry_rejects_invalid_contract(geometry):
    with pytest.raises(GeometryValidationError):
        validate_geometry(geometry, ANCHORS)


def test_validate_geometry_enforces_coordinate_and_payload_limits():
    oversized = line(*[(35.9 + index / 1_000_000, 56.8) for index in range(20_001)])
    with pytest.raises(GeometryValidationError, match="20 000"):
        validate_geometry(oversized, ANCHORS)
```

- [ ] **Step 2: Запустить unit-тесты и увидеть отсутствие модуля**

Run: `python -m pytest tests/test_route_geometry_api.py -q`

Expected: collection ERROR с `ModuleNotFoundError: app.route_geometry`.

- [ ] **Step 3: Реализовать публичные типы и валидатор**

```python
# app/route_geometry.py
import json
import math


DIRECTIONS = ("forward", "backward")
ANCHOR_TOLERANCE = 0.000001
MAX_COORDINATES = 20_000
MAX_GEOMETRY_BYTES = 2 * 1024 * 1024


class GeometryValidationError(ValueError):
    pass


class GeometryVersionConflict(ValueError):
    pass


def _same_coordinate(left, right):
    return (
        abs(left[0] - right[0]) <= ANCHOR_TOLERANCE
        and abs(left[1] - right[1]) <= ANCHOR_TOLERANCE
    )


def validate_geometry(geometry, anchors):
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        raise GeometryValidationError("Геометрия должна быть GeoJSON LineString")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise GeometryValidationError("Линия должна содержать минимум две координаты")
    if len(coordinates) > MAX_COORDINATES:
        raise GeometryValidationError("Линия не может содержать более 20 000 координат")
    normalized = []
    for point in coordinates:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise GeometryValidationError("Каждая координата должна содержать долготу и широту")
        if isinstance(point[0], bool) or isinstance(point[1], bool):
            raise GeometryValidationError("Координаты должны быть числами")
        longitude, latitude = float(point[0]), float(point[1])
        if not math.isfinite(longitude) or not math.isfinite(latitude):
            raise GeometryValidationError("Координаты должны быть конечными числами")
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise GeometryValidationError("Координаты выходят за допустимый диапазон")
        current = [longitude, latitude]
        if normalized and _same_coordinate(normalized[-1], current):
            raise GeometryValidationError("Соседние координаты не должны дублироваться")
        normalized.append(current)
    cursor = 0
    for anchor in anchors:
        found = next((index for index in range(cursor, len(normalized)) if _same_coordinate(normalized[index], anchor)), None)
        if found is None:
            raise GeometryValidationError("Линия должна проходить через все остановки по порядку")
        cursor = found + 1
    result = {"type": "LineString", "coordinates": normalized}
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_GEOMETRY_BYTES:
        raise GeometryValidationError("Геометрия превышает допустимый размер 2 МБ")
    return result


def validate_geometry_shape(geometry):
    return validate_geometry(geometry, [])["coordinates"]
```

- [ ] **Step 4: Добавить функции чтения остановок и сериализации записи**

```python
def stop_anchors(con, route_id, direction):
    rows = con.execute(
        "SELECT s.longitude,s.latitude FROM route_stops rs "
        "JOIN stops s ON s.id=rs.stop_id WHERE rs.route_id=? AND rs.direction=? "
        "ORDER BY rs.sequence",
        (route_id, direction),
    ).fetchall()
    if len(rows) < 2:
        raise GeometryValidationError("Для линии нужны минимум две остановки")
    if any(row[0] is None or row[1] is None for row in rows):
        raise GeometryValidationError("У всех остановок должны быть координаты")
    return [(float(row[0]), float(row[1])) for row in rows]


def geometry_record(row):
    if row is None:
        return None
    return {
        "geometry": json.loads(row["geometry_json"]),
        "source": row["source"],
        "version": row["version"],
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


def get_geometry(con, route_id, direction):
    row = con.execute(
        "SELECT * FROM route_geometries WHERE route_id=? AND direction=?",
        (route_id, direction),
    ).fetchone()
    return geometry_record(row)
```

- [ ] **Step 5: Запустить unit-тесты валидатора**

Run: `python -m pytest tests/test_route_geometry_api.py -q`

Expected: тесты валидатора PASS.

- [ ] **Step 6: Зафиксировать серверную модель**

```bash
git add app/route_geometry.py tests/test_route_geometry_api.py
git commit -m "feat(routes): validate route line geometry"
```

### Task 3: API чтения, сохранения, сброса и версий

**Files:**
- Modify: `app/route_geometry.py`
- Modify: `app/api_route_network.py:218-236`
- Modify: `tests/test_route_geometry_api.py`

- [ ] **Step 1: Добавить падающие API-тесты CRUD, направлений и конфликта**

```python
def test_geometry_crud_is_independent_per_direction(geometry_client):
    client, route_id, forward, backward = geometry_client
    created = client.put(f"/api/routes/{route_id}/geometry/forward", json={
        "geometry": line(forward[0], (35.9015, 56.8015), forward[1]),
        "expected_version": 0,
    })
    assert created.status_code == 200, created.text
    assert created.json()["version"] == 1
    network = client.get(f"/api/routes/{route_id}/network").json()
    assert network["geometries"]["forward"]["source"] == "manual"
    assert network["geometries"]["backward"] is None

    conflict = client.put(f"/api/routes/{route_id}/geometry/forward", json={
        "geometry": line(forward[0], forward[1]),
        "expected_version": 0,
    })
    assert conflict.status_code == 409
    assert client.get(f"/api/routes/{route_id}/network").json()["geometries"]["forward"]["version"] == 1

    deleted = client.request(
        "DELETE", f"/api/routes/{route_id}/geometry/forward",
        json={"expected_version": 1},
    )
    assert deleted.status_code == 200
    assert client.get(f"/api/routes/{route_id}/network").json()["geometries"]["forward"] is None


@pytest.mark.parametrize("direction", ["sideways", "", "FORWARD"])
def test_geometry_api_rejects_unknown_direction(geometry_client, direction):
    client, route_id, forward, backward = geometry_client
    response = client.put(f"/api/routes/{route_id}/geometry/{direction}", json={
        "geometry": line(forward[0], forward[1]), "expected_version": 0,
    })
    assert response.status_code == 400
```

- [ ] **Step 2: Запустить API-тесты и подтвердить 404 маршрута API**

Run: `python -m pytest tests/test_route_geometry_api.py -q`

Expected: новые CRUD-тесты FAIL с `404`.

- [ ] **Step 3: Реализовать оптимистическое сохранение и сброс в доменном модуле**

```python
def save_geometry(con, route_id, direction, geometry, source, expected_version, username, timestamp):
    if direction not in DIRECTIONS or source not in ("manual", "osrm"):
        raise GeometryValidationError("Некорректное направление или источник геометрии")
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
        raise GeometryValidationError("expected_version должен быть неотрицательным целым числом")
    normalized = validate_geometry(geometry, stop_anchors(con, route_id, direction))
    current = con.execute(
        "SELECT * FROM route_geometries WHERE route_id=? AND direction=?",
        (route_id, direction),
    ).fetchone()
    actual_version = current["version"] if current else 0
    if actual_version != expected_version:
        raise GeometryVersionConflict("Линия уже изменена другим пользователем")
    payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    if current:
        updated = con.execute(
            "UPDATE route_geometries SET geometry_json=?,source=?,version=?,updated_by=?,updated_at=? WHERE id=? AND version=?",
            (payload, source, actual_version + 1, username, timestamp, current["id"], actual_version),
        )
        if updated.rowcount != 1:
            raise GeometryVersionConflict("Линия уже изменена другим пользователем")
    else:
        con.execute(
            "INSERT INTO route_geometries(route_id,direction,geometry_json,source,version,updated_by,created_at,updated_at) VALUES(?,?,?,?,1,?,?,?)",
            (route_id, direction, payload, source, username, timestamp, timestamp),
        )
    return current, get_geometry(con, route_id, direction)


def delete_geometry(con, route_id, direction, expected_version):
    if direction not in DIRECTIONS:
        raise GeometryValidationError("Направление должно быть forward или backward")
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
        raise GeometryValidationError("expected_version должен быть неотрицательным целым числом")
    current = con.execute(
        "SELECT * FROM route_geometries WHERE route_id=? AND direction=?",
        (route_id, direction),
    ).fetchone()
    actual_version = current["version"] if current else 0
    if actual_version != expected_version:
        raise GeometryVersionConflict("Линия уже изменена другим пользователем")
    if current:
        con.execute("DELETE FROM route_geometries WHERE id=? AND version=?", (current["id"], actual_version))
    return geometry_record(current)
```

- [ ] **Step 4: Добавить `geometries` в network-ответ и два endpoint**

```python
# imports app/api_route_network.py
from .route_geometry import (
    DIRECTIONS, GeometryValidationError, GeometryVersionConflict,
    delete_geometry, get_geometry, save_geometry,
)

# внутри GET /routes/{route_id}/network
"geometries": {
    direction: get_geometry(con, route_id, direction)
    for direction in DIRECTIONS
},

@router.put("/routes/{route_id}/geometry/{direction}")
def route_geometry_put(route_id: int, direction: str, payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "routes")
    con = db.connect()
    try:
        _route_or_404(con, route_id)
        old, saved = save_geometry(
            con, route_id, direction, payload.get("geometry"), "manual",
            payload.get("expected_version"), user["username"], _now(),
        )
        db.audit(con, user["username"], "сохранение геометрии маршрута", "route_geometry", route_id,
                 old={"direction": direction, "source": old["source"] if old else None, "version": old["version"] if old else 0},
                 new={"direction": direction, "source": saved["source"], "version": saved["version"], "coordinates": len(saved["geometry"]["coordinates"])})
        con.commit()
        return saved
    except GeometryVersionConflict as exc:
        con.rollback()
        raise HTTPException(409, str(exc))
    except GeometryValidationError as exc:
        con.rollback()
        raise HTTPException(400, str(exc))
    finally:
        con.close()


@router.delete("/routes/{route_id}/geometry/{direction}")
def route_geometry_delete(route_id: int, direction: str, payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "routes")
    con = db.connect()
    try:
        _route_or_404(con, route_id)
        old = delete_geometry(con, route_id, direction, payload.get("expected_version"))
        db.audit(con, user["username"], "сброс геометрии маршрута", "route_geometry", route_id,
                 old={"direction": direction, "source": old["source"] if old else None, "version": old["version"] if old else 0},
                 new={"direction": direction, "source": None, "version": 0})
        con.commit()
        return {"ok": True, "direction": direction}
    except GeometryVersionConflict as exc:
        con.rollback()
        raise HTTPException(409, str(exc))
    except GeometryValidationError as exc:
        con.rollback()
        raise HTTPException(400, str(exc))
    finally:
        con.close()
```

- [ ] **Step 5: Добавить тесты `403`, `404`, неверного GeoJSON, аудита и лимита 2 МБ**

Проверить, что каждый неуспешный запрос оставляет строку и `audit_log` неизменными, а успешный аудит содержит только направление, источник, версию и число координат — без полного GeoJSON.

```python
assert response.status_code in (400, 403, 404, 409)
assert con.execute("SELECT COUNT(*) FROM route_geometries").fetchone()[0] == before_geometry_count
assert con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before_audit_count
```

- [ ] **Step 6: Запустить API-тесты**

Run: `python -m pytest tests/test_route_geometry_api.py tests/test_route_network_api.py -q`

Expected: все тесты PASS.

- [ ] **Step 7: Зафиксировать API геометрии**

```bash
git add app/route_geometry.py app/api_route_network.py tests/test_route_geometry_api.py
git commit -m "feat(routes): expose versioned route geometry API"
```

### Task 4: Нормализация и атомарное применение OSRM

**Files:**
- Modify: `app/route_geometry.py`
- Modify: `app/api_route_network.py:466-590`
- Modify: `tests/test_route_osrm.py`

- [ ] **Step 1: Написать падающие тесты якорей и атомарности**

```python
def test_osrm_preview_snaps_geometry_to_exact_stop_anchors(tmp_path, monkeypatch):
    client, route_id = make_client(tmp_path)
    add_trace(client, route_id)
    monkeypatch.setattr(osrm, "request_route", lambda coordinates, **kwargs: {
        "geometry": {"type": "LineString", "coordinates": [
            [35.9010004, 56.8010004], [35.9015, 56.8015], [35.9020004, 56.8020004],
        ]},
        "legs": [{"distance": 1500, "duration": 240}],
    })
    preview = client.post(f"/api/routes/{route_id}/osrm/preview/forward")
    assert preview.json()["geometry"]["coordinates"][0] == [35.901, 56.801]
    assert preview.json()["geometry"]["coordinates"][-1] == [35.902, 56.802]


def test_osrm_apply_checks_geometry_version_and_writes_atomically(tmp_path, monkeypatch):
    client, route_id = make_client(tmp_path)
    add_trace(client, route_id)
    monkeypatch.setattr(osrm, "request_route", lambda coordinates, **kwargs: {
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "legs": [{"distance": 1500, "duration": 240}],
    })
    preview = client.post(f"/api/routes/{route_id}/osrm/preview/forward").json()
    conflict = client.post(f"/api/routes/{route_id}/osrm/apply/forward", json={
        "preview_token": preview["preview_token"], "expected_geometry_version": 1,
    })
    assert conflict.status_code == 409
    assert client.get(f"/api/routes/{route_id}/network").json()["forward"][1]["distance_from_prev_km"] == 1.0
```

- [ ] **Step 2: Запустить OSRM-тесты и подтвердить падение**

Run: `python -m pytest tests/test_route_osrm.py -q`

Expected: новые проверки точных якорей и версии FAIL.

- [ ] **Step 3: Реализовать нормализацию OSRM по ближайшим последовательным сегментам**

```python
def normalize_osrm_geometry(geometry, anchors):
    coordinates = validate_geometry_shape(geometry)
    if len(coordinates) < len(anchors):
        raise GeometryValidationError("OSRM вернул меньше координат, чем остановок")
    cursor = 0
    for anchor_index, anchor in enumerate(anchors):
        remaining = len(anchors) - anchor_index - 1
        upper = max(cursor, len(coordinates) - remaining - 1)
        nearest = min(
            range(cursor, upper + 1),
            key=lambda index: (coordinates[index][0] - anchor[0]) ** 2 + (coordinates[index][1] - anchor[1]) ** 2,
        )
        coordinates[nearest] = [float(anchor[0]), float(anchor[1])]
        cursor = nearest + 1
    return validate_geometry({"type": "LineString", "coordinates": coordinates}, anchors)
```

`validate_geometry_shape` выполняет числовые, диапазонные, размерные проверки `validate_geometry`, но не проверяет якоря; это позволяет сначала точно вставить остановки, затем вызвать полный валидатор.

- [ ] **Step 4: Нормализовать preview и добавить ожидаемую версию в план OSRM**

```python
anchors = stop_anchors(con, route_id, direction)
geometry = normalize_osrm_geometry(calculated["geometry"], anchors)
current_geometry = get_geometry(con, route_id, direction)
plan = {
    "kind": "osrm",
    "route_id": route_id,
    "direction": direction,
    "diff": diff,
    "geometry": geometry,
    "expected_geometry_version": current_geometry["version"] if current_geometry else 0,
}
```

Preview-ответ возвращает `geometry` и `geometry_version` из плана.

- [ ] **Step 5: Сохранять OSRM-геометрию в той же транзакции, что и расстояния**

Перед циклом обновления перегонов сравнить `payload["expected_geometry_version"]` с версией, записанной в preview-плане. После пересчёта cumulative вызвать `save_geometry` с источником `osrm` и ожидаемой версией из payload. `preview.applied_at`, расстояния, геометрия и единая запись аудита коммитятся одним `con.commit()`; любое исключение выполняет `con.rollback()`.

```python
expected_version = payload.get("expected_geometry_version")
if expected_version != plan["expected_geometry_version"]:
    raise HTTPException(409, "Сохранённая линия изменилась после расчёта OSRM")
old_geometry, saved_geometry = save_geometry(
    con, route_id, direction, plan["geometry"], "osrm", expected_version,
    user["username"], now.isoformat(timespec="seconds"),
)
```

- [ ] **Step 6: Запустить OSRM и geometry API-тесты**

Run: `python -m pytest tests/test_route_osrm.py tests/test_route_geometry_api.py -q`

Expected: все тесты PASS, включая повторное применение preview и конфликт без частичных расстояний.

- [ ] **Step 7: Зафиксировать OSRM-интеграцию**

```bash
git add app/route_geometry.py app/api_route_network.py tests/test_route_osrm.py
git commit -m "feat(routes): persist OSRM geometry atomically"
```

### Task 5: Согласованность при изменении остановок и состава трассы

**Files:**
- Modify: `app/route_geometry.py`
- Modify: `app/api_route_network.py:239-340`
- Modify: `tests/test_route_geometry_api.py`

- [ ] **Step 1: Написать падающие тесты изменения якоря и состава остановок**

```python
def test_moving_stop_replaces_anchor_and_increments_geometry_version(geometry_client):
    client, route_id, forward, backward = geometry_client
    saved = client.put(f"/api/routes/{route_id}/geometry/forward", json={
        "geometry": line(forward[0], forward[1]), "expected_version": 0,
    }).json()
    stop_id = client.get(f"/api/routes/{route_id}/network").json()["forward"][0]["stop"]["id"]
    response = client.put(f"/api/stops/{stop_id}", json={"longitude": 35.9005, "latitude": 56.8005})
    assert response.status_code == 200
    geometry = client.get(f"/api/routes/{route_id}/network").json()["geometries"]["forward"]
    assert geometry["version"] == saved["version"] + 1
    assert geometry["geometry"]["coordinates"][0] == [35.9005, 56.8005]


def test_replacing_direction_stops_resets_only_that_geometry(geometry_client):
    client, route_id, forward, backward = geometry_client
    client.put(f"/api/routes/{route_id}/geometry/forward", json={"geometry": line(*forward), "expected_version": 0})
    client.put(f"/api/routes/{route_id}/geometry/backward", json={"geometry": line(*backward), "expected_version": 0})
    items = client.get(f"/api/routes/{route_id}/network").json()["forward"]
    client.put(f"/api/routes/{route_id}/stops/forward", json={"items": [
        {"stop_id": row["stop"]["id"], "sequence": index + 1, "distance_from_prev_km": row["distance_from_prev_km"]}
        for index, row in enumerate(reversed(items))
    ]})
    geometries = client.get(f"/api/routes/{route_id}/network").json()["geometries"]
    assert geometries["forward"] is None
    assert geometries["backward"] is not None
```

- [ ] **Step 2: Запустить тесты и подтвердить рассинхронизацию**

Run: `python -m pytest tests/test_route_geometry_api.py -q`

Expected: новые тесты FAIL — старая линия остаётся без нового якоря.

- [ ] **Step 3: Реализовать замену координаты якоря во всех связанных геометриях**

```python
def synchronize_stop_anchor(con, stop_id, old_coordinate, new_coordinate, username, timestamp):
    if old_coordinate[0] is None or old_coordinate[1] is None or new_coordinate[0] is None or new_coordinate[1] is None:
        route_directions = con.execute("SELECT route_id,direction FROM route_stops WHERE stop_id=?", (stop_id,)).fetchall()
        for item in route_directions:
            con.execute("DELETE FROM route_geometries WHERE route_id=? AND direction=?", (item["route_id"], item["direction"]))
        return
    for item in con.execute("SELECT route_id,direction FROM route_stops WHERE stop_id=?", (stop_id,)).fetchall():
        current = get_geometry(con, item["route_id"], item["direction"])
        if current is None:
            continue
        coordinates = current["geometry"]["coordinates"]
        matches = [index for index, point in enumerate(coordinates) if _same_coordinate(point, old_coordinate)]
        if len(matches) != 1:
            con.execute("DELETE FROM route_geometries WHERE route_id=? AND direction=?", (item["route_id"], item["direction"]))
            continue
        coordinates[matches[0]] = [float(new_coordinate[0]), float(new_coordinate[1])]
        save_geometry(con, item["route_id"], item["direction"], current["geometry"], "manual", current["version"], username, timestamp)
```

В `stop_update` вызвать функцию после `UPDATE stops`, но до аудита и commit. Если координата удалена или старый якорь нельзя однозначно сопоставить, безопасно сбросить затронутую геометрию и записать это в аудит изменения остановки.

- [ ] **Step 4: Сбрасывать геометрию изменяемого направления при замене трассы**

В `route_stops_replace` получить прежнюю запись геометрии до `DELETE route_stops`, удалить только `(route_id, direction)` и включить старые `source/version` в существующий аудит замены трассы.

```python
old_geometry = get_geometry(con, route_id, direction)
con.execute("DELETE FROM route_geometries WHERE route_id=? AND direction=?", (route_id, direction))
db.audit(
    con, user["username"], "замена трассы маршрута", "routes", route_id,
    old={direction: old, "geometry": {
        "source": old_geometry["source"], "version": old_geometry["version"],
        "coordinates": len(old_geometry["geometry"]["coordinates"]),
    } if old_geometry else None},
    new={direction: saved, "geometry": None},
)
```

- [ ] **Step 5: Запустить тесты мутаций**

Run: `python -m pytest tests/test_route_geometry_api.py tests/test_route_network_api.py tests/test_route_stop_mutations.py -q`

Expected: все тесты PASS.

- [ ] **Step 6: Зафиксировать согласованность якорей**

```bash
git add app/route_geometry.py app/api_route_network.py tests/test_route_geometry_api.py
git commit -m "fix(routes): keep geometry aligned with stops"
```

### Task 6: Чистая JavaScript-модель редактора

**Files:**
- Create: `static/route-geometry-editor.js`
- Create: `tests/js/route_geometry_editor_behavior.js`
- Create: `tests/test_route_geometry_frontend.py`

- [ ] **Step 1: Написать Node-сценарии операций над черновиком**

```javascript
// tests/js/route_geometry_editor_behavior.js
"use strict";
const assert = require("node:assert/strict");
const editor = require("../../static/route-geometry-editor.js");

const anchors = [[35.9, 56.8], [35.92, 56.82]];
const geometry = { type: "LineString", coordinates: [anchors[0], [35.91, 56.81], anchors[1]] };

function draftLifecycle() {
  const draft = editor.createDraft(geometry, anchors, 4);
  assert.equal(draft.version, 4);
  assert.equal(editor.isDirty(draft), false);
  editor.moveVertex(draft, 1, [35.911, 56.811]);
  assert.equal(editor.isDirty(draft), true);
  editor.cancelDraft(draft);
  assert.equal(editor.isDirty(draft), false);
}

function anchorsCannotMoveOrDelete() {
  const draft = editor.createDraft(geometry, anchors, 1);
  assert.throws(() => editor.moveVertex(draft, 0, [1, 1]), /остановки/);
  assert.throws(() => editor.deleteVertex(draft, 2), /остановки/);
}

function insertAndDeleteControlPoint() {
  const draft = editor.createDraft(geometry, anchors, 1);
  const index = editor.insertVertex(draft, 0, [35.905, 56.805]);
  assert.equal(index, 1);
  assert.deepEqual(draft.coordinates[1], [35.905, 56.805]);
  editor.deleteVertex(draft, 1);
  assert.equal(draft.coordinates.length, 3);
}

function sparseMarkersKeepAnchorsAndUserPoints() {
  const coordinates = Array.from({ length: 500 }, (_, index) => [35.9 + index / 10000, 56.8]);
  const draft = editor.createDraft({ type: "LineString", coordinates }, [coordinates[0], coordinates[499]], 1);
  draft.userVertexIndexes.add(123);
  const indexes = editor.visibleVertexIndexes(draft, 120);
  assert.ok(indexes.length <= 122);
  assert.ok(indexes.includes(0) && indexes.includes(123) && indexes.includes(499));
}

({ draft_lifecycle: draftLifecycle, anchors_locked: anchorsCannotMoveOrDelete,
   insert_delete: insertAndDeleteControlPoint, sparse: sparseMarkersKeepAnchorsAndUserPoints })[process.argv[2]]();
```

- [ ] **Step 2: Добавить pytest-обёртку и подтвердить отсутствие модуля**

```python
# tests/test_route_geometry_frontend.py
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "js" / "route_geometry_editor_behavior.js"


@pytest.mark.parametrize("scenario", ["draft_lifecycle", "anchors_locked", "insert_delete", "sparse"])
def test_route_geometry_editor_behavior(scenario):
    node = shutil.which("node")
    assert node, "Node.js is required for executable route geometry tests"
    result = subprocess.run([node, str(HARNESS), scenario], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
```

Run: `python -m pytest tests/test_route_geometry_frontend.py -q`

Expected: FAIL с `Cannot find module '../../static/route-geometry-editor.js'`.

- [ ] **Step 3: Реализовать UMD-модуль чистой модели**

Экспортировать точные функции: `createDraft`, `isDirty`, `cancelDraft`, `moveVertex`, `insertVertex`, `deleteVertex`, `visibleVertexIndexes`, `nearestSegmentIndex`, `geometryPayload`, `sourceLabel`.

```javascript
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.RouteGeometryEditor = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const clone = coordinates => coordinates.map(point => [+point[0], +point[1]]);
  const key = point => `${(+point[0]).toFixed(6)}:${(+point[1]).toFixed(6)}`;
  function createDraft(geometry, anchors, version) {
    const coordinates = clone(geometry.coordinates);
    const anchorKeys = new Set(anchors.map(key));
    const anchorIndexes = new Set(coordinates.map((point, index) => anchorKeys.has(key(point)) ? index : -1).filter(index => index >= 0));
    return { original: clone(coordinates), coordinates, anchorIndexes, userVertexIndexes: new Set(), selectedIndex: null, version: +version || 0 };
  }
  function isDirty(draft) { return JSON.stringify(draft.coordinates) !== JSON.stringify(draft.original); }
  function cancelDraft(draft) { draft.coordinates = clone(draft.original); draft.userVertexIndexes.clear(); draft.selectedIndex = null; }
  function moveVertex(draft, index, point) {
    if (draft.anchorIndexes.has(index)) throw new Error("Нельзя перемещать вершину остановки");
    draft.coordinates[index] = [+point[0], +point[1]];
  }
  function insertVertex(draft, segmentIndex, point) {
    const index = segmentIndex + 1;
    draft.coordinates.splice(index, 0, [+point[0], +point[1]]);
    draft.anchorIndexes = new Set([...draft.anchorIndexes].map(value => value >= index ? value + 1 : value));
    draft.userVertexIndexes = new Set([...draft.userVertexIndexes].map(value => value >= index ? value + 1 : value).concat(index));
    draft.selectedIndex = index;
    return index;
  }
  function deleteVertex(draft, index) {
    if (draft.anchorIndexes.has(index)) throw new Error("Нельзя удалить вершину остановки");
    draft.coordinates.splice(index, 1);
    draft.anchorIndexes = new Set([...draft.anchorIndexes].map(value => value > index ? value - 1 : value));
    draft.userVertexIndexes = new Set([...draft.userVertexIndexes].filter(value => value !== index).map(value => value > index ? value - 1 : value));
    draft.selectedIndex = null;
  }
  function visibleVertexIndexes(draft, limit) {
    const required = new Set([0, draft.coordinates.length - 1, ...draft.anchorIndexes, ...draft.userVertexIndexes]);
    const candidates = draft.coordinates.map((point, index) => index).filter(index => !required.has(index));
    const slots = Math.max(0, limit - required.size);
    if (slots > 0 && candidates.length) {
      const step = Math.max(1, Math.ceil(candidates.length / slots));
      for (let offset = 0; offset < candidates.length && required.size < limit; offset += step) required.add(candidates[offset]);
    }
    return [...required].sort((left, right) => left - right);
  }
  function nearestSegmentIndex(point, projected) {
    if (!Array.isArray(projected) || projected.length < 2) throw new Error("Линия должна содержать минимум две точки");
    function distanceSquared(a, b, p) {
      const dx = b[0] - a[0], dy = b[1] - a[1];
      const length = dx * dx + dy * dy;
      const ratio = length ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length)) : 0;
      const x = a[0] + ratio * dx, y = a[1] + ratio * dy;
      return (p[0] - x) ** 2 + (p[1] - y) ** 2;
    }
    let nearest = 0, best = distanceSquared(projected[0], projected[1], point);
    for (let index = 1; index < projected.length - 1; index += 1) {
      const distance = distanceSquared(projected[index], projected[index + 1], point);
      if (distance < best) { nearest = index; best = distance; }
    }
    return nearest;
  }
  function geometryPayload(draft) { return { type: "LineString", coordinates: clone(draft.coordinates) }; }
  function sourceLabel(source) { return source === "manual" ? "Ручная геометрия" : source === "osrm" ? "Геометрия OSRM" : "Линия по остановкам"; }
  return { createDraft, isDirty, cancelDraft, moveVertex, insertVertex, deleteVertex, visibleVertexIndexes, nearestSegmentIndex, geometryPayload, sourceLabel };
});
```

`visibleVertexIndexes` равномерно выбирает обычные вершины до лимита 120 и всегда добавляет anchors/endpoints/user indexes. `nearestSegmentIndex` принимает точку щелчка и массив уже спроецированных экранных точек, вычисляет квадрат расстояния до каждого отрезка и возвращает индекс ближайшего отрезка.

- [ ] **Step 4: Расширить Node-сценарии проверкой ближайшего сегмента и сдвига индексов**

```javascript
assert.equal(editor.nearestSegmentIndex([7, 1], [[0, 0], [5, 0], [10, 0]]), 1);
assert.deepEqual(editor.geometryPayload(draft), { type: "LineString", coordinates: draft.coordinates });
```

- [ ] **Step 5: Запустить исполняемые тесты**

Run: `python -m pytest tests/test_route_geometry_frontend.py -q`

Expected: все сценарии PASS.

- [ ] **Step 6: Зафиксировать модель редактора**

```bash
git add static/route-geometry-editor.js tests/js/route_geometry_editor_behavior.js tests/test_route_geometry_frontend.py
git commit -m "feat(routes): add route geometry draft model"
```

### Task 7: Состояние UI, команды сохранения и защита черновика

**Files:**
- Modify: `static/route-card.js:21-104,606-615,766-788,1018-1033`
- Modify: `static/app.js:20-31`
- Modify: `tests/js/route_geometry_editor_behavior.js`
- Modify: `tests/test_route_geometry_frontend.py`

- [ ] **Step 1: Написать падающие сценарии команд UI**

Добавить VM-harness, который загружает сначала `route-geometry-editor.js`, затем `route-card.js`, и сценарии:

```javascript
async function saveKeepsDraftAfterConflict() {
  const calls = [];
  const { context, state } = routeCardHarness(async (url, options) => {
    calls.push({ url, options });
    const error = new Error("Линия уже изменена другим пользователем");
    error.status = 409;
    throw error;
  });
  context.routeCardStartGeometryEdit();
  state.geometryEditor.draft.coordinates.splice(1, 0, [35.905, 56.805]);
  await context.routeCardSaveGeometry();
  assert.equal(state.geometryEditor.active, true);
  assert.equal(state.geometryEditor.draft.coordinates.length, 3);
  assert.match(state.geometryEditor.error, /другим пользователем/);
}

function cancelAndNavigationGuard() {
  const { context, state, confirms } = routeCardHarness(async () => ({}));
  context.routeCardStartGeometryEdit();
  state.geometryEditor.draft.coordinates.splice(1, 0, [35.905, 56.805]);
  confirms.push(false);
  context.routeCardDirection("backward");
  assert.equal(state.direction, "forward");
  confirms.push(true);
  context.routeCardDirection("backward");
  assert.equal(state.direction, "backward");
}
```

Также проверить: read-only роль не видит кнопку, reset требует confirm, OSRM требует отказа от dirty-черновика, OSRM apply отправляет `expected_geometry_version`, а успешный save закрывает режим только после ответа.

- [ ] **Step 2: Запустить сценарии и подтвердить отсутствие UI-команд**

Run: `python -m pytest tests/test_route_geometry_frontend.py -q`

Expected: новые сценарии FAIL с отсутствующими функциями.

- [ ] **Step 3: Добавить состояние редактора и выбор отображаемой геометрии**

```javascript
geometryEditor: { active: false, draft: null, error: "", saving: false },

function routeCardStoredGeometry(state, direction = state.direction) {
  return state.network.geometries && state.network.geometries[direction];
}

function routeCardBaseGeometry(state) {
  const stored = routeCardStoredGeometry(state);
  if (stored) return stored.geometry;
  return { type: "LineString", coordinates: routeCardDraft(state).filter(row => row.stop.longitude != null && row.stop.latitude != null).map(row => [+row.stop.longitude, +row.stop.latitude]) };
}

function routeCardDisplayedGeometry(state) {
  if (state.geometryEditor.active) return RouteGeometryEditor.geometryPayload(state.geometryEditor.draft);
  return routeCardBaseGeometry(state);
}
```

Удалить временное поле `state.geometry`; `routeCardGeometryPoints` использует только `routeCardDisplayedGeometry(state)`.

- [ ] **Step 4: Реализовать команды start/cancel/save/reset и общий guard**

```javascript
function routeCardGeometryDirty(state = window._routeCard) {
  return !!(state && state.geometryEditor.active && RouteGeometryEditor.isDirty(state.geometryEditor.draft));
}
function routeCardConfirmGeometryDiscard(state = window._routeCard) {
  return !routeCardGeometryDirty(state) || confirm("Отменить несохранённые изменения линии трассы?");
}
function routeCardStartGeometryEdit() {
  const state = window._routeCard;
  if (!routeCardCanEdit() || state.osrmPreview) return;
  const stored = routeCardStoredGeometry(state);
  const geometry = stored ? stored.geometry : routeCardDisplayedGeometry(state);
  const anchors = routeCardDraft(state).map(row => [+row.stop.longitude, +row.stop.latitude]);
  state.geometryEditor = { active: true, draft: RouteGeometryEditor.createDraft(geometry, anchors, stored ? stored.version : 0), error: "", saving: false };
  renderRouteCard(state);
}
async function routeCardSaveGeometry() {
  const state = window._routeCard, editor = state.geometryEditor;
  if (!editor.active || editor.saving) return;
  editor.saving = true; editor.error = ""; renderRouteCard(state);
  try {
    await api(`/api/routes/${state.routeId}/geometry/${state.direction}`, { method: "PUT", body: { geometry: RouteGeometryEditor.geometryPayload(editor.draft), expected_version: editor.draft.version } });
    editor.active = false; editor.draft = null;
    toast("Линия трассы сохранена");
    await routeCardReload();
  } catch (error) {
    editor.error = error.message;
    editor.saving = false;
    renderRouteCard(state);
  }
}
function routeCardCancelGeometryEdit() {
  const state = window._routeCard;
  if (!routeCardConfirmGeometryDiscard(state)) return;
  state.geometryEditor = { active: false, draft: null, error: "", saving: false };
  renderRouteCard(state);
}
```

Reset вызывает `DELETE` с `{expected_version}`, после confirm. `routeCardTab`, `routeCardDirection`, кнопка «Назад» и `beforeunload` вызывают единый guard. При отказе текущие tab/direction/hash не меняются.

- [ ] **Step 5: Сохранить HTTP-статус в ошибке общего API-клиента**

```javascript
if (!r.ok) {
  const error = new Error(data.detail || ("Ошибка " + r.status));
  error.status = r.status;
  error.data = data;
  throw error;
}
```

Для `409` рядом с кнопками показывать текст: «Линия уже изменена другим пользователем. Ваш черновик сохранён на экране; обновите данные после визуальной проверки».

- [ ] **Step 6: Обновить OSRM-команды**

`routeCardOsrmPreview` сначала вызывает discard guard, не меняет сохранённую линию и сохраняет только preview. `routeCardOsrmApply` требует отдельный confirm и отправляет:

```javascript
body: {
  preview_token: state.osrmPreview.preview_token,
  expected_geometry_version: state.osrmPreview.geometry_version,
}
```

- [ ] **Step 7: Запустить UI behavior-тесты**

Run: `python -m pytest tests/test_route_geometry_frontend.py tests/test_route_card_depot_behavior.py tests/test_route_document_download_behavior.py -q`

Expected: все тесты PASS.

- [ ] **Step 8: Зафиксировать команды интерфейса**

```bash
git add static/app.js static/route-card.js tests/js/route_geometry_editor_behavior.js tests/test_route_geometry_frontend.py
git commit -m "feat(routes): add route geometry editing workflow"
```

### Task 8: Leaflet-контрольные точки и интерактивное редактирование

**Files:**
- Modify: `static/route-card.js:606-760`
- Modify: `static/styles.css:197-221,259-286`
- Modify: `tests/js/route_geometry_editor_behavior.js`
- Modify: `tests/test_route_geometry_frontend.py`
- Modify: `tests/test_route_card_map_frontend.py`

- [ ] **Step 1: Добавить падающие проверки раздельных Leaflet-слоёв**

Проверить в VM с минимальными двойниками Leaflet:

```javascript
assert.equal(stopMarker.options.draggable, false);
assert.equal(controlMarker.options.draggable, true);
controlMarker.fire("dragend", { target: controlMarker });
assert.deepEqual(state.geometryEditor.draft.coordinates[controlIndex], [35.911, 56.811]);
polyline.fire("click", { latlng: { lng: 35.905, lat: 56.805 } });
assert.ok(state.geometryEditor.draft.userVertexIndexes.size === 1);
```

Статические проверки должны найти классы `.route-geometry-control`, `.is-selected`, обработчики `click`, `dragend`, `keydown` и лимит `120`.

- [ ] **Step 2: Запустить frontend-тесты и подтвердить падение**

Run: `python -m pytest tests/test_route_geometry_frontend.py tests/test_route_card_map_frontend.py -q`

Expected: FAIL из-за отсутствия контрольных Leaflet-маркеров.

- [ ] **Step 3: Разделить слои линии, остановок и контрольных точек**

В `routeCardBindMap` создать:

```javascript
const currentLine = window.L.polyline(line, { color: "#2563eb", weight: 6 }).addTo(map);
const previewPoints = state.osrmPreview
  ? state.osrmPreview.geometry.coordinates.map(point => [+point[1], +point[0]])
  : [];
const previewLine = previewPoints.length > 1
  ? window.L.polyline(previewPoints, { color: "#dc2626", weight: 5, dashArray: "10 8" }).addTo(map)
  : null;
const controlsLayer = window.L.layerGroup().addTo(map);
```

В обычном режиме остановки сохраняют существующее перетаскивание координат. В ручном режиме остановки получают `draggable: false`; дополнительным вершинам из `visibleVertexIndexes(draft, 120)` назначаются маленькие `L.divIcon`, `draggable: true`, `keyboard: true` и `aria-label="Контрольная точка N"`.

- [ ] **Step 4: Реализовать добавление ближайшей вершины щелчком по линии**

```javascript
currentLine.on("click", event => {
  if (!state.geometryEditor.active) return;
  const projected = state.geometryEditor.draft.coordinates.map(point => {
    const screen = map.latLngToLayerPoint([point[1], point[0]]);
    return [screen.x, screen.y];
  });
  const click = map.latLngToLayerPoint(event.latlng);
  const segmentIndex = RouteGeometryEditor.nearestSegmentIndex([click.x, click.y], projected);
  RouteGeometryEditor.insertVertex(state.geometryEditor.draft, segmentIndex, [event.latlng.lng, event.latlng.lat]);
  renderRouteCard(state);
});
```

Drag вызывает `moveVertex`, не делает HTTP-запрос и перерисовывает линию/контроли. Щелчок по контрольной точке задаёт `selectedIndex`; кнопка «Удалить точку» и `Delete` вызывают `deleteVertex`, но обязательные anchor indexes не получают кнопку удаления.

- [ ] **Step 5: Добавить панель режима и доступность**

```html
<div class="route-geometry-editor" role="region" aria-label="Редактор линии трассы">
  <span>Щёлкните по линии, чтобы добавить точку; перетащите точку для корректировки.</span>
  <button class="btn sec" aria-label="Удалить выбранную контрольную точку">Удалить точку</button>
  <button class="btn sec">Отменить изменения</button>
  <button class="btn">Сохранить линию</button>
</div>
```

Кнопка «Корректировать линию» доступна только `routeCardCanEdit()` и при координатах у всех остановок. Рядом всегда отображается метка `sourceLabel`; при OSM tile error ручные команды блокируются, черновик сохраняется в состоянии и показывается русское предупреждение.

- [ ] **Step 6: Добавить стили контрольных точек**

```css
.route-geometry-control { background: transparent; border: 0; }
.route-geometry-control span { display: block; width: 12px; height: 12px; border: 2px solid #fff; border-radius: 50%; background: #f97316; box-shadow: 0 1px 5px #0f172a88; }
.route-geometry-control.is-selected span { width: 18px; height: 18px; margin: -3px; background: #dc2626; outline: 3px solid #fef08a; }
.route-geometry-editor { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 8px 0; padding: 10px; border: 1px solid #93c5fd; border-radius: 9px; background: #eff6ff; }
.route-geometry-editor [role="alert"] { flex-basis: 100%; color: var(--err); }
.route-osrm-preview { stroke-dasharray: 10 8; }
```

- [ ] **Step 7: Запустить frontend и существующие map-тесты**

Run: `python -m pytest tests/test_route_geometry_frontend.py tests/test_route_card_map_frontend.py -q`

Expected: все тесты PASS; старое перетаскивание координат остановки работает только вне ручного режима.

- [ ] **Step 8: Зафиксировать Leaflet-редактор**

```bash
git add static/route-card.js static/styles.css tests/js/route_geometry_editor_behavior.js tests/test_route_geometry_frontend.py tests/test_route_card_map_frontend.py
git commit -m "feat(routes): edit route line with Leaflet controls"
```

### Task 9: Подключение ресурсов, cache-key и регрессионная проверка

**Files:**
- Modify: `static/index.html:35-42`
- Modify: `tests/test_route_geometry_frontend.py`
- Modify: `tests/test_route_card_frontend.py`
- Modify: `tests/test_route_card_map_frontend.py`
- Modify: `tests/test_route_documents_frontend.py`
- Modify: `tests/test_route_shift_frontend.py`

- [ ] **Step 1: Написать падающий тест порядка загрузки ресурсов**

```python
def test_geometry_editor_loads_before_route_card_with_matching_cache_keys():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    editor = "/static/route-geometry-editor.js?v=1.0"
    card = "/static/route-card.js?v=4.1"
    assert editor in index and card in index
    assert index.index(editor) < index.index(card)
    assert "styles.css?v=3.3&amp;route=4.1" in index
```

- [ ] **Step 2: Запустить статические frontend-тесты и подтвердить старые cache-key**

Run: `python -m pytest tests/test_route_geometry_frontend.py tests/test_route_card_frontend.py tests/test_route_card_map_frontend.py tests/test_route_documents_frontend.py tests/test_route_shift_frontend.py -q`

Expected: FAIL на старых `route-card.js?v=4.0` и styles key.

- [ ] **Step 3: Подключить новый ресурс и согласованно обновить ожидания**

```html
<link rel="stylesheet" href="/static/styles.css?v=3.3&amp;route=4.1">
<script src="/static/route-geometry-editor.js?v=1.0"></script>
<script src="/static/route-card.js?v=4.1"></script>
```

Во всех существующих тестах заменить только ожидаемый cache-key `4.0` на `4.1`; не ослаблять остальные утверждения.

- [ ] **Step 4: Запустить весь frontend-набор**

Run: `python -m pytest tests/test_route_geometry_frontend.py tests/test_route_card_frontend.py tests/test_route_card_map_frontend.py tests/test_route_documents_frontend.py tests/test_route_shift_frontend.py tests/test_route_card_depot_behavior.py tests/test_route_document_download_behavior.py -q`

Expected: все тесты PASS.

- [ ] **Step 5: Зафиксировать подключение ресурсов**

```bash
git add static/index.html tests/test_route_geometry_frontend.py tests/test_route_card_frontend.py tests/test_route_card_map_frontend.py tests/test_route_documents_frontend.py tests/test_route_shift_frontend.py
git commit -m "chore(routes): load route geometry editor assets"
```

### Task 10: Полная проверка и ручная приёмка

**Files:**
- Modify only if a verification failure exposes a defect in files listed above.

- [ ] **Step 1: Запустить специализированные тесты**

Run: `python -m pytest tests/test_route_geometry_schema.py tests/test_route_geometry_api.py tests/test_route_osrm.py tests/test_route_geometry_frontend.py tests/test_route_card_map_frontend.py -q`

Expected: все специализированные тесты PASS.

- [ ] **Step 2: Запустить полный набор проекта**

Run: `python -m pytest -q`

Expected: все тесты PASS; допускаются только уже известные предупреждения, без новых failures/errors.

- [ ] **Step 3: Проверить запуск локальной программы**

Run: `start.bat`

Expected: сервер остаётся запущенным на `http://127.0.0.1:8001/`, консоль не содержит traceback, `GET /` возвращает HTTP 200.

- [ ] **Step 4: Выполнить приёмочную проверку в браузере**

1. Открыть карточку тестового маршрута и вкладку «Схема трассы».
2. Для прямого направления нажать «Корректировать линию».
3. Щёлкнуть по неправильному отрезку, перетащить появившуюся точку на нужную улицу и убедиться, что остановки не двигаются.
4. Нажать «Сохранить линию», обновить страницу и проверить метку «Ручная геометрия» и сохранённую форму.
5. Повторить для обратного направления и убедиться, что прямая линия не изменилась.
6. Создать несохранённую правку, попробовать сменить направление и проверить варианты «остаться» и «отбросить».
7. Запустить OSRM, отменить preview и проверить сохранность ручной линии.
8. Снова запустить OSRM, применить только после подтверждения и проверить метку «Геометрия OSRM».
9. Отключить сеть или заблокировать OSM tiles и проверить предупреждение и блокировку неточного сохранения.
10. Войти ролью только для чтения и проверить отсутствие команд редактирования.

- [ ] **Step 5: Проверить рабочее дерево**

Run: `git status --short`

Expected: пустой вывод. Если проверка выявила и исправила дефект, сначала повторить затронутые тесты и полный `pytest -q`, затем создать отдельный коммит `fix(routes): correct route geometry acceptance issue`.
