# -*- coding: utf-8 -*-
from tests.test_repair_operations_api import create_order
from tests.test_repair_requests_api import make_client
from tests.test_repair_stock_api import seed_part


def test_reserve_issue_return_and_install_preserve_quantities(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    part_id = seed_part(stock=5, price=1000)
    repair_part = client.post(f"/api/repairs/orders/{order['id']}/parts", json={
        "part_id": part_id, "quantity": 3,
    }).json()
    part_link = repair_part["id"]
    reserved = client.post(f"/api/repairs/parts/{part_link}/reserve", json={"quantity": 3})
    assert reserved.status_code == 200, reserved.text
    assert reserved.json()["reserved_qty"] == 3
    issued = client.post(f"/api/repairs/parts/{part_link}/issue", json={"quantity": 2})
    assert issued.status_code == 200, issued.text
    assert issued.json()["reserved_qty"] == 1
    assert issued.json()["stock_qty"] == 3
    returned = client.post(f"/api/repairs/parts/{part_link}/return", json={"quantity": 1})
    assert returned.status_code == 200, returned.text
    assert returned.json()["returned_qty"] == 1
    assert returned.json()["stock_qty"] == 4
    installed = client.post(f"/api/repairs/parts/{part_link}/install", json={"quantity": 1})
    assert installed.status_code == 200, installed.text
    assert installed.json()["installed_qty"] == 1
