# -*- coding: utf-8 -*-
"""Сквозная приёмка полного цикла ремонта и ТО."""
from tests.test_repair_requests_api import make_client


def test_complete_repair_acceptance_flow(tmp_path):
    client, bus_id = make_client(tmp_path)
    refs = client.get("/api/repairs/references").json()

    request = client.post("/api/repairs/requests", json={
        "vehicle_id": bus_id,
        "odometer": 12000,
        "fault_description": "Люфт передней подвески",
        "criticality": "высокая",
        "request_source": "диспетчер",
    })
    assert request.status_code == 201, request.text

    order = client.post("/api/repairs/orders", json={
        "request_id": request.json()["id"],
        "vehicle_id": bus_id,
        "repair_type_id": refs["repair_types"][0]["id"],
        "repair_post_id": refs["repair_posts"][0]["id"],
        "diagnosis": "Износ шарниров подвески",
        "planned_start": "2026-07-16T09:00:00",
        "planned_end": "2026-07-16T18:00:00",
    })
    assert order.status_code == 201, order.text
    order_id = order.json()["id"]

    import app.db as db
    con = db.connect()
    try:
        second_worker = con.execute(
            "INSERT INTO users(username,password_hash,full_name,role,active) VALUES(?,?,?,?,1)",
            ("acceptance-worker", "unused", "Второй слесарь", "слесарь"),
        ).lastrowid
        warehouse_id = con.execute("SELECT id FROM warehouses WHERE code='MAIN'").fetchone()[0]
        part_id = con.execute(
            "INSERT INTO parts(code,name,warehouse_id,stock_qty,min_qty,unit_price) VALUES(?,?,?,?,?,?)",
            ("ACC-001", "Шарнир подвески", warehouse_id, 4, 1, 1500),
        ).lastrowid
        con.commit()
    finally:
        con.close()

    first_worker = client.get("/api/me").json()["id"]
    assignments = []
    for worker_id, role, rate in ((first_worker, "мастер", 1000), (second_worker, "слесарь", 800)):
        assigned = client.post(f"/api/repairs/orders/{order_id}/workers", json={
            "worker_id": worker_id, "role": role, "planned_hours": 2, "hourly_rate": rate,
        })
        assert assigned.status_code == 201, assigned.text
        assignments.append(assigned.json()["id"])
    for assignment_id in assignments:
        assert client.post(f"/api/repairs/workers/{assignment_id}/start").status_code == 200
        assert client.post(f"/api/repairs/workers/{assignment_id}/finish", json={"actual_hours": 2}).status_code == 200

    operation = client.post(f"/api/repairs/orders/{order_id}/operations", json={
        "name": "Замена шарниров подвески", "norm_hours": 3,
    })
    assert operation.status_code == 201, operation.text
    operation_id = operation.json()["id"]
    assert client.post(f"/api/repairs/operations/{operation_id}/start").status_code == 200
    completed = client.post(f"/api/repairs/operations/{operation_id}/complete", json={
        "actual_hours": 3.5, "result": "Шарниры заменены",
    })
    assert completed.status_code == 200, completed.text

    requested_part = client.post(f"/api/repairs/orders/{order_id}/parts", json={
        "part_id": part_id, "quantity": 2,
    })
    assert requested_part.status_code == 201, requested_part.text
    repair_part_id = requested_part.json()["id"]
    assert client.post(f"/api/repairs/parts/{repair_part_id}/issue", json={"quantity": 2}).status_code == 200
    assert client.post(f"/api/repairs/parts/{repair_part_id}/install", json={"quantity": 2}).status_code == 200

    for status in ("диагностика", "готов к работе", "в работе", "контроль"):
        changed = client.post(f"/api/repairs/orders/{order_id}/status", json={"status": status})
        assert changed.status_code == 200, changed.text

    negative = client.post(f"/api/repairs/orders/{order_id}/inspection", json={
        "result": "не годен", "release_allowed": False, "defects": "Требуется повторная протяжка",
    })
    assert negative.status_code == 201, negative.text
    assert negative.json()["order_status"] == "в работе"

    assert client.post(f"/api/repairs/orders/{order_id}/status", json={"status": "контроль"}).status_code == 200
    positive = client.post(f"/api/repairs/orders/{order_id}/inspection", json={
        "result": "годен", "release_allowed": True, "comment": "Замечаний нет",
    })
    assert positive.status_code == 201, positive.text
    closed = client.post(f"/api/repairs/orders/{order_id}/close", json={"result": "Подвеска исправна"})
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "завершен"
    assert closed.json()["parts_cost"] == 3000
    assert closed.json()["labor_cost"] == 3600
    assert closed.json()["total_cost"] == 6600

    history = client.get(f"/api/vehicles/{bus_id}/repair-history")
    assert history.status_code == 200
    assert history.json()["items"][0]["order_number"] == order.json()["order_number"]
    assert "Шарнир подвески" in history.json()["items"][0]["parts_json"]

    con = db.connect()
    try:
        from app.repair_service import vehicle_release_block_reason
        assert vehicle_release_block_reason(con, bus_id) == ""
        bus = con.execute("SELECT status FROM buses WHERE id=?", (bus_id,)).fetchone()
        audit_count = con.execute("SELECT COUNT(*) FROM audit_log WHERE object_type LIKE 'repair_%'").fetchone()[0]
        close_notification = con.execute(
            "SELECT message FROM notifications WHERE category='ремонт' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    assert bus["status"] == "исправен"
    assert audit_count >= 10
    assert close_notification and order.json()["order_number"] in close_notification["message"]

    report = client.get("/api/repairs/reports/export.xlsx")
    assert report.status_code == 200, report.text
    assert report.content[:2] == b"PK"