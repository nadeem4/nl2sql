
import sqlite3
import pathlib
from typing import List, Dict
from ..schemas import (
    REF_SQL_SQLITE,
    OPS_SQL_SQLITE,
    SUPPLY_SQL_SQLITE,
    HISTORY_SQL_SQLITE
)

class SQLiteWriter:
    """Writes Demo Data to SQLite databases."""

    @staticmethod
    def write_lite(output_dir: pathlib.Path, 
                   ref_data: tuple, 
                   ops_data: tuple, 
                   supply_data: tuple, 
                   history_data: tuple):
        """Main entry point to write all 4 databases."""
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Unpack Data
        factories, mtypes, shifts = ref_data
        employees, machines, logs = ops_data
        products, suppliers, inventory = supply_data
        orders, items, runs = history_data
        
        # 1. Ref
        SQLiteWriter._create_db(output_dir / "manufacturing_ref.db", REF_SQL_SQLITE, {
            "factories": factories,
            "machine_types": mtypes,
            "shifts": shifts
        })
        
        # 2. Ops
        SQLiteWriter._create_db(output_dir / "manufacturing_ops.db", OPS_SQL_SQLITE, {
            "employees": employees,
            "machines": machines,
            "maintenance_logs": logs
        })
        
        # 3. Supply
        SQLiteWriter._create_db(output_dir / "manufacturing_supply.db", SUPPLY_SQL_SQLITE, {
            "products": products,
            "suppliers": suppliers,
            "inventory": inventory
        })
        
        # 4. History
        SQLiteWriter._create_db(output_dir / "manufacturing_history.db", HISTORY_SQL_SQLITE, {
            "sales_orders": orders,
            "sales_items": items,
            "production_runs": runs
        })
        
    @staticmethod
    def _create_db(path: pathlib.Path, schema_sql: str, data_map: Dict[str, List[Dict]]):
        """Creates a DB, runs schema, and inserts data."""
        if path.exists():
            path.unlink()
            
        conn = sqlite3.connect(str(path))
        cursor = conn.cursor()
        
        # 1. Schema
        cursor.executescript(schema_sql)
        
        # 2. Data
        for table_name, rows in data_map.items():
            if not rows:
                continue
                
            # Assume all rows have same keys
            keys = list(rows[0].keys())
            cols = ", ".join(keys)
            placeholders = ", ".join(f":{k}" for k in keys)
            sql = f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders})"
            
            cursor.executemany(sql, rows)
            
        conn.commit()
        conn.close()
