# -*- coding: utf-8 -*-
from app.repair_schema import migrate_repairs


def test_vehicle_card_schema_is_idempotent(tmp_path):
    import app.db as db

    db.DB_PATH = str(tmp_path / "vehicle-card.db")
    db.init_db()
    con = db.connect()
    try:
        migrate_repairs(con)
        migrate_repairs(con)
        tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"vehicle_incidents", "vehicle_damages"} <= tables

        incident_columns = {
            row[1] for row in con.execute("PRAGMA table_info(vehicle_incidents)")
        }
        assert {
            "bus_id",
            "incident_type",
            "occurred_at",
            "repair_request_id",
            "repair_order_id",
            "actual_damage_cost",
            "cancel_reason",
        } <= incident_columns

        damage_columns = {
            row[1] for row in con.execute("PRAGMA table_info(vehicle_damages)")
        }
        assert {
            "incident_id",
            "area",
            "description",
            "severity",
            "resolved",
            "repair_order_id",
        } <= damage_columns

        media_columns = {
            row[1] for row in con.execute("PRAGMA table_info(repair_attachments)")
        }
        assert {
            "incident_id",
            "damage_id",
            "caption",
            "captured_at",
            "is_cover",
            "cancelled_at",
            "cancel_reason",
        } <= media_columns

        history_columns = {
            row[1] for row in con.execute("PRAGMA table_info(vehicle_repair_history)")
        }
        assert {
            "labor_cost",
            "parts_cost",
            "external_cost",
            "other_cost",
            "master_name",
        } <= history_columns
    finally:
        con.close()
