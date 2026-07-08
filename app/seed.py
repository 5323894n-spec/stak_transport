# -*- coding: utf-8 -*-
"""Демо-данные небольшого АТП: python -m app.seed"""
import datetime, random
from . import db
from .auth import hash_password
from .api_planning import trips_generate, roster_generate, order_generate, order_status, outputs_summary
from .api_waybills import medical_create, tech_create, waybills_from_order, waybill_close

random.seed(42)
ADMIN = {"id": 1, "username": "admin", "full_name": "Администратор", "role": "админ"}

FIO = ["Иванов Сергей Петрович","Смирнов Алексей Николаевич","Кузнецов Дмитрий Владимирович",
"Попов Андрей Михайлович","Васильев Олег Игоревич","Петров Николай Александрович",
"Соколов Виктор Сергеевич","Михайлов Павел Андреевич","Новиков Игорь Валентинович",
"Федоров Максим Олегович","Морозов Владимир Ильич","Волков Анатолий Петрович",
"Алексеев Роман Дмитриевич","Лебедев Константин Юрьевич","Семенов Евгений Борисович",
"Егоров Артем Викторович","Павлов Денис Григорьевич","Козлов Станислав Иванович",
"Степанов Юрий Фёдорович","Николаев Вадим Романович","Орлов Геннадий Львович",
"Андреев Валерий Николаевич","Макаров Илья Сергеевич","Никитин Антон Павлович",
"Захаров Пётр Алексеевич","Зайцев Владислав Игоревич","Соловьев Тимур Маратович",
"Борисов Григорий Ефимович","Яковлев Аркадий Семёнович","Григорьев Леонид Андреевич"]

MEDIC = "Малолитвинова Ольга Анатольевна"
MECHANICS = ["Сироткин Фёдор Михайлович", "Горбачева Татьяна Алексеевна"]

def run():
    db.init_db()
    con = db.connect()
    if con.execute("SELECT COUNT(*) c FROM drivers").fetchone()["c"] > 0:
        print("Демо-данные уже загружены — пропуск.")
        con.close()
        return
    today = datetime.date.today()
    # пользователи
    users = [("dispatcher","Дежурный диспетчер","диспетчер"),("ekspl","Отдел эксплуатации","эксплуатация"),
             ("kadry","Отдел кадров","кадры"),("buh","Бухгалтерия","бухгалтер"),("mech","Механик ОТК","механик"),
             ("med","Медицинский работник","медик"),("fuel","Топливная группа","топливо"),
             ("dir","Руководитель","руководитель")]
    if not con.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        con.execute("INSERT INTO users(username,password_hash,full_name,role) VALUES('admin',?,?,?)",
                    (hash_password("admin"), "Администратор системы", "админ"))
    for u, fn, role in users:
        con.execute("INSERT OR IGNORE INTO users(username,password_hash,full_name,role) VALUES(?,?,?,?)",
                    (u, hash_password("12345"), fn, role))
    # маршруты
    routes = [
        ("1", "Вокзал — мкр. Южный", "городское", 12.4, 45, 12, 3),
        ("2", "Речной порт — Соминка", "городское", 10.8, 40, 15, 2),
        ("5", "Автовокзал — пос. Литвинки", "городское", 14.2, 50, 15, 2),
        ("7", "пл. Гагарина — Мамулино", "городское", 9.6, 35, 12, 2),
        ("104", "Тверь — Васильевский Мох", "пригородное", 28.5, 55, 40, 2),
        ("310", "Тверь — Торжок", "межмуниципальное", 61.0, 90, 120, 1),
    ]
    rid_map = {}
    for num, name, ct, km, tt, iv, outs in routes:
        cur = con.execute(
            "INSERT INTO routes(number,name,comm_type,start_point,end_point,length_km,trip_time_min,"
            "interval_min,outputs_count,bus_types,work_days) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (num, name, ct, name.split(" — ")[0], name.split(" — ")[-1], km, tt, iv, outs,
             "большой" if ct == "городское" else "средний", "ежедневно"))
        rid_map[num] = cur.lastrowid
    con.commit()
    # расписания: будни / суббота / воскресенье
    for num, name, ct, km, tt, iv, outs in routes:
        for dt, o in (("будни", outs), ("суббота", max(1, outs - 1)), ("воскресенье", max(1, outs - 2))):
            trips_generate({"route_id": rid_map[num], "day_type": dt, "outputs": o,
                            "first_dep": "05:40", "last_dep": "21:40" if ct == "городское" else "19:00",
                            "trip_time": tt, "interval": max(iv, (tt + 6) * 2 // max(1, o)),
                            "distance": km}, ADMIN)
    # автобусы
    models = [("ЛиАЗ", "529265", "большой", 110, 42.0), ("ЛиАЗ", "429260", "большой", 75, 36.0),
              ("ПАЗ", "Vector Next", "средний", 53, 24.0), ("МАЗ", "203", "большой", 105, 40.0)]
    letters = "АВЕКМНОРСТУХ"
    bus_ids = []
    for i in range(20):
        brand, model, cls, cap, rate = models[i % 4]
        plate = f"{random.choice(letters)}{random.randint(100,999)}{random.choice(letters)}{random.choice(letters)} 69"
        status = "в ремонте" if i == 18 else ("резерв" if i == 19 else "исправен")
        cur = con.execute(
            "INSERT INTO buses(garage_number,plate,vin,brand,model,year,bus_class,capacity,fuel_type,fuel_rate,"
            "winter_coeff,column_name,next_to_date,osago_expires,diag_card_expires,status,odometer,tank_capacity,fuel_balance) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(101 + i), plate, f"XTZ{random.randint(10**12, 10**13-1)}", brand, model,
             random.randint(2016, 2024), cls, cap, "ДТ", rate, 1.12, "Колонна №1" if i < 10 else "Колонна №2",
             (today + datetime.timedelta(days=random.randint(10, 120))).isoformat(),
             (today + datetime.timedelta(days=random.randint(20, 360))).isoformat(),
             (today + datetime.timedelta(days=random.randint(15, 180))).isoformat(),
             status, random.randint(120000, 420000), 200, random.randint(60, 180)))
        bus_ids.append(cur.lastrowid)
    # водители
    route_nums = ["1", "1", "1", "2", "2", "5", "5", "7", "7", "104", "104", "310"]
    driver_ids = []
    for i, fio in enumerate(FIO):
        rnum = route_nums[i % len(route_nums)]
        bus_id = bus_ids[i % 18] if i < 27 else None
        birth = datetime.date(1965 + i % 30, 1 + i % 12, 1 + i % 27)
        cur = con.execute(
            "INSERT INTO drivers(tab_number,fio,birth_date,division,license_categories,license_number,"
            "license_issued,license_expires,snils,inn,phone,employment_type,default_schedule,assigned_route_id,"
            "assigned_bus_id,driver_class,hired_date,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"03{i+1:02d}", fio, birth.isoformat(), "Колонна №1" if i < 15 else "Колонна №2", "D",
             f"69 {random.randint(10,39)} {random.randint(100000,999999)}",
             datetime.date(2015 + i % 8, 1 + i % 12, 5).isoformat(),
             (today + datetime.timedelta(days=random.randint(-5, 2000) if i != 28 else 12)).isoformat(),
             f"{random.randint(100,999)}-{random.randint(100,999)}-{random.randint(100,999)} {random.randint(10,99)}",
             f"69{random.randint(10**9, 10**10-1)}", f"+7 (910) {random.randint(100,999)}-{random.randint(10,99)}-{random.randint(10,99)}",
             "Основное место работы", "2/2" if i % 5 else "5/2", rid_map[rnum], bus_id, str(1 + i % 3),
             datetime.date(2012 + i % 12, 1 + i % 12, 10).isoformat(), "работает"))
        driver_ids.append(cur.lastrowid)
    con.commit()
    # график на текущий и следующий месяц
    m0 = today.replace(day=1)
    import calendar as pycal
    m_end = m0.replace(day=pycal.monthrange(m0.year, m0.month)[1])
    nxt = (m_end + datetime.timedelta(days=1))
    nxt_end = nxt.replace(day=pycal.monthrange(nxt.year, nxt.month)[1])
    roster_generate({"date_from": m0.isoformat(), "date_to": nxt_end.isoformat()}, ADMIN)
    # отсутствия: отпуск и больничный
    from .api_refs import absence_create
    absence_create({"driver_id": driver_ids[5], "type_code": "ОТ",
                    "date_from": (today - datetime.timedelta(days=3)).isoformat(),
                    "date_to": (today + datetime.timedelta(days=18)).isoformat(),
                    "comment": "ежегодный отпуск по графику"}, ADMIN)
    absence_create({"driver_id": driver_ids[11], "type_code": "Б",
                    "date_from": (today - datetime.timedelta(days=1)).isoformat(),
                    "date_to": (today + datetime.timedelta(days=6)).isoformat(),
                    "comment": "лист нетрудоспособности"}, ADMIN)
    # наряды и путевые листы за вчера (закрытые) и сегодня (открытые)
    con.commit()
    for offs in (-1, 0):
        d = (today + datetime.timedelta(days=offs)).isoformat()
        try:
            res = order_generate({"date": d}, ADMIN)
        except Exception as e:
            print("наряд", d, "—", e)
            continue
        con2 = db.connect()
        lines = db.rows(con2.execute(
            "SELECT l.* FROM order_lines l JOIN orders o ON o.id=l.order_id WHERE o.date=?", (d,)))
        con2.close()
        # медосмотры и техконтроль для всех назначенных
        seen_d, seen_b = set(), set()
        for l in lines:
            if l["driver_id"] and l["driver_id"] not in seen_d:
                medical_create({"driver_id": l["driver_id"], "date": d, "time": "05:05",
                                "type": "предрейсовый", "result": "допущен", "medic_name": MEDIC,
                                "org": "ООО «ТМС 77», лиц. № ЛО-69-01"}, ADMIN)
                seen_d.add(l["driver_id"])
            if l["bus_id"] and l["bus_id"] not in seen_b:
                tech_create({"bus_id": l["bus_id"], "date": d, "time": "05:10",
                             "result": "выпуск разрешен", "mechanic_name": MECHANICS[l["bus_id"] % 2]}, ADMIN)
                seen_b.add(l["bus_id"])
        try:
            order_status(db.one(db.connect().execute("SELECT id FROM orders WHERE date=?", (d,)))["id"],
                         {"status": "утвержден", "force_comment": "демо-данные"}, ADMIN)
        except Exception as e:
            print("утверждение", d, "—", e)
        res = waybills_from_order(d, ADMIN)
        print(d, "— путевых листов:", len(res["created"]), "заблокировано:", len(res["blocked"]))
        if offs == -1:
            con3 = db.connect()
            wbs = db.rows(con3.execute("SELECT * FROM waybills WHERE date=? AND status='оформлен'", (d,)))
            con3.close()
            for w in wbs:
                line = [x for x in lines if x["id"] == w["order_line_id"]][0]
                dist = (line["distance_km"] or 100) * random.uniform(0.97, 1.03)
                fuel_fact_ratio = random.uniform(0.9, 1.18)
                bus = db.one(db.connect().execute("SELECT * FROM buses WHERE id=?", (w["bus_id"],)))
                plan = dist * (bus["fuel_rate"] or 35) / 100.0
                given = round(plan * random.uniform(0.8, 1.2))
                fuel_end = max(5, round((w["fuel_start"] or 100) + given - plan * fuel_fact_ratio, 1))
                waybill_close(w["id"], {"odo_end": round((w["odo_start"] or 0) + dist, 1),
                                        "fuel_given": given, "fuel_end": fuel_end,
                                        "depart_fact": w["depart_plan"], "return_fact": w["return_plan"]}, ADMIN)
    con.close()
    print("Демо-данные загружены: маршрутов 6, автобусов 20, водителей 30.")
    print("Пользователи: admin/admin, dispatcher/12345, ekspl/12345, kadry/12345, buh/12345, mech/12345, med/12345, fuel/12345, dir/12345")

if __name__ == "__main__":
    run()
