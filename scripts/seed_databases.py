#!/usr/bin/env python3
"""
Seed multiple databases (SQLite, Postgres, MySQL, MSSQL) with manufacturing data.
Uses Faker for synthetic data generation.
"""
import argparse
import json
import random
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any

import sqlalchemy
from sqlalchemy import create_engine, text
from faker import Faker

fake = Faker()

# --- Configuration ---

DATABASES = {
    "manufacturing_ref": "sqlite:///data/manufacturing.db",
    "manufacturing_ops": "postgresql+psycopg2://user:password@localhost:5432/manufacturing_ops",
    "manufacturing_supply": "mysql+pymysql://user:password@localhost:3306/manufacturing_supply",
    "manufacturing_history": "mssql+pymssql://sa:Password123!@localhost:1433/manufacturing_history",
}

# --- Schema Definitions (DDL) ---
# We use raw SQL for simplicity and direct control over dialect differences where needed.

DDL_SQLITE_REF = """
DROP TABLE IF EXISTS shifts;
DROP TABLE IF EXISTS machine_types;
DROP TABLE IF EXISTS factories;

CREATE TABLE factories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    opened_on DATE NOT NULL,
    capacity_index INTEGER DEFAULT 100
);

CREATE TABLE machine_types (
    id INTEGER PRIMARY KEY,
    model_name TEXT NOT NULL,
    manufacturer TEXT NOT NULL,
    specifications JSON
);

CREATE TABLE shifts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL
);
"""

DDL_POSTGRES_OPS = """
DROP TABLE IF EXISTS maintenance_logs;
DROP TABLE IF EXISTS spare_parts;
DROP TABLE IF EXISTS machines;
DROP TABLE IF EXISTS employees;

CREATE TABLE employees (
    id SERIAL PRIMARY KEY,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL,
    factory_id INTEGER NOT NULL, -- FK to SQLite (logical)
    hired_date DATE NOT NULL,
    contact_info JSONB
);

CREATE TABLE machines (
    id SERIAL PRIMARY KEY,
    factory_id INTEGER NOT NULL, -- FK to SQLite (logical)
    machine_type_id INTEGER NOT NULL, -- FK to SQLite (logical)
    name TEXT NOT NULL,
    serial_number TEXT NOT NULL UNIQUE,
    commissioned_on DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'Active'
);

CREATE TABLE spare_parts (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    machine_type_id INTEGER NOT NULL, -- FK to SQLite (logical)
    stock_quantity INTEGER NOT NULL,
    criticality TEXT NOT NULL
);

CREATE TABLE maintenance_logs (
    id SERIAL PRIMARY KEY,
    machine_id INTEGER NOT NULL, -- FK to machines
    performed_at TIMESTAMP NOT NULL,
    maintenance_type TEXT NOT NULL,
    downtime_minutes INTEGER NOT NULL,
    performed_by_id INTEGER NOT NULL, -- FK to employees
    notes TEXT
);
"""

DDL_MYSQL_SUPPLY = """
DROP TABLE IF EXISTS purchase_order_items;
DROP TABLE IF EXISTS purchase_orders;
DROP TABLE IF EXISTS inventory;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS suppliers;

CREATE TABLE suppliers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    country TEXT NOT NULL,
    rating FLOAT
);

CREATE TABLE products (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sku VARCHAR(255) NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL,
    supplier_id INT NOT NULL, -- FK to suppliers
    description TEXT
);

CREATE TABLE inventory (
    id INT AUTO_INCREMENT PRIMARY KEY,
    product_id INT NOT NULL, -- FK to products
    warehouse_location TEXT NOT NULL,
    quantity_on_hand INT NOT NULL,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE purchase_orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    supplier_id INT NOT NULL, -- FK to suppliers
    ordered_at DATETIME NOT NULL,
    status VARCHAR(50) NOT NULL,
    total_amount DECIMAL(12, 2) NOT NULL
);

CREATE TABLE purchase_order_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    purchase_order_id INT NOT NULL, -- FK to purchase_orders
    product_id INT NOT NULL, -- FK to products
    quantity INT NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL
);
"""

DDL_MSSQL_HISTORY = """
IF OBJECT_ID('sales_order_items', 'U') IS NOT NULL DROP TABLE sales_order_items;
IF OBJECT_ID('sales_orders', 'U') IS NOT NULL DROP TABLE sales_orders;
IF OBJECT_ID('defects', 'U') IS NOT NULL DROP TABLE defects;
IF OBJECT_ID('production_runs', 'U') IS NOT NULL DROP TABLE production_runs;
IF OBJECT_ID('customers', 'U') IS NOT NULL DROP TABLE customers;

CREATE TABLE customers (
    id INT IDENTITY(1,1) PRIMARY KEY,
    company_name NVARCHAR(255) NOT NULL,
    contact_name NVARCHAR(255) NOT NULL,
    email NVARCHAR(255) NOT NULL,
    region NVARCHAR(100) NOT NULL
);

CREATE TABLE production_runs (
    id INT IDENTITY(1,1) PRIMARY KEY,
    product_id INT NOT NULL, -- FK to MySQL (logical)
    machine_id INT NOT NULL, -- FK to Postgres (logical)
    start_time DATETIME NOT NULL,
    end_time DATETIME NOT NULL,
    quantity_produced INT NOT NULL,
    scrap_count INT NOT NULL DEFAULT 0
);

CREATE TABLE defects (
    id INT IDENTITY(1,1) PRIMARY KEY,
    production_run_id INT NOT NULL, -- FK to production_runs
    defect_type NVARCHAR(100) NOT NULL,
    severity NVARCHAR(50) NOT NULL,
    count INT NOT NULL
);

CREATE TABLE sales_orders (
    id INT IDENTITY(1,1) PRIMARY KEY,
    customer_id INT NOT NULL, -- FK to customers
    order_date DATETIME NOT NULL,
    status NVARCHAR(50) NOT NULL,
    total_amount DECIMAL(12, 2) NOT NULL
);

CREATE TABLE sales_order_items (
    id INT IDENTITY(1,1) PRIMARY KEY,
    sales_order_id INT NOT NULL, -- FK to sales_orders
    product_id INT NOT NULL, -- FK to MySQL (logical)
    quantity INT NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL
);
"""

# --- Seeding Logic ---

def seed_sqlite(engine):
    print("Seeding SQLite (Ref)...")
    with engine.connect() as conn:
        # Factories
        factories = [
            {"id": 1, "name": "Plant Austin", "location": "Austin, TX", "opened_on": "2010-01-01"},
            {"id": 2, "name": "Plant Berlin", "location": "Berlin, DE", "opened_on": "2015-05-20"},
            {"id": 3, "name": "Plant Tokyo", "location": "Tokyo, JP", "opened_on": "2018-11-11"},
        ]
        for f in factories:
            conn.execute(text("INSERT INTO factories (id, name, location, opened_on) VALUES (:id, :name, :location, :opened_on)"), f)
        
        # Machine Types
        types = ["Press", "CNC", "Lathe", "Injection Molder", "Conveyor"]
        for i, t in enumerate(types, 1):
            conn.execute(text("INSERT INTO machine_types (id, model_name, manufacturer, specifications) VALUES (:id, :model, :mfg, :specs)"), 
                         {"id": i, "model": t, "mfg": fake.company(), "specs": json.dumps({"power": "220V", "weight": random.randint(500, 2000)})})

        # Shifts
        shifts = [
            {"id": 1, "name": "Morning", "start": "06:00", "end": "14:00"},
            {"id": 2, "name": "Afternoon", "start": "14:00", "end": "22:00"},
            {"id": 3, "name": "Night", "start": "22:00", "end": "06:00"},
        ]
        for s in shifts:
            conn.execute(text("INSERT INTO shifts (id, name, start_time, end_time) VALUES (:id, :name, :start, :end)"), s)
        conn.commit()

def seed_postgres(engine):
    print("Seeding Postgres (Ops)...")
    with engine.connect() as conn:
        # Employees (100)
        for _ in range(100):
            conn.execute(text("INSERT INTO employees (full_name, role, factory_id, hired_date, contact_info) VALUES (:name, :role, :fid, :date, :info)"),
                         {"name": fake.name(), "role": random.choice(["Operator", "Technician", "Manager"]), "fid": random.randint(1, 3), 
                          "date": fake.date_between(start_date='-5y'), "info": json.dumps({"email": fake.email()})})
        
        # Machines (50)
        for i in range(50):
            conn.execute(text("INSERT INTO machines (factory_id, machine_type_id, name, serial_number, commissioned_on) VALUES (:fid, :mtid, :name, :sn, :date)"),
                         {"fid": random.randint(1, 3), "mtid": random.randint(1, 5), "name": f"Machine-{i}", "sn": fake.uuid4(), "date": fake.date_between(start_date='-10y')})
        
        # Spare Parts (200)
        for _ in range(200):
            conn.execute(text("INSERT INTO spare_parts (name, machine_type_id, stock_quantity, criticality) VALUES (:name, :mtid, :qty, :crit)"),
                         {"name": fake.word().title() + " Part", "mtid": random.randint(1, 5), "qty": random.randint(0, 100), "crit": random.choice(["Low", "Medium", "High"])})

        # Maintenance Logs (2000)
        # Need machine IDs and employee IDs. Assuming serial IDs 1..50 and 1..100
        for _ in range(2000):
            conn.execute(text("INSERT INTO maintenance_logs (machine_id, performed_at, maintenance_type, downtime_minutes, performed_by_id, notes) VALUES (:mid, :at, :type, :down, :eid, :notes)"),
                         {"mid": random.randint(1, 50), "at": fake.date_time_this_year(), "type": random.choice(["Preventive", "Corrective"]), 
                          "down": random.randint(10, 240), "eid": random.randint(1, 100), "notes": fake.sentence()})
        conn.commit()

def seed_mysql(engine):
    print("Seeding MySQL (Supply)...")
    with engine.connect() as conn:
        # Suppliers (50)
        for _ in range(50):
            conn.execute(text("INSERT INTO suppliers (name, contact_email, country, rating) VALUES (:name, :email, :country, :rating)"),
                         {"name": fake.company(), "email": fake.company_email(), "country": fake.country(), "rating": random.uniform(1.0, 5.0)})
        
        # Products (500)
        for _ in range(500):
            conn.execute(text("INSERT INTO products (sku, name, category, unit_price, supplier_id, description) VALUES (:sku, :name, :cat, :price, :sid, :desc)"),
                         {"sku": fake.ean13(), "name": fake.bs().title(), "cat": random.choice(["Electronics", "Mechanics", "Fluids"]), 
                          "price": random.uniform(10, 1000), "sid": random.randint(1, 50), "desc": fake.text(max_nb_chars=50)})
        
        # Inventory (1000)
        for _ in range(1000):
            conn.execute(text("INSERT INTO inventory (product_id, warehouse_location, quantity_on_hand) VALUES (:pid, :loc, :qty)"),
                         {"pid": random.randint(1, 500), "loc": random.choice(["WH-A", "WH-B", "WH-C"]), "qty": random.randint(0, 5000)})
        
        # Purchase Orders (2000)
        for _ in range(2000):
            res = conn.execute(text("INSERT INTO purchase_orders (supplier_id, ordered_at, status, total_amount) VALUES (:sid, :at, :status, :amt)"),
                               {"sid": random.randint(1, 50), "at": fake.date_time_this_year(), "status": random.choice(["Pending", "Received", "Cancelled"]), "amt": 0})
            po_id = res.lastrowid
            
            # PO Items
            total = 0
            for _ in range(random.randint(1, 5)):
                price = random.uniform(10, 500)
                qty = random.randint(1, 100)
                conn.execute(text("INSERT INTO purchase_order_items (purchase_order_id, product_id, quantity, unit_price) VALUES (:poid, :pid, :qty, :price)"),
                             {"poid": po_id, "pid": random.randint(1, 500), "qty": qty, "price": price})
                total += price * qty
            
            conn.execute(text("UPDATE purchase_orders SET total_amount = :total WHERE id = :id"), {"total": total, "id": po_id})
        conn.commit()

def seed_mssql(engine):
    print("Seeding MSSQL (History)...")
    with engine.connect() as conn:
        # Customers (200)
        for _ in range(200):
            conn.execute(text("INSERT INTO customers (company_name, contact_name, email, region) VALUES (:name, :contact, :email, :region)"),
                         {"name": fake.company(), "contact": fake.name(), "email": fake.email(), "region": random.choice(["NA", "EU", "APAC"])})
        
        # Production Runs (5000)
        for _ in range(5000):
            start = fake.date_time_this_year()
            end = start + timedelta(hours=random.randint(1, 12))
            res = conn.execute(text("INSERT INTO production_runs (product_id, machine_id, start_time, end_time, quantity_produced, scrap_count) VALUES (:pid, :mid, :start, :end, :qty, :scrap)"),
                               {"pid": random.randint(1, 500), "mid": random.randint(1, 50), "start": start, "end": end, "qty": random.randint(100, 10000), "scrap": random.randint(0, 50)})
            # No easy lastrowid in MSSQL with execute, but we don't strictly need it for defects if we just generate random defects for random runs
        
        # Defects (1000)
        for _ in range(1000):
            conn.execute(text("INSERT INTO defects (production_run_id, defect_type, severity, count) VALUES (:rid, :type, :sev, :cnt)"),
                         {"rid": random.randint(1, 5000), "type": random.choice(["Crack", "Dent", "Dimension", "Color"]), "sev": random.choice(["Minor", "Major", "Critical"]), "cnt": random.randint(1, 10)})

        # Sales Orders (5000)
        for _ in range(5000):
            # MSSQL IDENTITY insert
            conn.execute(text("INSERT INTO sales_orders (customer_id, order_date, status, total_amount) VALUES (:cid, :date, :status, :amt)"),
                         {"cid": random.randint(1, 200), "date": fake.date_time_this_year(), "status": random.choice(["New", "Shipped", "Closed"]), "amt": 0})
            
            # We need the ID. In MSSQL, scope_identity() is tricky with basic execute. 
            # For bulk data generation, we can just insert items blindly linking to random orders 1..5000
        
        # Sales Order Items (15000)
        for _ in range(15000):
             conn.execute(text("INSERT INTO sales_order_items (sales_order_id, product_id, quantity, unit_price) VALUES (:oid, :pid, :qty, :price)"),
                          {"oid": random.randint(1, 5000), "pid": random.randint(1, 500), "qty": random.randint(1, 50), "price": random.uniform(50, 2000)})
        
        conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", action="store_true", help="Wait for DBs to be ready")
    args = parser.parse_args()

    if args.wait:
        print("Waiting 10s for DBs to start...")
        time.sleep(10)

    # 1. SQLite
    engine_sqlite = create_engine(DATABASES["manufacturing_ref"])
    with engine_sqlite.connect() as conn:
        # Access raw DBAPI connection for executescript
        conn.connection.executescript(DDL_SQLITE_REF)
    seed_sqlite(engine_sqlite)

    # 2. Postgres
    try:
        engine_pg = create_engine(DATABASES["manufacturing_ops"])
        with engine_pg.connect() as conn:
            conn.execute(text(DDL_POSTGRES_OPS))
            conn.commit()
        seed_postgres(engine_pg)
    except Exception as e:
        print(f"Skipping Postgres: {e}")

    # 3. MySQL
    try:
        engine_mysql = create_engine(DATABASES["manufacturing_supply"])
        with engine_mysql.connect() as conn:
            # MySQL doesn't support executing multiple statements in one go easily with some drivers without config
            # But let's try splitting
            for stmt in DDL_MYSQL_SUPPLY.split(";"):
                if stmt.strip():
                    conn.execute(text(stmt))
            conn.commit()
        seed_mysql(engine_mysql)
    except Exception as e:
        print(f"Skipping MySQL: {e}")

    # 4. MSSQL
    try:
        engine_mssql = create_engine(DATABASES["manufacturing_history"])
        with engine_mssql.connect() as conn:
            # MSSQL also prefers single statements usually
            for stmt in DDL_MSSQL_HISTORY.split(";"):
                if stmt.strip():
                    conn.execute(text(stmt))
            conn.commit()
        seed_mssql(engine_mssql)
    except Exception as e:
        print(f"Skipping MSSQL: {e}")

    print("Seeding complete.")

if __name__ == "__main__":
    main()
