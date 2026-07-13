# -*- coding: utf-8 -*-
from tests.test_repair_requests_api import make_client
from tests.test_repair_operations_api import create_order


def incident_payload():
    return {
        "incident_type": "ДТП",
        "occurred_at": "2026-07-13T08:30:00",
        "place": "ул. Центральная, 10",
        "circumstances": "Касательное столкновение при выезде",
        "fault_status": "не установлена",
        "estimated_damage_cost": 150000,
        "create_repair_request": True,
        "damages": [
            {
                "area": "правый борт",
                "description": "Вмятина и царапины",
                "severity": "средняя",
            },
            {
                "area": "передняя дверь",
                "description": "Разбито стекло",
                "severity": "тяжёлая",
            },
        ],
    }


def test_incident_creates_damages_and_linked_repair_request(tmp_path):
    client, bus_id = make_client(tmp_path)

    response = client.post(
        f"/api/repairs/vehicles/{bus_id}/incidents", json=incident_payload()
    )

    assert response.status_code == 201, response.text
    item = response.json()
    assert item["incident_type"] == "ДТП"
    assert item["repair_request_id"]
    assert [row["area"] for row in item["damages"]] == [
        "правый борт",
        "передняя дверь",
    ]
    request = client.get(f"/api/repairs/requests/{item['repair_request_id']}")
    assert request.status_code == 200, request.text
    assert request.json()["incident_id"] == item["id"]


def test_incident_cancel_requires_reason_and_keeps_record(tmp_path):
    client, bus_id = make_client(tmp_path)
    created = client.post(
        f"/api/repairs/vehicles/{bus_id}/incidents", json=incident_payload()
    )
    assert created.status_code == 201, created.text
    incident = created.json()

    empty = client.post(
        f"/api/repairs/incidents/{incident['id']}/cancel", json={"reason": ""}
    )
    assert empty.status_code == 400

    cancelled = client.post(
        f"/api/repairs/incidents/{incident['id']}/cancel",
        json={"reason": "Дублирующая запись"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "отменено"
    assert cancelled.json()["cancel_reason"] == "Дублирующая запись"

    active = client.get(f"/api/repairs/vehicles/{bus_id}/incidents").json()["items"]
    assert active == []
    all_items = client.get(
        f"/api/repairs/vehicles/{bus_id}/incidents?include_cancelled=true"
    ).json()["items"]
    assert any(row["id"] == incident["id"] for row in all_items)

def test_incident_links_order_and_damage_can_be_resolved(tmp_path):
    client, bus_id = make_client(tmp_path)
    payload = incident_payload()
    payload["create_repair_request"] = False
    payload["damages"] = []
    created_incident = client.post(
        f"/api/repairs/vehicles/{bus_id}/incidents", json=payload
    )
    assert created_incident.status_code == 201, created_incident.text
    incident = created_incident.json()
    order = create_order(client, bus_id)

    import app.db as db

    con = db.connect()
    try:
        con.execute("UPDATE repair_orders SET total_cost=4200 WHERE id=?", (order["id"],))
        con.commit()
    finally:
        con.close()

    linked = client.patch(
        f"/api/repairs/incidents/{incident['id']}",
        json={"repair_order_id": order["id"]},
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["actual_damage_cost"] == 4200
    assert linked.json()["repair_order_number"] == order["order_number"]

    created = client.post(
        f"/api/repairs/incidents/{incident['id']}/damages",
        json={
            "area": "задний бампер",
            "description": "Трещина",
            "severity": "средняя",
        },
    )
    assert created.status_code == 201, created.text
    damage = created.json()

    resolved = client.patch(
        f"/api/repairs/damages/{damage['id']}",
        json={"resolved": True, "repair_order_id": order["id"]},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["resolved"] == 1
    assert resolved.json()["resolved_at"]
    assert resolved.json()["repair_order_id"] == order["id"]
