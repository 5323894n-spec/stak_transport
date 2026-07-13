# -*- coding: utf-8 -*-
from tests.test_repair_operations_api import create_order
from tests.test_repair_requests_api import make_client


def test_repair_calendar_combines_orders_and_maintenance_plans(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    refs = client.get("/api/repairs/references").json()

    import app.db as db
    con = db.connect()
    try:
        con.execute(
            "UPDATE repair_orders SET planned_start=?,planned_end=? WHERE id=?",
            ("2026-07-14T09:00:00", "2026-07-14T15:00:00", order["id"]),
        )
        con.commit()
    finally:
        con.close()

    plan = client.post("/api/repairs/maintenance/plans", json={
        "vehicle_id": bus_id,
        "repair_type_id": refs["repair_types"][0]["id"],
        "name": "ТО календарное",
        "next_date": "2026-07-18",
    })
    assert plan.status_code == 201, plan.text

    response = client.get("/api/repairs/calendar?date_from=2026-07-10&date_to=2026-07-20")
    assert response.status_code == 200, response.text
    events = response.json()["items"]
    assert [event["event_type"] for event in events] == ["ремонт", "ТО"]
    assert events[0]["order_number"] == order["order_number"]
    assert events[0]["garage_number"] == "Р-101"
    assert events[1]["title"] == "ТО календарное"
    assert events[1]["start"] == "2026-07-18"

    outside = client.get("/api/repairs/calendar?date_from=2026-08-01&date_to=2026-08-31")
    assert outside.status_code == 200
    assert outside.json()["items"] == []


def test_repair_calendar_rejects_reversed_range(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.get("/api/repairs/calendar?date_from=2026-07-20&date_to=2026-07-10")
    assert response.status_code == 400
    assert "диапазон" in response.json()["detail"].lower()