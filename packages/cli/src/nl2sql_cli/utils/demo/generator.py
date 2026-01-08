import sqlite3
import random
import datetime
from pathlib import Path
from typing import List, Dict, Any, Tuple
from .data import FACTORIES, SHIFTS, MACHINE_TYPES, PRODUCTS, SUPPLIERS

class DemoDataGenerator:
    def __init__(self, seed: int = 42):
        self.seed = seed
        random.seed(seed)
        
    def generate_lite(self, output_dir: Path):
        """Generates 4 SQLite databases in the output directory."""
        output_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"Generating Demo Data in {output_dir}...")
        self._gen_ref(output_dir / "manufacturing_ref.db")
        self._gen_ops(output_dir / "manufacturing_ops.db")
        self._gen_supply(output_dir / "manufacturing_supply.db")
        self._gen_history(output_dir / "manufacturing_history.db")
        print("[OK] Demo Data Generated.")

    def _create_db(self, path: Path, schema_sql: str) -> sqlite3.Connection:
        if path.exists():
            path.unlink()
        conn = sqlite3.connect(str(path))
        cursor = conn.cursor()
        cursor.executescript(schema_sql)
        conn.commit()
        return conn

    def _gen_ref(self, path: Path):
        schema = """
        CREATE TABLE factories (
            id INTEGER PRIMARY KEY,
            name TEXT,
            region TEXT,
            capacity INTEGER
        );
        CREATE TABLE machine_types (
            id INTEGER PRIMARY KEY,
            model TEXT,
            producer TEXT,
            maintenance_interval_days INTEGER
        );
        CREATE TABLE shifts (
            id INTEGER PRIMARY KEY,
            name TEXT,
            start_time TEXT,
            end_time TEXT
        );
        """
        conn = self._create_db(path, schema)
        cur = conn.cursor()
        
        cur.executemany("INSERT INTO factories VALUES (:id, :name, :region, :capacity)", FACTORIES)
        cur.executemany("INSERT INTO machine_types VALUES (:id, :model, :producer, :maintenance_interval_days)", MACHINE_TYPES)
        cur.executemany("INSERT INTO shifts VALUES (:id, :name, :start_time, :end_time)", SHIFTS)
        conn.commit()
        conn.close()

    def _gen_ops(self, path: Path):
        schema = """
        CREATE TABLE employees (
            id INTEGER PRIMARY KEY,
            name TEXT,
            factory_id INTEGER,
            shift_id INTEGER
        );
        CREATE TABLE machines (
            id INTEGER PRIMARY KEY,
            factory_id INTEGER,
            type_id INTEGER,
            status TEXT,
            installation_date DATE
        );
        CREATE TABLE maintenance_logs (
            id INTEGER PRIMARY KEY,
            machine_id INTEGER,
            date DATE,
            description TEXT,
            technician_id INTEGER
        );
        """
        conn = self._create_db(path, schema)
        cur = conn.cursor()
        
        # 1. Employees (Skewed to Austin=1)
        employees = []
        for i in range(1, 60):
            # 60% chance Austin, 20% Berlin, 20% Shanghai
            r = random.random()
            fid = 1 if r < 0.6 else (2 if r < 0.8 else 3)
            sid = random.choice([1, 2, 3])
            names = ["Smith", "Garcia", "Kim", "Muller", "Chen", "Patel", "Jones"]
            fname = random.choice(["John", "Maria", "Wei", "Hans", "Rahul", "Sarah"])
            employees.append((i, f"{fname} {random.choice(names)}", fid, sid))
            
        cur.executemany("INSERT INTO employees VALUES (?, ?, ?, ?)", employees)
        
        # 2. Machines
        machines = []
        mach_id = 1
        for factory in FACTORIES:
            # Each factory has 5-10 machines
            count = random.randint(5, 10)
            for _ in range(count):
                mtype = random.choice(MACHINE_TYPES)
                # Default OK
                status = "Active"
                date = (datetime.date.today() - datetime.timedelta(days=random.randint(100, 1000))).isoformat()
                machines.append((mach_id, factory["id"], mtype["id"], status, date))
                mach_id += 1
                
        # SCENARIO: 2 Machines in Error
        machines[3] = (machines[3][0], machines[3][1], machines[3][2], "Error", machines[3][4])
        machines[7] = (machines[7][0], machines[7][1], machines[7][2], "Maintenance", machines[7][4])
        
        cur.executemany("INSERT INTO machines VALUES (?, ?, ?, ?, ?)", machines)
        
        # 3. Logs
        logs = []
        log_id = 1
        # Generate logs for the broken machine (ID=4, index 3)
        broken_id = machines[3][0]
        logs.append((log_id, broken_id, datetime.date.today().isoformat(), "Vibration sensor alert triggered", 101))
        log_id += 1
        logs.append((log_id, broken_id, (datetime.date.today() - datetime.timedelta(days=1)).isoformat(), "Unusual noise reported", 102))
        log_id += 1
        
        cur.executemany("INSERT INTO maintenance_logs VALUES (?, ?, ?, ?, ?)", logs)
        
        conn.commit()
        conn.close()

    def _gen_supply(self, path: Path):
        schema = """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            sku TEXT,
            name TEXT,
            base_cost REAL,
            category TEXT
        );
        CREATE TABLE suppliers (
            id INTEGER PRIMARY KEY,
            name TEXT,
            country TEXT
        );
        CREATE TABLE inventory (
            product_id INTEGER,
            factory_id INTEGER,
            quantity INTEGER,
            PRIMARY KEY (product_id, factory_id)
        );
        """
        conn = self._create_db(path, schema)
        cur = conn.cursor()
        
        cur.executemany("INSERT INTO products VALUES (:id, :sku, :name, :base_cost, :category)", PRODUCTS)
        cur.executemany("INSERT INTO suppliers VALUES (:id, :name, :country)", SUPPLIERS)
        
        # Inventory
        inv = []
        for p in PRODUCTS:
            for f in FACTORIES:
                qty = random.randint(100, 1000)
                # SCENARIO: Bolt M5 (ID:4) Low in Berlin (ID:2)
                if p["id"] == 4 and f["id"] == 2:
                    qty = 5 # CRITICAL LOW
                inv.append((p["id"], f["id"], qty))
                
        cur.executemany("INSERT INTO inventory VALUES (?, ?, ?)", inv)
        conn.commit()
        conn.close()

    def _gen_history(self, path: Path):
        schema = """
        CREATE TABLE sales_orders (
            id INTEGER PRIMARY KEY,
            customer_name TEXT,
            order_date DATE,
            total_amount REAL
        );
        CREATE TABLE sales_items (
            id INTEGER PRIMARY KEY,
            order_id INTEGER,
            product_id INTEGER,
            quantity INTEGER,
            unit_price REAL
        );
        CREATE TABLE production_runs (
            id INTEGER PRIMARY KEY,
            factory_id INTEGER,
            date DATE,
            output_quantity INTEGER
        );
        """
        conn = self._create_db(path, schema)
        cur = conn.cursor()
        
        # Sales
        orders = []
        items = []
        item_id_counter = 1
        
        customers = ["Acme Inc", "Cyberdyne", "Wayne Ent", "Stark Ind", "Massive Dynamic"]
        
        start_date = datetime.date.today() - datetime.timedelta(days=365)
        
        for i in range(1, 1001): # 1000 orders
            cust = random.choice(customers)
            # Date Logic: Spike in Q4 (Oct, Nov, Dec is roughly days 270-365)
            # Simple weight: 50% chance to be in last 90 days
            if random.random() < 0.5:
                # Recent (Q4ish)
                delta = random.randint(270, 365)
            else:
                delta = random.randint(0, 270)
            
            date = (start_date + datetime.timedelta(days=delta)).isoformat()
            
            # Generate items first to get total
            order_total = 0
            num_items = random.randint(1, 5)
            for _ in range(num_items):
                prod = random.choice(PRODUCTS)
                qty = random.randint(1, 100)
                price = prod["base_cost"] * 1.5 # Margin
                line_total = qty * price
                order_total += line_total
                
                items.append((item_id_counter, i, prod["id"], qty, price))
                item_id_counter += 1
            
            orders.append((i, cust, date, order_total))
            
        cur.executemany("INSERT INTO sales_orders VALUES (?, ?, ?, ?)", orders)
        cur.executemany("INSERT INTO sales_items VALUES (?, ?, ?, ?, ?)", items)
        
        # Production Runs
        runs = []
        for i in range(1, 501):
            fid = random.choice([1,2,3])
            date = (start_date + datetime.timedelta(days=random.randint(0, 365))).isoformat()
            qty = random.randint(500, 2000)
            runs.append((i, fid, date, qty))
            
        cur.executemany("INSERT INTO production_runs VALUES (?, ?, ?, ?)", runs)
        
        conn.commit()
        conn.close()
