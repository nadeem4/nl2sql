#!/usr/bin/env python3
"""
Create a sample SQLite database for a manufacturing domain.

Usage:
    python scripts/setup_sqlite_manufacturing.py --db data/manufacturing.db
"""
import argparse
import pathlib
import sqlite3
from datetime import datetime


def ensure_dir(path: pathlib.Path) -> None:
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)


def connect(db_path: pathlib.Path) -> sqlite3.Connection:
    ensure_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS maintenance_logs;
        DROP TABLE IF EXISTS defects;
        DROP TABLE IF EXISTS production_runs;
        DROP TABLE IF EXISTS inventory;
        DROP TABLE IF EXISTS machines;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS factories;

        CREATE TABLE factories (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            location TEXT NOT NULL,
            opened_on DATE NOT NULL
        );

        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            sku TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            category TEXT NOT NULL
        );

        CREATE TABLE machines (
            id INTEGER PRIMARY KEY,
            factory_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            serial_number TEXT NOT NULL UNIQUE,
            line TEXT NOT NULL,
            commissioned_on DATE NOT NULL,
            FOREIGN KEY (factory_id) REFERENCES factories(id)
        );

        CREATE TABLE inventory (
            id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL,
            warehouse TEXT NOT NULL,
            on_hand INTEGER NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(id)
        );

        CREATE TABLE production_runs (
            id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL,
            machine_id INTEGER NOT NULL,
            operator TEXT NOT NULL,
            shift TEXT NOT NULL,
            started_at DATETIME NOT NULL,
            ended_at DATETIME NOT NULL,
            quantity_produced INTEGER NOT NULL,
            scrap_count INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (machine_id) REFERENCES machines(id)
        );

        CREATE TABLE defects (
            id INTEGER PRIMARY KEY,
            production_run_id INTEGER NOT NULL,
            defect_type TEXT NOT NULL,
            defect_count INTEGER NOT NULL,
            severity TEXT NOT NULL,
            FOREIGN KEY (production_run_id) REFERENCES production_runs(id)
        );

        CREATE TABLE maintenance_logs (
            id INTEGER PRIMARY KEY,
            machine_id INTEGER NOT NULL,
            performed_at DATETIME NOT NULL,
            maintenance_type TEXT NOT NULL,
            notes TEXT,
            downtime_minutes INTEGER NOT NULL,
            performed_by TEXT NOT NULL,
            FOREIGN KEY (machine_id) REFERENCES machines(id)
        );

        CREATE INDEX idx_machines_factory ON machines(factory_id);
        CREATE INDEX idx_inventory_product ON inventory(product_id);
        CREATE INDEX idx_runs_product ON production_runs(product_id);
        CREATE INDEX idx_runs_machine ON production_runs(machine_id);
        CREATE INDEX idx_defects_run ON defects(production_run_id);
        CREATE INDEX idx_maint_machine ON maintenance_logs(machine_id);
        """
    )
    conn.commit()


def seed_data(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    factories = [
        (1, "Plant A", "Cincinnati, OH", "2010-03-15"),
        (2, "Plant B", "Austin, TX", "2016-07-01"),
    ]
    products = [
        (1, "SKU-100", "Widget Alpha", "Widgets"),
        (2, "SKU-200", "Widget Beta", "Widgets"),
        (3, "SKU-300", "Gadget Gamma", "Gadgets"),
    ]
    machines = [
        (1, 1, "Press-01", "SN-PR-001", "Line 1", "2012-01-10"),
        (2, 1, "Press-02", "SN-PR-002", "Line 1", "2013-05-22"),
        (3, 2, "Cutter-01", "SN-CT-010", "Line A", "2017-02-14"),
    ]
    inventory = [
        (1, 1, "WH-Ohio", 1200, "2024-04-10T08:00:00"),
        (2, 2, "WH-Ohio", 800, "2024-04-10T08:00:00"),
        (3, 3, "WH-Texas", 500, "2024-04-10T08:00:00"),
    ]
    production_runs = [
        (1, 1, 1, "Alice", "Day", "2024-04-09T08:00:00", "2024-04-09T16:00:00", 500, 12),
        (2, 1, 2, "Bob", "Night", "2024-04-09T20:00:00", "2024-04-10T04:00:00", 450, 20),
        (3, 2, 1, "Charlie", "Day", "2024-04-10T08:00:00", "2024-04-10T16:00:00", 480, 15),
        (4, 3, 3, "Dana", "Day", "2024-04-09T08:00:00", "2024-04-09T15:00:00", 300, 5),
    ]
    defects = [
        (1, 1, "Surface", 5, "low"),
        (2, 2, "Dimension", 8, "medium"),
        (3, 2, "Surface", 4, "low"),
        (4, 3, "Assembly", 6, "medium"),
        (5, 4, "Surface", 2, "low"),
    ]
    maintenance_logs = [
        (1, 1, "2024-04-08T18:00:00", "Preventive", "Lubrication and inspection", 60, "Eve"),
        (2, 2, "2024-04-07T10:00:00", "Corrective", "Replaced belt", 120, "Frank"),
        (3, 3, "2024-04-08T12:00:00", "Preventive", "Blade sharpening", 90, "Grace"),
    ]

    cur.executemany("INSERT INTO factories VALUES (?, ?, ?, ?)", factories)
    cur.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", products)
    cur.executemany("INSERT INTO machines VALUES (?, ?, ?, ?, ?, ?)", machines)
    cur.executemany("INSERT INTO inventory VALUES (?, ?, ?, ?, ?)", inventory)
    cur.executemany(
        "INSERT INTO production_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", production_runs
    )
    cur.executemany("INSERT INTO defects VALUES (?, ?, ?, ?, ?)", defects)
    cur.executemany("INSERT INTO maintenance_logs VALUES (?, ?, ?, ?, ?, ?, ?)", maintenance_logs)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Setup sample SQLite DB for manufacturing.")
    parser.add_argument(
        "--db",
        type=pathlib.Path,
        default=pathlib.Path("data/manufacturing.db"),
        help="Path to the SQLite database file to create.",
    )
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        create_schema(conn)
        seed_data(conn)
    finally:
        conn.close()

    print(f"SQLite manufacturing database created at: {args.db.resolve()}")


if __name__ == "__main__":
    main()
