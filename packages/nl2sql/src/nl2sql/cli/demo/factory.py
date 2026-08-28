import random
import datetime
from typing import List, Dict, Any, Tuple
from .data import (
    FACTORIES,
    SHIFTS,
    MACHINE_TYPES,
    PRODUCTS,
    SUPPLIERS,
    DEPARTMENTS,
    CUSTOMER_SEGMENTS,
    EMPLOYEE_ROLES,
    SUPPLIER_PRODUCTS,
)

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

    def _weighted_choice(self, values: List[int], weights: List[float]) -> int:
        total = sum(weights)
        pick = random.random() * total
        cumulative = 0.0
        for value, weight in zip(values, weights):
            cumulative += weight
            if pick <= cumulative:
                return value
        return values[-1]

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

    def get_ref_data(self) -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict], List[Dict], List[Dict]]:
        """Returns reference data for shared dimensions."""
        return FACTORIES, MACHINE_TYPES, SHIFTS, DEPARTMENTS, EMPLOYEE_ROLES, CUSTOMER_SEGMENTS

    def get_ops_data(self) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """Generates (employees, machines, maintenance_logs)."""
        self._reset_random()

        factory_ids = [f["id"] for f in FACTORIES]
        factory_weights = [0.45, 0.2, 0.15, 0.12, 0.08]

        # 1. Employees
        employees = []
        role_ids = [r["id"] for r in EMPLOYEE_ROLES]
        role_weights = [0.45, 0.12, 0.12, 0.1, 0.16, 0.05]
        shift_ids = [s["id"] for s in SHIFTS]
        shift_weights = [0.5, 0.35, 0.15]
        last_names = ["Smith", "Garcia", "Kim", "Muller", "Chen", "Patel", "Jones", "Brown", "Nguyen", "Singh"]
        first_names = ["John", "Maria", "Wei", "Hans", "Rahul", "Sarah", "Aisha", "Luis", "Mina", "Kenji"]

        for i in range(1, 501):
            fid = self._weighted_choice(factory_ids, factory_weights)
            sid = self._weighted_choice(shift_ids, shift_weights)
            role_id = self._weighted_choice(role_ids, role_weights)
            role = next(r for r in EMPLOYEE_ROLES if r["id"] == role_id)
            hire_date = (datetime.date.today() - datetime.timedelta(days=random.randint(30, 365 * 8))).isoformat()
            status = "Active"
            roll = random.random()
            if roll < 0.07:
                status = "Leave"
            elif roll < 0.1:
                status = "Contractor"

            employees.append({
                "id": i,
                "name": f"{random.choice(first_names)} {random.choice(last_names)}",
                "factory_id": fid,
                "shift_id": sid,
                "hire_date": hire_date,
                "role_id": role_id,
                "department_id": role["department_id"],
                "status": status
            })

        # 2. Machines
        machines = []
        mach_id = 1
        total_capacity = sum(f["capacity"] for f in FACTORIES)
        for factory in FACTORIES:
            base = max(10, int((factory["capacity"] / total_capacity) * 150))
            count = base + random.randint(-2, 3)
            for _ in range(count):
                mtype = random.choice(MACHINE_TYPES)
                install_date = datetime.date.today() - datetime.timedelta(days=random.randint(120, 2000))
                last_maint = install_date + datetime.timedelta(days=random.randint(30, 900))
                if last_maint > datetime.date.today():
                    last_maint = datetime.date.today() - datetime.timedelta(days=random.randint(7, 90))
                status = "Active"
                if (datetime.date.today() - last_maint).days > int(mtype["maintenance_interval_days"] * 1.2):
                    status = "Maintenance"
                elif random.random() < 0.02:
                    status = "Error"

                machines.append({
                    "id": mach_id,
                    "factory_id": factory["id"],
                    "type_id": mtype["id"],
                    "status": status,
                    "installation_date": install_date.isoformat(),
                    "last_maintenance_date": last_maint.isoformat()
                })
                mach_id += 1

        # 3. Logs
        logs = []
        log_id = 1
        technician_ids = [e["id"] for e in employees if e["role_id"] == 5]
        severities = ["Low", "Medium", "High", "Critical"]
        descriptions = [
            "Vibration sensor alert triggered",
            "Unusual noise reported",
            "Temperature threshold exceeded",
            "Calibration drift detected",
            "Unexpected shutdown",
            "Hydraulic pressure drop",
        ]
        for _ in range(250):
            machine = random.choice(machines)
            tech_id = random.choice(technician_ids) if technician_ids else random.choice(employees)["id"]
            sev = random.choices(severities, weights=[0.45, 0.3, 0.2, 0.05], k=1)[0]
            downtime = random.randint(1, 4) if sev in ["Low", "Medium"] else random.randint(4, 12)
            log_date = datetime.date.today() - datetime.timedelta(days=random.randint(0, 120))
            logs.append({
                "id": log_id,
                "machine_id": machine["id"],
                "date": log_date.isoformat(),
                "description": random.choice(descriptions),
                "technician_id": tech_id,
                "severity": sev,
                "downtime_hours": downtime
            })
            log_id += 1

        return employees, machines, logs

    def get_supply_data(self) -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict]]:
        """Generates (products, suppliers, inventory, supplier_products)."""
        self._reset_random()
        
        # Products & Suppliers are static
        products = PRODUCTS
        suppliers = SUPPLIERS
        supplier_products = SUPPLIER_PRODUCTS
        
        # Inventory
        inventory = []
        for p in PRODUCTS:
            for f in FACTORIES:
                if p["id"] in [1, 2, 3]:
                    qty = random.randint(20, 200)
                else:
                    qty = random.randint(200, 2000)
                # Scenario: Low Stock
                if p["id"] == 4 and f["id"] == 2:
                    qty = 5
                if p["id"] == 2 and f["id"] == 4:
                    qty = 12
                last_updated = (datetime.date.today() - datetime.timedelta(days=random.randint(0, 14))).isoformat()
                
                inventory.append({
                    "product_id": p["id"],
                    "factory_id": f["id"],
                    "quantity": qty,
                    "last_updated": last_updated
                })
        
        return products, suppliers, inventory, supplier_products

    def get_history_data(self) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """Generates (sales_orders, sales_items, production_runs)."""
        self._reset_random()
        
        start_date = datetime.date.today() - datetime.timedelta(days=365)
        customers = ["Acme Inc", "Cyberdyne", "Wayne Ent", "Stark Ind", "Massive Dynamic"]
        segment_ids = [s["id"] for s in CUSTOMER_SEGMENTS]
        factory_ids = [f["id"] for f in FACTORIES]
        factory_weights = [0.45, 0.2, 0.15, 0.12, 0.08]
        order_statuses = ["Pending", "Shipped", "Delivered", "Cancelled"]
        
        orders = []
        items = []
        item_id_counter = 1
        monthly_volume = {m: 0 for m in range(1, 13)}
        
        # 5000 Orders
        for i in range(1, 5001):
            cust = random.choice(customers)
            if random.random() < 0.6:
                delta = random.randint(270, 365)
            else:
                delta = random.randint(0, 270)
            
            order_date = start_date + datetime.timedelta(days=delta)
            date_str = order_date.isoformat()
            fid = self._weighted_choice(factory_ids, factory_weights)
            status = random.choices(order_statuses, weights=[0.12, 0.25, 0.58, 0.05], k=1)[0]
            segment_id = random.choice(segment_ids)
            
            order_total = 0.0
            num_items = random.randint(1, 6)
            
            # Generate Items
            for _ in range(num_items):
                prod = random.choices(PRODUCTS, weights=[1, 1, 2, 6, 6, 4, 3, 4, 3], k=1)[0]
                qty = random.randint(1, 120)
                markup = random.uniform(1.3, 1.8)
                discount = 0.0
                if qty >= 50:
                    discount = random.choice([0.02, 0.05, 0.08])
                price = prod["base_cost"] * markup
                line_total = qty * price * (1 - discount)
                order_total += line_total
                
                items.append({
                    "id": item_id_counter,
                    "order_id": i,
                    "product_id": prod["id"],
                    "quantity": qty,
                    "unit_price": round(price, 2),
                    "discount_pct": round(discount * 100, 2)
                })
                item_id_counter += 1
                monthly_volume[order_date.month] += qty
                
            orders.append({
                "id": i,
                "customer_name": cust,
                "order_date": date_str,
                "total_amount": round(order_total, 2),
                "status": status,
                "customer_segment_id": segment_id,
                "factory_id": fid
            })
            
        # Production Runs
        runs = []
        run_id = 1
        for day_offset in range(0, 365):
            run_date = start_date + datetime.timedelta(days=day_offset)
            month = run_date.month
            season_multiplier = 1.0
            if month in [10, 11, 12]:
                season_multiplier = 1.35
            elif month in [6, 7, 8]:
                season_multiplier = 1.15

            for factory in FACTORIES:
                base = int((factory["capacity"] / 10) * season_multiplier)
                qty = max(250, base + random.randint(-120, 150))
                run_shift = self._weighted_choice([s["id"] for s in SHIFTS], [0.5, 0.35, 0.15])
                run_status = "Complete" if random.random() > 0.03 else "Delayed"

                runs.append({
                    "id": run_id,
                    "factory_id": factory["id"],
                    "date": run_date.isoformat(),
                    "output_quantity": qty,
                    "shift_id": run_shift,
                    "status": run_status
                })
                run_id += 1
            
        return orders, items, runs
