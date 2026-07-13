# -*- coding: utf-8 -*-
import datetime

from tests.test_repair_operations_api import create_order
from tests.test_repair_requests_api import make_client


def test_order_status_events_are_recorded_and_closed_on_transition(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)

    import app.db as db
    con = db.connect()
    try:
        initial = con.execute(
            "SELECT status,entered_at,left_at FROM repair_order_status_events WHERE order_id=?",
            (order["id"],),
        ).fetchall()
    finally:
        con.close()
    assert [(row["status"], row["left_at"]) for row in initial] == [("черновик", None)]

    changed = client.post(f"/api/repairs/orders/{order['id']}/status", json={"status": "диагностика"})
    assert changed.status_code == 200, changed.text

    con = db.connect()
    try:
        events = con.execute(
            "SELECT status,entered_at,left_at FROM repair_order_status_events WHERE order_id=? ORDER BY id",
            (order["id"],),
        ).fetchall()
    finally:
        con.close()
    assert events[0]["status"] == "черновик" and events[0]["left_at"]
    assert events[1]["status"] == "диагностика" and events[1]["left_at"] is None


def test_downtime_metrics_include_stage_hours_and_readiness(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    assert client.post(f"/api/repairs/orders/{order['id']}/status", json={"status": "диагностика"}).status_code == 200

    import app.db as db
    con = db.connect()
    try:
        con.execute("INSERT INTO buses(garage_number,status) VALUES(?,?)", ("Р-102", "исправен"))
        now = datetime.datetime.now().replace(microsecond=0)
        con.execute(
            "UPDATE repair_order_status_events SET entered_at=?,left_at=? WHERE order_id=? AND status='черновик'",
            ((now - datetime.timedelta(hours=4)).isoformat(), (now - datetime.timedelta(hours=3)).isoformat(), order["id"]),
        )
        con.execute(
            "UPDATE repair_order_status_events SET entered_at=? WHERE order_id=? AND status='диагностика'",
            ((now - datetime.timedelta(hours=3)).isoformat(), order["id"]),
        )
        con.commit()
    finally:
        con.close()

    response = client.get("/api/repairs/metrics/downtime")
    assert response.status_code == 200, response.text
    metrics = response.json()
    assert metrics["fleet_total"] == 2
    assert metrics["unavailable_buses"] == 1
    assert metrics["readiness_coefficient"] == 0.5
    assert metrics["stage_hours"]["черновик"] == 1.0
    assert 2.99 <= metrics["stage_hours"]["диагностика"] <= 3.01
    assert 3.99 <= metrics["total_stage_hours"] <= 4.01