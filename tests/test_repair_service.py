# -*- coding: utf-8 -*-
import datetime

import pytest
from fastapi import HTTPException


def test_worker_cannot_close_order():
    from app.repair_service import require_repair_action

    with pytest.raises(HTTPException) as exc:
        require_repair_action({"role": "слесарь"}, "close_order")
    assert exc.value.status_code == 403


def test_authorized_roles_can_perform_repair_actions():
    from app.repair_service import require_repair_action

    require_repair_action({"role": "мастер ремонта"}, "manage_order")
    require_repair_action({"role": "механик контроля"}, "inspect")
    require_repair_action({"role": "склад"}, "stock")


def test_transition_rejects_skipping_control():
    from app.repair_service import validate_transition

    with pytest.raises(HTTPException) as exc:
        validate_transition("в работе", "завершен", release_allowed=False)
    assert exc.value.status_code == 409


def test_transition_allows_close_after_positive_control():
    from app.repair_service import validate_transition

    validate_transition("контроль", "завершен", release_allowed=True)


def test_document_numbers_are_sequential_per_year(tmp_path):
    from tests.test_repair_schema import init_repair_db
    from app.repair_service import next_document_number

    db = init_repair_db(tmp_path)
    con = db.connect()
    try:
        assert next_document_number(con, "request", "ЗР", year=2026) == "ЗР-2026-000001"
        assert next_document_number(con, "request", "ЗР", year=2026) == "ЗР-2026-000002"
        assert next_document_number(con, "request", "ЗР", year=2027) == "ЗР-2027-000001"
    finally:
        con.close()


def test_active_repair_blocks_vehicle_release(tmp_path):
    from tests.test_repair_schema import init_repair_db
    from app.repair_service import active_repair_for_vehicle, vehicle_release_block_reason

    db = init_repair_db(tmp_path)
    con = db.connect()
    try:
        bus_id = con.execute("INSERT INTO buses(garage_number) VALUES(?)", ("R-1",)).lastrowid
        con.execute(
            "INSERT INTO repair_orders(number,bus_id,status) VALUES(?,?,?)",
            ("РМ-2026-000001", bus_id, "в работе"),
        )
        repair = active_repair_for_vehicle(con, bus_id)
        assert repair["number"] == "РМ-2026-000001"
        assert "РМ-2026-000001" in vehicle_release_block_reason(con, bus_id)
    finally:
        con.close()


def test_cost_and_downtime_calculations():
    from app.repair_service import calculate_cost, calculate_downtime

    assert calculate_cost(labor=1200, parts=3000, external=500, other=100) == 4800
    assert calculate_downtime(
        "2026-07-12T09:00:00", "2026-07-12T18:30:00"
    ) == 9.5
    assert calculate_downtime(None, datetime.datetime.now().isoformat()) == 0


def test_audit_change_records_old_and_new_values(tmp_path):
    from tests.test_repair_schema import init_repair_db
    from app.repair_service import audit_change

    db = init_repair_db(tmp_path)
    con = db.connect()
    try:
        audit_change(
            con,
            {"username": "admin"},
            "изменение ремонта",
            "repair_order",
            7,
            old={"status": "черновик"},
            new={"status": "в работе"},
        )
        row = con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        assert row["username"] == "admin"
        assert "черновик" in row["old_value"]
        assert "в работе" in row["new_value"]
    finally:
        con.close()
