# -*- coding: utf-8 -*-
from tests.test_repair_operations_api import create_order
from tests.test_repair_requests_api import make_client


def test_repair_order_print_form_contains_required_sections(tmp_path):
    client, bus_id = make_client(tmp_path)
    order = create_order(client, bus_id)
    response = client.get(f"/api/repairs/orders/{order['id']}/print")
    assert response.status_code == 200, response.text
    assert order["order_number"] in response.text
    assert "ЗАКАЗ-НАРЯД НА РЕМОНТ" in response.text
    assert "Р-101" in response.text
    assert "Операции" in response.text
    assert "Исполнители" in response.text
    assert "Запчасти" in response.text
    assert "Подпись мастера" in response.text
