# Модуль «Билеты и выручка» — дизайн

**Дата:** 2026-08-08
**Статус:** согласован
**Автор:** совместный брейншторм

## Цель

Добавить в АТП-систему коммерческий контур пассажирских перевозок: учёт
выручки за смену, справочник видов билетов с версионными тарифами, кассовую
сверку и отчётность по маршрутам/водителям/периоду. Это первый из нескольких
пассажирских модулей; льготники/субсидии, потранзакционные продажи и
GPS-мониторинг — отдельными итерациями позже.

## Ключевые решения (согласованы)

1. **Уровень ввода:** лист выручки **за смену** — итоговая сумма плюс количество
   билетов по видам.
2. **Привязка:** лист выручки привязан **к путевому листу**; водитель, автобус,
   маршрут и дата подставляются из ПЛ автоматически.
3. **Льготники/субсидии:** сейчас **не включаем** (YAGNI, отдельная итерация).
4. **Тарифы:** хранятся **версиями с датами действия** по образцу нормативов
   Приказа 424; расчёт берёт цену, действующую на дату смены.

## Данные

Четыре новые таблицы добавляются в `SCHEMA` (`app/db.py`) как
`CREATE TABLE IF NOT EXISTS`. Инкрементальные изменения существующих таблиц не
требуются. Индексы создаются в `init_db` рядом с существующими.

### `fare_types` — справочник видов билетов

| Поле | Тип | Назначение |
|---|---|---|
| `id` | INTEGER PK | |
| `code` | TEXT UNIQUE | краткий код (`single`, `month`, `child`, `baggage`) |
| `name` | TEXT | наименование (разовый, проездной месячный, детский, багаж) |
| `unit` | TEXT | единица (`поездка` / `месяц` / `место`) |
| `active` | INTEGER DEFAULT 1 | скрытие без удаления |

### `fare_tariffs` — версии цен по виду билета

| Поле | Тип | Назначение |
|---|---|---|
| `id` | INTEGER PK | |
| `fare_type_id` | INTEGER NOT NULL → fare_types | |
| `valid_from` | TEXT NOT NULL | дата начала действия (YYYY-MM-DD) |
| `valid_to` | TEXT | дата конца действия (NULL = бессрочно) |
| `price` | REAL NOT NULL | цена, руб. |
| `active` | INTEGER DEFAULT 1 | |
| `comment` | TEXT | основание/примечание |

Хелпер `active_tariff(con, fare_type_id, on_date)` возвращает запись с
`active=1 AND valid_from<=on_date AND (valid_to IS NULL OR valid_to>=on_date)`,
последнюю по `valid_from`. Отсутствие тарифа на дату — доменная ошибка при
расчёте строки.

### `revenue_sheets` — лист выручки за смену

| Поле | Тип | Назначение |
|---|---|---|
| `id` | INTEGER PK | |
| `number` | INTEGER UNIQUE NOT NULL | сквозной номер листа |
| `waybill_id` | INTEGER NOT NULL → waybills | смена |
| `date` | TEXT NOT NULL | дата (из ПЛ) |
| `driver_id` | INTEGER | из ПЛ |
| `bus_id` | INTEGER | из ПЛ |
| `route_id` | INTEGER | из ПЛ |
| `conductor_id` | INTEGER | необяз., ссылка на drivers (член бригады) |
| `expected_amount` | REAL DEFAULT 0 | расчёт из строк (Σ amount) |
| `submitted_amount` | REAL DEFAULT 0 | сдано наличными |
| `difference` | REAL DEFAULT 0 | submitted − expected (излишек «+», недостача «−») |
| `status` | TEXT DEFAULT 'черновик' | черновик / сдан / сверен / аннулирован |
| `created_by` | TEXT | |
| `created_at` | TEXT | |
| `submitted_at` | TEXT | |
| `reconciled_by` | TEXT | |
| `reconciled_at` | TEXT | |
| `cancel_reason` | TEXT | |
| `comment` | TEXT | |

Ограничение: один активный (не аннулированный) лист на путевой лист.
Реализуется проверкой в сервисе + частичным уникальным индексом
`WHERE status<>'аннулирован'`.

### `revenue_lines` — строки по видам билетов

| Поле | Тип | Назначение |
|---|---|---|
| `id` | INTEGER PK | |
| `sheet_id` | INTEGER NOT NULL → revenue_sheets ON DELETE CASCADE | |
| `fare_type_id` | INTEGER NOT NULL → fare_types | |
| `tickets_count` | INTEGER NOT NULL DEFAULT 0 | ≥ 0 |
| `unit_price` | REAL NOT NULL | снимок тарифа на дату смены |
| `amount` | REAL NOT NULL | tickets_count × unit_price |

Уникальность `(sheet_id, fare_type_id)` — один вид билета не дублируется в листе.

## Компоненты

Каждый модуль имеет одну зону ответственности, общается через явные функции и
тестируется изолированно.

### `app/revenue_service.py` — бизнес-логика (без FastAPI)

- `list_fare_types(con)`, `upsert_fare_type(...)`, `set_fare_type_active(...)`.
- `list_tariffs(con, fare_type_id=None)`, `add_tariff(...)`,
  `active_tariff(con, fare_type_id, on_date)`.
- `create_sheet_from_waybill(con, waybill_id, *, conductor_id, created_by)` —
  читает ПЛ, подставляет реквизиты, выделяет сквозной `number`, статус
  «черновик»; ошибка если ПЛ не найден или уже есть активный лист.
- `set_sheet_lines(con, sheet_id, lines, *, user)` — принимает
  `[(fare_type_id, tickets_count)]`, берёт `unit_price` через `active_tariff`
  на дату листа, считает `amount` и пересчитывает `expected_amount`. Валидация:
  неотрицательные счётчики, известные виды билетов, наличие тарифа на дату.
- `submit_sheet(con, sheet_id, submitted_amount, *, user)` — фиксирует
  «сдано», считает `difference`, статус «сдан».
- `reconcile_sheet(con, sheet_id, *, user)` — статус «сверен».
- `cancel_sheet(con, sheet_id, reason, *, user)` — статус «аннулирован»,
  сохраняет аудит.
- Статусная машина: черновик→сдан→сверен; аннулирование из любого не-финального
  статуса. Правки строк — только в «черновике».

Все мутации пишут `db.audit(...)` и не делают `commit` сами (коммитит слой API),
по образцу существующих сервисов.

### `app/api_revenue.py` — REST (`/api/revenue/...`)

- `GET/POST/PUT /api/revenue/fare-types` — справочник (право `revenue`).
- `GET/POST /api/revenue/tariffs` — версии тарифов (право `revenue`).
- `POST /api/revenue/sheets` — создать из ПЛ; `GET /api/revenue/sheets`
  (фильтры: период, маршрут, статус); `GET /api/revenue/sheets/{id}`.
- `PUT /api/revenue/sheets/{id}/lines` — строки (только «черновик»).
- `POST /api/revenue/sheets/{id}/submit`, `.../reconcile`, `.../cancel`.
- Ошибки валидации → 400, ненайденное → 404, нет прав → 403 (через
  `require_write(user, "revenue")`). Аудит и `con.commit()` на успехе.

### `app/revenue_reports.py` — Excel-отчёты

Функции строят `openpyxl`-книгу теми же стилевыми хелперами, что и расписания
(`apply_sheet_setup`, `write_title_band`, `write_table_header`,
`_xlsx_download_response`): выручка по маршрутам, по водителям, по видам билетов
за период; строки листов с расчётом и сверкой. Эндпоинт
`GET /api/revenue/report.xlsx?from&to&group_by`.

### Фронтенд

- `static/revenue.js` — регистрирует `VIEWS.revenue`; вкладка «Выручка» с
  подвкладками **Тарифы**, **Листы выручки**, **Отчёты**. Диалог создания листа
  из ПЛ, ввод строк с живым пересчётом суммы, кнопки «Сдать»/«Сверить».
- Пункт меню и подключение скрипта в `static/index.html` (с версией ассета);
  показ вкладки по праву `revenue`.
- Стили — в `static/styles.css` (адаптив + печать), по образцу существующих.

### Схема, миграции, демо

- 4 `CREATE TABLE` в `SCHEMA`; индексы (`idx_revenue_sheets_waybill`,
  частичный уникальный на активный лист, `idx_fare_tariffs_type_from`) в
  `init_db`.
- Сид (`app/seed.py`): виды билетов (разовый/детский/багаж/проездной),
  тариф(ы) с `valid_from`, и демо-листы выручки для вчерашних путевых листов.
- Матрица прав (`app/auth.py`): добавить `"revenue"` в `WRITE_ACCESS` для
  `бухгалтер` и `диспетчер`.

## Поток данных

```
Путевой лист (смена)
  → POST /revenue/sheets {waybill_id, conductor_id}
      реквизиты из ПЛ, номер, статус «черновик»
  → PUT  /revenue/sheets/{id}/lines [{fare_type_id, tickets_count}]
      unit_price = active_tariff(on = sheet.date); amount; expected_amount
  → POST /revenue/sheets/{id}/submit {submitted_amount}
      difference = submitted − expected; статус «сдан»
  → POST /revenue/sheets/{id}/reconcile   → статус «сверен»
Отчёты: агрегация по маршрутам/водителям/периоду + выгрузка Excel.
```

## Обработка ошибок

- Несуществующий ПЛ / лист / вид билета → 404.
- Отрицательное количество или сумма, дубль вида в листе, правка не-черновика,
  отсутствие тарифа на дату, повторный активный лист на ПЛ → 400 с понятным
  русским сообщением.
- Нет права `revenue` → 403.
- Все доменные ошибки — подкласс `ValueError`, транслируются слоем API.

## Тестирование (TDD, до реализации)

- **Юнит (`revenue_service`)**: тариф-на-дату (границы `valid_from/valid_to`),
  расчёт строк и `expected_amount`, сверка и `difference`, статусные переходы,
  запрет правок вне «черновика», запрет второго активного листа.
- **API**: контракт эндпоинтов, коды 400/403/404, аудит, права ролей
  (бухгалтер/диспетчер/чужая роль).
- **Отчёты**: структура Excel-книги, агрегаты, `content-disposition`.
- **Фронтенд**: наличие вкладки/подвкладок и хуков, версии ассетов;
  исполняемый JS-тест живого пересчёта суммы.
- Полный `python -m pytest -q` зелёный; новые тесты добавляются красными.

## Границы (YAGNI)

Не включаем: льготные категории и субсидии, потранзакционные продажи,
интеграцию с валидаторами/фаребоксами, выгрузку выручки в 1С, печатную форму
листа (может быть добавлена позднее по запросу). Кондуктор — необязательная
ссылка на существующих `drivers`, отдельная сущность персонала не вводится.
