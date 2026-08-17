# Устройство АТП-системы

## Общая схема

Один процесс: uvicorn поднимает FastAPI-приложение `app.main:app`, оно отдаёт
статику из `static/` и REST-API из `app/api_*.py`, данные лежат в одном файле
SQLite `atp.db`. Ни сборки фронтенда, ни отдельного бэкенд-сервиса,
ни очередей, ни Docker — намеренно упрощённый стек прототипа.

Браузер (ванильный JS) → `fetch('/api/...')` с токеном в заголовке
`Authorization: Bearer <token>` → роутер FastAPI → сервисный модуль → SQLite.

## Модули бэкенда

| Файл | За что отвечает |
|---|---|
| `app/main.py` | точка входа, `include_router` всех роутеров, монтирование `/static` |
| `app/db.py` | схема (`SCHEMA`), миграции, `connect()`, `init_db()`, аудит, нормативы по умолчанию |
| `app/auth.py` | пароли (pbkdf2), сессии-токены, `ROLES`, матрица `WRITE_ACCESS`, `require_write` |
| `app/norms.py` | проверки Приказа № 424: смена, время управления, перерывы, отдых, переработки |
| `app/seed.py` | демо-данные (`run.py --demo`) |
| `app/api_refs.py` | справочники, производственный календарь, отсутствия, нормативы, импорт Excel |
| `app/api_planning.py` | расписания, выходы, графики водителей, наряды |
| `app/api_waybills.py` | медосмотры, техконтроль, путевые листы формы № 6, печать |
| `app/api_time.py` | табель, выгрузка в 1С, топливо, отчёты, главная панель |
| `app/api_summary.py` | сводное расписание |
| `app/api_route_*.py`, `app/route_*.py` | маршрутная сеть v2: остановки, периоды, расписание, смены, геометрия (Leaflet/OSRM), паспорт маршрута в Word, импорт ЕРМ |
| `app/api_repair*.py`, `app/repair_*.py` | ремонт и ТО: заявки, заказ-наряды, склад, исполнители, контроль, повторы, уведомления, отчёты, печать |
| `app/api_vehicle_*.py`, `app/vehicle_card_reports.py` | карточка автобуса, ДТП/повреждения, фото, Excel-досье |
| `app/api_revenue.py`, `app/revenue_*.py` | выручка, билеты, тарифы, сдача выручки |
| `app/api_dispatch.py`, `app/dispatch_*.py` | диспетчерский контроль: доска выпуска, телеметрия, отклонения, отчёт |
| `app/xl.py`, `app/*_reports.py`, `app/route_document_xlsx.py`, `app/route_passport_docx.py` | выгрузки Excel/Word |

Правило разделения: FastAPI-слой (`api_*.py`) только парсит запрос, проверяет
права и переводит исключения в HTTP-коды; вся логика — в `*_service.py`,
`norms.py`, `route_*.py`, которые принимают открытое соединение `con` и
тестируются без HTTP.

## Типовой роутер

```python
# -*- coding: utf-8 -*-
"""API модуля X."""
from fastapi import APIRouter, Body, Depends, HTTPException
from . import db
from .auth import current_user, require_write
from . import x_service as xs

router = APIRouter(prefix="/api/x")

@router.get("/board")
def board(date: str, user=Depends(current_user)):
    con = db.connect()
    try:
        return xs.build_board(con, date)
    finally:
        con.close()

@router.post("/items")
def create(payload: dict = Body(...), user=Depends(current_user)):
    require_write(user, "x")          # раздел из WRITE_ACCESS в app/auth.py
    con = db.connect()
    try:
        ...
        con.commit()
        return {"ok": True}
    finally:
        con.close()
```

Соединение открывается на запрос и обязательно закрывается в `finally`.
Сообщения ошибок — по-русски, они показываются пользователю как есть.
Значимые изменения пишутся в аудит (`audit_log`, функции в `app/db.py`).

## База данных

- Путь берётся из переменной окружения `ATP_DB`, иначе `atp.db` рядом с
  проектом. Тесты подменяют `db.DB_PATH` на файл в `tmp_path`.
- `connect()` включает `PRAGMA foreign_keys=ON` и `row_factory = sqlite3.Row`;
  помощники `db.rows(cur)` и `db.one(cur)` превращают строки в словари.
- Основные группы таблиц: `users/sessions/settings/audit_log`;
  `drivers/buses/routes/route_trips/stops`; `calendar/absences/absence_types`;
  `roster_*` (графики), `orders/order_lines` (наряды), `waybills` (путевые);
  `norms` (версии нормативов); `repair_*`; `revenue_*`; `dispatch_*`;
  `timesheet_*`, `time_codes`.

### Как менять схему

Только через миграции, применяемые при каждом старте в `init_db()`:

- новая таблица — добавить `CREATE TABLE IF NOT EXISTS` в `SCHEMA` (`app/db.py`)
  или в соответствующий `migrate_*` модуля;
- новая колонка — добавить кортеж `("таблица", "колонка", "TEXT")` в список
  `MIGRATIONS` в `app/db.py`; функция `migrate()` сама проверит
  `PRAGMA table_info` и выполнит `ALTER TABLE ... ADD COLUMN`;
- индексы — `CREATE ... INDEX IF NOT EXISTS` в `init_db()`.

Повторный запуск на уже мигрированной базе обязан проходить без ошибок:
у пользователей боевая база с данными, пересоздавать её нельзя.

## Фронтенд

`static/index.html` — единственная страница, подключающая скрипты с
версионированными адресами (`app.js?v=3.5`). Роутинг внутри `app.js`:

- `NAV` — список пунктов меню `[ключ, "Название"]`;
- `VIEWS.<ключ> = async function () { ... }` — рендер раздела в `#content`;
- крупные разделы вынесены в отдельные файлы (`dispatch.js`, `revenue.js`,
  `route-card.js`, `vehicle-card.js`, `route-geometry-editor.js`), которые
  дописывают свои `VIEWS.*` и подключены в `index.html`.

Чтобы добавить раздел: пункт в `NAV` → `VIEWS.<ключ>` (в `app.js` или новом
файле) → подключить файл в `index.html` → поднять `?v=` у изменённых скриптов
→ стили в `styles.css` (там же блок `@media print` для печатных форм).

Из внешних библиотек только Leaflet в `static/vendor/` (карты маршрутов).
Графики и виджеты пишем сами инлайновым SVG.

## Тесты

- `python -m pytest -q` — весь набор; часть тестов интерфейса запускает
  сценарии `tests/js/*.js` через `node`, поэтому Node.js должен быть в PATH.
- API: временная база в `tmp_path`, `TestClient(app)`, логин `admin/admin`,
  токен в заголовки — пример `_client()` в
  `tests/test_route_schedule_document.py`.
- Выгрузки проверяются по структуре: имена листов, заголовки, области печати,
  наличие формул — см. `tests/test_route_schedule_document.py`,
  `tests/test_order_excel_export.py`.
- Интерфейс проверяется двумя способами: наличие ключевых строк в исходнике
  (`tests/test_dispatch_frontend.py`) и исполняемые поведенческие сценарии
  (`tests/js/*_behavior.js` + питоновская обёртка).
- Приёмочные сценарии целых модулей: `tests/test_repair_acceptance.py`,
  `tests/test_vehicle_card_acceptance.py`.

## Роли и права

`ROLES` и `WRITE_ACCESS` в `app/auth.py`. Роль `админ` может всё. Разделы
для `require_write`: `orders`, `waybills`, `roster`, `summary`, `revenue`,
`dispatch`, `routes`, `trips`, `drivers`, `absences`, `buses`, `tech`,
`medical`, `fuel`, `timesheet`, `export1c`, `repairs`, `repair_orders`,
`repair_work`, `repair_inspections`, `repair_stock`.

Демо-пользователи: `admin/admin`, остальные (`dispatcher`, `ekspl`, `kadry`,
`buh`, `mech`, `med`, `fuel`, `dir`) с паролем `12345`.

## Документация проекта

- `README.md` — обзор для пользователя и запуск;
- `docs/Ремонт_и_ТО_инструкция.md`, `docs/route-passport-export.md`;
- `docs/Руководство_пользователя_ATP_Servis_V2.docx` собирается скриптом
  `docs/build_user_guide.py`;
- `docs/superpowers/specs/` и `docs/superpowers/plans/` — история дизайнов и
  планов по каждой фиче; лучший источник контекста «почему сделано так».
