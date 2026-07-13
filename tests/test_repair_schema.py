# -*- coding: utf-8 -*-
import sqlite3


REPAIR_TABLES = {
    "repair_requests", "repair_orders", "repair_order_workers",
    "repair_operations", "repair_parts", "repair_inspections",
    "vehicle_repair_history", "repair_attachments", "maintenance_plans",
    "maintenance_events", "repair_types", "fault_categories",
    "vehicle_systems", "operation_catalog", "workshops", "repair_posts",
    "parts", "warehouses", "stock_movements", "repair_sequences",
    "repair_order_status_events",
}

BUS_COLUMNS = {
    "modification", "commissioned_at", "body_number", "engine_number",
    "engine_type", "ecological_class", "assigned_route_id", "last_to_date",
    "next_to_date", "warranty_status", "photo_path",
}


def init_repair_db(tmp_path):
    import app.db as db

    db.DB_PATH = str(tmp_path / "repair.db")
    db.init_db()
    return db


def test_repair_migration_is_idempotent_and_creates_all_tables(tmp_path):
    db = init_repair_db(tmp_path)
    db.init_db()

    con = db.connect()
    try:
        names = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        columns = {row[1] for row in con.execute("PRAGMA table_info(buses)")}
    finally:
        con.close()

    assert REPAIR_TABLES <= names
    assert BUS_COLUMNS <= columns


def test_repair_schema_has_query_indexes_and_document_constraints(tmp_path):
    db = init_repair_db(tmp_path)
    con = db.connect()
    try:
        indexes = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        request_sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='repair_requests'"
        ).fetchone()[0]
        order_sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='repair_orders'"
        ).fetchone()[0]
        foreign_keys = con.execute("PRAGMA foreign_key_list(repair_orders)").fetchall()
    finally:
        con.close()

    assert {
        "idx_repair_requests_vehicle", "idx_repair_requests_status",
        "idx_repair_requests_fault_category", "idx_repair_orders_vehicle",
        "idx_repair_orders_status", "idx_repair_orders_master",
        "idx_repair_orders_dates", "idx_repair_workers_worker",
        "idx_maintenance_events_due_date", "idx_stock_movements_part_date",
    } <= indexes
    assert "UNIQUE" in request_sql.upper() and "UNIQUE" in order_sql.upper()
    assert "CHECK" in order_sql.upper()
    assert foreign_keys


def test_repair_reference_data_is_seeded_without_duplicates(tmp_path):
    db = init_repair_db(tmp_path)
    db.init_db()
    con = db.connect()
    try:
        counts = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("repair_types", "fault_categories", "vehicle_systems", "workshops", "warehouses")
        }
        duplicate_codes = {
            table: con.execute(
                f"SELECT code FROM {table} GROUP BY code HAVING COUNT(*) > 1"
            ).fetchall()
            for table in counts
        }
    finally:
        con.close()

    assert all(count > 0 for count in counts.values())
    assert all(not rows for rows in duplicate_codes.values())


def test_repair_schema_rejects_negative_stock_and_duplicate_numbers(tmp_path):
    db = init_repair_db(tmp_path)
    con = db.connect()
    try:
        warehouse_id = con.execute("SELECT id FROM warehouses LIMIT 1").fetchone()[0]
        with __import__("pytest").raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO parts(code,name,warehouse_id,stock_qty) VALUES(?,?,?,?)",
                ("NEG", "Negative stock", warehouse_id, -1),
            )

        bus_id = con.execute(
            "INSERT INTO buses(garage_number) VALUES(?)", ("TEST-REPAIR",)
        ).lastrowid
        con.execute(
            "INSERT INTO repair_requests(number,bus_id,description) VALUES(?,?,?)",
            ("ЗР-2026-000001", bus_id, "first"),
        )
        with __import__("pytest").raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO repair_requests(number,bus_id,description) VALUES(?,?,?)",
                ("ЗР-2026-000001", bus_id, "duplicate"),
            )
    finally:
        con.close()
