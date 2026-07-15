# -*- coding: utf-8 -*-
"""База данных SQLite: схема, подключение, аудит."""
import sqlite3, json, os, datetime

from .repair_schema import migrate_repairs
from .route_schema import migrate_route_network

DB_PATH = os.environ.get("ATP_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "atp.db"))

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.create_function(
        "lower", 1, lambda value: value.casefold() if isinstance(value, str) else value)
    con.execute("PRAGMA foreign_keys=ON")
    return con

def rows(cur):
    return [dict(r) for r in cur.fetchall()]

def one(cur):
    r = cur.fetchone()
    return dict(r) if r else None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
  full_name TEXT, role TEXT NOT NULL, active INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, user_id INTEGER, created TEXT, last_seen TEXT);

CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, ts TEXT, username TEXT, action TEXT, object_type TEXT,
  object_id TEXT, old_value TEXT, new_value TEXT, ip TEXT, comment TEXT);

CREATE TABLE IF NOT EXISTS drivers(
  id INTEGER PRIMARY KEY, tab_number TEXT UNIQUE, fio TEXT NOT NULL, birth_date TEXT,
  division TEXT, position TEXT DEFAULT 'Водитель автобуса',
  license_categories TEXT, license_number TEXT, license_issued TEXT, license_expires TEXT,
  snils TEXT, inn TEXT, phone TEXT, address TEXT, employment_type TEXT DEFAULT 'Основное место работы',
  default_schedule TEXT DEFAULT '2/2', assigned_route_id INTEGER, assigned_bus_id INTEGER,
  driver_class TEXT DEFAULT '1', bus_type_permits TEXT DEFAULT 'большой,средний,малый',
  hired_date TEXT, fired_date TEXT, status TEXT DEFAULT 'работает',
  med_info TEXT, training_info TEXT, restrictions TEXT, notes TEXT);

CREATE TABLE IF NOT EXISTS buses(
  id INTEGER PRIMARY KEY, garage_number TEXT UNIQUE, plate TEXT, vin TEXT,
  brand TEXT, model TEXT, year INTEGER, bus_class TEXT DEFAULT 'большой', capacity INTEGER,
  fuel_type TEXT DEFAULT 'ДТ', fuel_rate REAL DEFAULT 35.0, winter_coeff REAL DEFAULT 1.1,
  column_name TEXT, assigned_driver_id INTEGER, next_to_date TEXT, osago_expires TEXT,
  diag_card_expires TEXT, status TEXT DEFAULT 'исправен', odometer REAL DEFAULT 0,
  tank_capacity REAL DEFAULT 200, fuel_balance REAL DEFAULT 0, equipment TEXT DEFAULT 'тахограф,ГЛОНАСС');

CREATE TABLE IF NOT EXISTS routes(
  id INTEGER PRIMARY KEY, number TEXT NOT NULL, name TEXT, comm_type TEXT DEFAULT 'городское',
  transport_type TEXT DEFAULT 'Регулярные перевозки пассажиров и багажа',
  start_point TEXT, end_point TEXT, stops TEXT, stops_back TEXT, length_km REAL, length_back_km REAL,
  trip_time_min INTEGER, trip_time_back_min INTEGER,
  interval_min INTEGER, outputs_count INTEGER DEFAULT 1, bus_types TEXT DEFAULT 'большой',
  season TEXT DEFAULT 'круглогодично', work_days TEXT DEFAULT 'ежедневно',
  notes TEXT, version INTEGER DEFAULT 1, active INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS route_trips(
  id INTEGER PRIMARY KEY, route_id INTEGER NOT NULL, day_type TEXT DEFAULT 'будни',
  output_number INTEGER DEFAULT 1, shift_number INTEGER DEFAULT 1, trip_number INTEGER,
  direction TEXT DEFAULT 'прямое', dep_time TEXT, arr_time TEXT, distance_km REAL,
  break_after_min INTEGER DEFAULT 0, break_type TEXT DEFAULT '',
  FOREIGN KEY(route_id) REFERENCES routes(id) ON DELETE CASCADE);

CREATE TABLE IF NOT EXISTS calendar(
  date TEXT PRIMARY KEY, day_type TEXT NOT NULL, comment TEXT);

CREATE TABLE IF NOT EXISTS absence_types(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, code_1c TEXT, paid INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS absences(
  id INTEGER PRIMARY KEY, driver_id INTEGER NOT NULL, type_code TEXT NOT NULL,
  date_from TEXT NOT NULL, date_to TEXT NOT NULL, status TEXT DEFAULT 'утверждено', comment TEXT,
  FOREIGN KEY(driver_id) REFERENCES drivers(id) ON DELETE CASCADE);

CREATE TABLE IF NOT EXISTS norms(
  id INTEGER PRIMARY KEY, name TEXT, valid_from TEXT, valid_to TEXT,
  params TEXT NOT NULL, doc_ref TEXT, comment TEXT, active INTEGER DEFAULT 1);

CREATE TABLE IF NOT EXISTS roster(
  id INTEGER PRIMARY KEY, driver_id INTEGER NOT NULL, date TEXT NOT NULL,
  status TEXT DEFAULT 'работа', route_id INTEGER, output_number INTEGER, shift_number INTEGER,
  start_time TEXT, end_time TEXT, hours REAL DEFAULT 0, night_hours REAL DEFAULT 0,
  break_min INTEGER DEFAULT 0,
  comment TEXT, approved INTEGER DEFAULT 0,
  UNIQUE(driver_id, date),
  FOREIGN KEY(driver_id) REFERENCES drivers(id) ON DELETE CASCADE);

CREATE TABLE IF NOT EXISTS roster_assignments(
  id INTEGER PRIMARY KEY, driver_id INTEGER NOT NULL, date TEXT NOT NULL,
  route_id INTEGER NOT NULL, day_type TEXT DEFAULT 'будни',
  output_number INTEGER DEFAULT 1, shift_number INTEGER DEFAULT 1,
  trip_from INTEGER, trip_to INTEGER,
  start_time TEXT, end_time TEXT, hours REAL DEFAULT 0, night_hours REAL DEFAULT 0,
  break_min INTEGER DEFAULT 0, distance_km REAL DEFAULT 0, trips_count INTEGER DEFAULT 0,
  comment TEXT,
  FOREIGN KEY(driver_id) REFERENCES drivers(id) ON DELETE CASCADE,
  FOREIGN KEY(route_id) REFERENCES routes(id) ON DELETE CASCADE);

CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY, date TEXT UNIQUE NOT NULL, status TEXT DEFAULT 'черновик',
  approved_by TEXT, approved_at TEXT, comment TEXT);

CREATE TABLE IF NOT EXISTS order_lines(
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, route_id INTEGER, output_number INTEGER,
  shift_number INTEGER, driver_id INTEGER, bus_id INTEGER,
  report_time TEXT, depart_depot TEXT, start_line TEXT, end_line TEXT, return_depot TEXT,
  shift_hours REAL, trips_count INTEGER, distance_km REAL, planned_fuel REAL,
  dispatcher_note TEXT, status TEXT DEFAULT 'план',
  FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE);

CREATE TABLE IF NOT EXISTS summary_schedules(
  id INTEGER PRIMARY KEY,
  schedule_date TEXT,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  day_type TEXT,
  status TEXT DEFAULT 'сформировано',
  created_by TEXT,
  created_at TEXT,
  updated_at TEXT,
  routes_count INTEGER DEFAULT 0,
  trips_count INTEGER DEFAULT 0,
  runs_count INTEGER DEFAULT 0,
  vehicles_count INTEGER DEFAULT 0,
  drivers_count INTEGER DEFAULT 0,
  errors_count INTEGER DEFAULT 0,
  warnings_count INTEGER DEFAULT 0,
  filters_json TEXT DEFAULT '{}',
  comment TEXT,
  excel_file_path TEXT);

CREATE TABLE IF NOT EXISTS summary_schedule_lines(
  id INTEGER PRIMARY KEY,
  summary_schedule_id INTEGER NOT NULL,
  service_date TEXT NOT NULL,
  route_id INTEGER,
  route_number TEXT,
  route_name TEXT,
  direction TEXT,
  run_number INTEGER,
  shift_number INTEGER,
  trip_number INTEGER,
  vehicle_id INTEGER,
  vehicle_number TEXT,
  garage_number TEXT,
  driver_id INTEGER,
  driver_tab_number TEXT,
  driver_name TEXT,
  departure_time TEXT,
  arrival_time TEXT,
  trip_duration INTEGER DEFAULT 0,
  depot_departure_time TEXT,
  depot_return_time TEXT,
  distance_km REAL DEFAULT 0,
  day_type TEXT,
  schedule_version TEXT,
  status TEXT DEFAULT 'действует',
  error_flag INTEGER DEFAULT 0,
  comment TEXT,
  FOREIGN KEY(summary_schedule_id) REFERENCES summary_schedules(id) ON DELETE CASCADE);

CREATE TABLE IF NOT EXISTS summary_schedule_errors(
  id INTEGER PRIMARY KEY,
  summary_schedule_id INTEGER NOT NULL,
  line_id INTEGER,
  level TEXT NOT NULL,
  route_number TEXT,
  run_number INTEGER,
  trip_number INTEGER,
  object_type TEXT,
  object_label TEXT,
  message TEXT NOT NULL,
  recommendation TEXT,
  created_at TEXT,
  FOREIGN KEY(summary_schedule_id) REFERENCES summary_schedules(id) ON DELETE CASCADE,
  FOREIGN KEY(line_id) REFERENCES summary_schedule_lines(id) ON DELETE CASCADE);

CREATE INDEX IF NOT EXISTS idx_summary_lines_summary ON summary_schedule_lines(summary_schedule_id);
CREATE INDEX IF NOT EXISTS idx_summary_lines_date_route ON summary_schedule_lines(service_date, route_id, run_number, shift_number);
CREATE INDEX IF NOT EXISTS idx_summary_errors_summary ON summary_schedule_errors(summary_schedule_id);
CREATE TABLE IF NOT EXISTS medical_checks(
  id INTEGER PRIMARY KEY, driver_id INTEGER NOT NULL, date TEXT, time TEXT,
  type TEXT DEFAULT 'предрейсовый', result TEXT DEFAULT 'допущен',
  medic_name TEXT, org TEXT, comment TEXT);

CREATE TABLE IF NOT EXISTS tech_checks(
  id INTEGER PRIMARY KEY, bus_id INTEGER NOT NULL, date TEXT, time TEXT,
  result TEXT DEFAULT 'выпуск разрешен', odometer REAL, notes TEXT,
  mechanic_name TEXT, comment TEXT);

CREATE TABLE IF NOT EXISTS waybills(
  id INTEGER PRIMARY KEY, number INTEGER UNIQUE NOT NULL, order_line_id INTEGER,
  date TEXT NOT NULL, valid_to TEXT, driver_id INTEGER, bus_id INTEGER, route_id INTEGER,
  output_number INTEGER, depart_plan TEXT, return_plan TEXT, depart_fact TEXT, return_fact TEXT,
  odo_start REAL, odo_end REAL, fuel_start REAL, fuel_given REAL DEFAULT 0,
  fuel_plan REAL, fuel_fact REAL, fuel_end REAL, distance REAL,
  medical_check_id INTEGER, tech_check_id INTEGER,
  status TEXT DEFAULT 'оформлен', cancel_reason TEXT, printed_at TEXT, print_count INTEGER DEFAULT 0,
  created_by TEXT, created_at TEXT, closed_at TEXT, comment TEXT);

CREATE TABLE IF NOT EXISTS fuel_records(
  id INTEGER PRIMARY KEY, date TEXT, bus_id INTEGER, driver_id INTEGER, route_id INTEGER,
  waybill_id INTEGER, kind TEXT DEFAULT 'рейс',
  distance REAL DEFAULT 0, rate REAL DEFAULT 0, plan_litres REAL DEFAULT 0, fact_litres REAL DEFAULT 0,
  given_litres REAL DEFAULT 0, start_balance REAL, end_balance REAL,
  saving REAL DEFAULT 0, overrun REAL DEFAULT 0, comment TEXT, responsible TEXT);

CREATE TABLE IF NOT EXISTS time_codes(
  code TEXT PRIMARY KEY, name TEXT, code_1c TEXT);

CREATE TABLE IF NOT EXISTS exports_1c(
  id INTEGER PRIMARY KEY, created_by TEXT, created_at TEXT, period_from TEXT, period_to TEXT,
  fmt TEXT, employees INTEGER, version INTEGER DEFAULT 1, status TEXT DEFAULT 'сформирована',
  file_name TEXT, protocol TEXT);

CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY, ts TEXT, level TEXT DEFAULT 'info', category TEXT,
  message TEXT, seen INTEGER DEFAULT 0);
"""

DEFAULT_NORMS = {
    "max_shift_hours": 10,
    "max_shift_hours_summed": 12,
    "max_driving_day": 9,
    "max_driving_day_ext": 10,
    "max_driving_ext_per_week": 2,
    "max_driving_week": 56,
    "max_driving_2weeks": 90,
    "driving_before_break_h": 4.5,
    "break_min_minutes": 45,
    "intershift_rest_factor": 2.0,
    "min_intershift_rest_summed_h": 12,
    "weekly_rest_h": 42,
    "night_start": "22:00",
    "night_end": "06:00",
    "week_norm_hours": 40,
    "overtime_year_max_h": 120,
    "overtime_2days_max_h": 4,
    "max_consecutive_workdays": 6,
    "prep_final_minutes": 18,
    "med_check_minutes": 5,
    "summed_accounting": 1,
    "accounting_period_months": 1,
}

DEFAULT_TIME_CODES = [
    ("Я", "Явка (дневные часы)", "Я"),
    ("Н", "Ночные часы", "Н"),
    ("С", "Сверхурочные часы", "С"),
    ("РВ", "Работа в выходной/праздничный день", "РВ"),
    ("ОТ", "Ежегодный оплачиваемый отпуск", "ОТ"),
    ("ОД", "Дополнительный отпуск", "ОД"),
    ("Б", "Больничный (временная нетрудоспособность)", "Б"),
    ("У", "Учебный отпуск", "У"),
    ("К", "Командировка", "К"),
    ("ДО", "Отпуск без сохранения з/п", "ДО"),
    ("ПК", "Обучение / повышение квалификации", "ПК"),
    ("МК", "Медкомиссия", "МК"),
    ("ОГ", "Отгул", "НВ"),
    ("РП", "Простой", "РП"),
    ("НБ", "Отстранение", "НБ"),
    ("РЗ", "Резерв", "Я"),
    ("В", "Выходной день", "В"),
]

ABSENCE_TYPES = [
    ("ОТ", "Ежегодный отпуск", "ОТ"), ("ОД", "Дополнительный отпуск", "ОД"),
    ("Б", "Больничный", "Б"), ("ДО", "Отпуск без сохранения з/п", "ДО"),
    ("У", "Учебный отпуск", "У"), ("К", "Командировка", "К"),
    ("МК", "Медкомиссия", "МК"), ("ПК", "Обучение", "ПК"),
    ("ОГ", "Отгул", "НВ"), ("РП", "Простой", "РП"),
    ("НБ", "Отстранение", "НБ"), ("РЗ", "Резерв", "Я"), ("ИН", "Иная причина", "НН"),
]

ORG_DEFAULTS = {
    "org_name": "ООО «ВЕРХНЕВОЛЖСКОЕ АВТОТРАНСПОРТНОЕ ПРЕДПРИЯТИЕ»",
    "org_address": "170007, Тверская обл., г. Тверь, ул. Шишкова, дом 92",
    "org_phone": "8 (4822) 78-98-08",
    "org_ogrn": "1196952012685",
    "org_okpo": "41292569",
    "org_okud": "0345006",
    "org_inn": "",
    "org_owner": "",
    "org_control_place": "",
    "org_license_reg": "",
    "org_license_series": "",
    "org_license_number": "",
    "waybill_series": "АК2",
    "waybill_prefix": "",
    "waybill_issue_mode": "strict_med_tech",
    "session_timeout_min": "120",
    "repair_repeat_days": "30",
}

MIGRATIONS = [
    ("routes", "stops_back", "TEXT"),
    ("routes", "length_back_km", "REAL"),
    ("routes", "trip_time_back_min", "INTEGER"),
    ("roster", "break_min", "INTEGER DEFAULT 0"),
    ("summary_schedules", "filters_json", "TEXT DEFAULT '{}'"),
    ("summary_schedule_lines", "service_date", "TEXT"),
    ("summary_schedule_lines", "vehicle_id", "INTEGER"),
    ("summary_schedule_lines", "driver_id", "INTEGER"),
    ("notifications", "source_key", "TEXT"),
]

def migrate(con):
    for table, col, decl in MIGRATIONS:
        cols = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

def init_db():
    con = connect()
    con.executescript(SCHEMA)
    migrate(con)
    migrate_route_network(con)
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_source_key ON notifications(source_key)")
    migrate_repairs(con)
    if not con.execute("SELECT 1 FROM norms").fetchone():
        con.execute(
            "INSERT INTO norms(name, valid_from, valid_to, params, doc_ref, comment) VALUES(?,?,?,?,?,?)",
            ("Режим труда и отдыха водителей (базовый)", "2021-01-01", "2099-12-31",
             json.dumps(DEFAULT_NORMS, ensure_ascii=False),
             "Приказ Минтранса РФ от 16.10.2020 № 424; ТК РФ гл. 15-19",
             "Базовая версия. Проверьте актуальность значений перед эксплуатацией."))
    for code, name, c1 in DEFAULT_TIME_CODES:
        con.execute("INSERT OR IGNORE INTO time_codes(code,name,code_1c) VALUES(?,?,?)", (code, name, c1))
    for code, name, c1 in ABSENCE_TYPES:
        con.execute("INSERT OR IGNORE INTO absence_types(code,name,code_1c) VALUES(?,?,?)", (code, name, c1))
    for k, v in ORG_DEFAULTS.items():
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    con.commit()
    con.close()

def get_settings(con):
    return {r["key"]: r["value"] for r in con.execute("SELECT key,value FROM settings")}

def get_active_norms(con, on_date=None):
    d = on_date or datetime.date.today().isoformat()
    r = con.execute(
        "SELECT * FROM norms WHERE active=1 AND valid_from<=? AND valid_to>=? ORDER BY valid_from DESC LIMIT 1",
        (d, d)).fetchone()
    if not r:
        r = con.execute("SELECT * FROM norms ORDER BY id DESC LIMIT 1").fetchone()
    p = dict(DEFAULT_NORMS)
    if r:
        try: p.update(json.loads(r["params"]))
        except Exception: pass
    return p

def audit(con, username, action, obj_type, obj_id, old=None, new=None, ip="", comment=""):
    con.execute(
        "INSERT INTO audit_log(ts,username,action,object_type,object_id,old_value,new_value,ip,comment) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (datetime.datetime.now().isoformat(timespec="seconds"), username, action, obj_type,
         str(obj_id) if obj_id is not None else "",
         json.dumps(old, ensure_ascii=False, default=str) if old is not None else None,
         json.dumps(new, ensure_ascii=False, default=str) if new is not None else None, ip, comment))

def notify(con, level, category, message):
    con.execute("INSERT INTO notifications(ts,level,category,message) VALUES(?,?,?,?)",
                (datetime.datetime.now().isoformat(timespec="seconds"), level, category, message))

