import random
import datetime
from typing import List, Dict, Any, Tuple
from .data import FACTORIES, SHIFTS, MACHINE_TYPES, PRODUCTS, SUPPLIERS

class DemoDataFactory:
    """
    Generates pure Python data structures (Lists of Dicts) for the Demo Environment.
    Ensures consistency between Lite (SQLite) and Docker (SQL Scripts) by being the single source of truth.
    """

    def __init__(self, seed: int = 42):
        self.seed = seed
        self._reset_random()

    def _reset_random(self):
        random.seed(self.seed)

    def generate_secrets(self) -> Dict[str, str]:
        """Generates random passwords for the demo environment."""
        self._reset_random() # Consistent secrets if called deterministically
        
        def _gen(length=24):
            chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
            return "".join(random.choice(chars) for _ in range(length))

        return {
            "DEMO_REF_PASSWORD": _gen(),
            "DEMO_OPS_PASSWORD": _gen(),
            "DEMO_POSTGRES_PASSWORD": _gen(),
            "DEMO_SUPPLY_PASSWORD": _gen(),
            "DEMO_MYSQL_ROOT_PASSWORD": _gen(),
            "DEMO_HISTORY_PASSWORD": f"StrongP@ss{_gen(8)}!",
            "DEMO_MSSQL_SA_PASSWORD": f"StrongP@ss{_gen(8)}!",
        }

    def get_ref_data(self) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """Returns (factories, machine_types, shifts). Data is static but returned for consistency."""
        return FACTORIES, MACHINE_TYPES, SHIFTS

    def get_ops_data(self) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """Generates (employees, machines, maintenance_logs)."""
        self._reset_random()
        
        # 1. Employees
        employees = []
        for i in range(1, 60):
            r = random.random()
            fid = 1 if r < 0.6 else (2 if r < 0.8 else 3)
            sid = random.choice([1, 2, 3])
            names = ["Smith", "Garcia", "Kim", "Muller", "Chen", "Patel", "Jones"]
            fname = random.choice(["John", "Maria", "Wei", "Hans", "Rahul", "Sarah"])
            employees.append({
                "id": i,
                "name": f"{fname} {random.choice(names)}",
                "factory_id": fid,
                "shift_id": sid
            })

        # 2. Machines
        machines = []
        mach_id = 1
        for factory in FACTORIES:
            count = random.randint(5, 10)
            for _ in range(count):
                mtype = random.choice(MACHINE_TYPES)
                status = "Active"
                date = (datetime.date.today() - datetime.timedelta(days=random.randint(100, 1000))).isoformat()
                
                machines.append({
                    "id": mach_id,
                    "factory_id": factory["id"],
                    "type_id": mtype["id"],
                    "status": status,
                    "installation_date": date
                })
                mach_id += 1
        
        # Scenario: Errors
        # We access by index (ID-1)
        if len(machines) > 3:
            machines[3]["status"] = "Error"
        if len(machines) > 7:
            machines[7]["status"] = "Maintenance"

        # 3. Logs
        logs = []
        log_id = 1
        broken_id = machines[3]["id"] if len(machines) > 3 else 1
        
        logs.append({
            "id": log_id,
            "machine_id": broken_id,
            "date": datetime.date.today().isoformat(),
            "description": "Vibration sensor alert triggered",
            "technician_id": 101
        })
        log_id += 1
        
        logs.append({
            "id": log_id,
            "machine_id": broken_id,
            "date": (datetime.date.today() - datetime.timedelta(days=1)).isoformat(),
            "description": "Unusual noise reported",
            "technician_id": 102
        })

        return employees, machines, logs

    def get_supply_data(self) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """Generates (products, suppliers, inventory)."""
        self._reset_random()
        
        # Products & Suppliers are static
        products = PRODUCTS
        suppliers = SUPPLIERS
        
        # Inventory
        inventory = []
        for p in PRODUCTS:
            for f in FACTORIES:
                qty = random.randint(100, 1000)
                # Scenario: Low Stock
                if p["id"] == 4 and f["id"] == 2:
                    qty = 5 
                
                inventory.append({
                    "product_id": p["id"],
                    "factory_id": f["id"],
                    "quantity": qty
                })
        
        return products, suppliers, inventory

    def get_history_data(self) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """Generates (sales_orders, sales_items, production_runs)."""
        self._reset_random()
        
        start_date = datetime.date.today() - datetime.timedelta(days=365)
        customers = ["Acme Inc", "Cyberdyne", "Wayne Ent", "Stark Ind", "Massive Dynamic"]
        
        orders = []
        items = []
        item_id_counter = 1
        
        # 1000 Orders
        for i in range(1, 1001):
            cust = random.choice(customers)
            if random.random() < 0.5:
                delta = random.randint(270, 365)
            else:
                delta = random.randint(0, 270)
            
            date = (start_date + datetime.timedelta(days=delta)).isoformat()
            
            order_total = 0.0
            num_items = random.randint(1, 5)
            
            # Generate Items
            for _ in range(num_items):
                prod = random.choice(PRODUCTS)
                qty = random.randint(1, 100)
                price = prod["base_cost"] * 1.5
                line_total = qty * price
                order_total += line_total
                
                items.append({
                    "id": item_id_counter,
                    "order_id": i,
                    "product_id": prod["id"],
                    "quantity": qty,
                    "unit_price": round(price, 2)
                })
                item_id_counter += 1
                
            orders.append({
                "id": i,
                "customer_name": cust,
                "order_date": date,
                "total_amount": round(order_total, 2)
            })
            
        # Production Runs
        runs = []
        for i in range(1, 501):
            fid = random.choice([1, 2, 3])
            date = (start_date + datetime.timedelta(days=random.randint(0, 365))).isoformat()
            qty = random.randint(500, 2000)
            
            runs.append({
                "id": i,
                "factory_id": fid,
                "date": date,
                "output_quantity": qty
            })
            
        return orders, items, runs
