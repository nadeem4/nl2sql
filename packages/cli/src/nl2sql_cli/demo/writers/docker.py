
import pathlib
from typing import List, Dict, Any
from ..schemas import (
    REF_SQL_POSTGRES,
    OPS_SQL_POSTGRES,
    SUPPLY_SQL_MYSQL,
    HISTORY_SQL_MSSQL,
    DOCKER_COMPOSE_TEMPLATE
)

class DockerWriter:
    """Writes Demo Artifacts (SQL, .env, docker-compose) for Docker environment."""

    @staticmethod
    def write_docker(output_dir: pathlib.Path,
                     secrets: Dict[str, str],
                     ref_data: tuple,
                     ops_data: tuple,
                     supply_data: tuple,
                     history_data: tuple):
        """Main entry point."""
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Unpack
        factories, mtypes, shifts, departments, roles, segments = ref_data
        employees, machines, logs = ops_data
        products, suppliers, inventory, supplier_products = supply_data
        orders, items, runs = history_data
        
        # 1. SQL Scripts
        DockerWriter._write_sql(output_dir / "init_ref.sql", REF_SQL_POSTGRES, secrets["DEMO_REF_PASSWORD"], "ref_admin", "manufacturing_ref", {
            "factories": factories,
            "machine_types": mtypes,
            "shifts": shifts,
            "departments": departments,
            "employee_roles": roles,
            "customer_segments": segments
        })
        
        DockerWriter._write_sql(output_dir / "init_ops.sql", OPS_SQL_POSTGRES, secrets["DEMO_OPS_PASSWORD"], "ops_admin", "manufacturing_ops", {
            "employees": employees, "machines": machines, "maintenance_logs": logs
        })
        
        DockerWriter._write_mysql(output_dir / "init_supply.sql", SUPPLY_SQL_MYSQL, {
            "products": products, "suppliers": suppliers, "inventory": inventory, "supplier_products": supplier_products
        })
        
        DockerWriter._write_mssql(output_dir / "init_history.sql", HISTORY_SQL_MSSQL, secrets, {
            "sales_orders": orders, "sales_items": items, "production_runs": runs
        })
        
        # 2. .env
        DockerWriter._write_env(output_dir / ".env", secrets)
        
        # 3. docker-compose
        (output_dir / "docker-compose.demo.yml").write_text(DOCKER_COMPOSE_TEMPLATE, encoding="utf-8")

    @staticmethod
    def _to_insert(table: str, rows: List[Dict]) -> str:
        if not rows:
            return ""
        
        columns = ", ".join(rows[0].keys())
        values_list = []
        for r in rows:
            vals = []
            for v in r.values():
                if isinstance(v, str):
                    clean = v.replace("'", "''") # Basic HTML escape for SQL
                    vals.append(f"'{clean}'")
                else:
                    vals.append(str(v))
            values_list.append(f"({', '.join(vals)})")
            
        # Bulk Insert
        # Postgres supports multi-value INSERT
        return f"INSERT INTO {table} ({columns}) VALUES\n" + ",\n".join(values_list) + ";"

    @staticmethod
    def _write_sql(path: pathlib.Path, schema: str, password: str, user: str, db: str, data: Dict[str, List[Dict]]):
        inserts = []
        for tbl, rows in data.items():
            inserts.append(DockerWriter._to_insert(tbl, rows))
            
        inserts_str = "\n\n".join(inserts)
        
        script = f"""
-- Create User and Database
CREATE USER {user} WITH PASSWORD '{password}';
CREATE DATABASE {db} OWNER {user};

-- Connect and Populate
\\c {db};

{schema}

{inserts_str}

-- Grant Ownership
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {user};
"""
        path.write_text(script, encoding="utf-8")

    @staticmethod
    def _write_mysql(path: pathlib.Path, schema: str, data: Dict[str, List[Dict]]):
        inserts = []
        for tbl, rows in data.items():
            inserts.append(DockerWriter._to_insert(tbl, rows))
            
        inserts_str = "\n\n".join(inserts)
            
        script = f"""
CREATE DATABASE IF NOT EXISTS manufacturing_supply;
USE manufacturing_supply;

{schema}

{inserts_str}
"""
        path.write_text(script, encoding="utf-8")

    @staticmethod
    def _write_mssql(path: pathlib.Path, schema: str, secrets: Dict[str, str], data: Dict[str, List[Dict]]):
        inserts = []
        for tbl, rows in data.items():
            inserts.append(DockerWriter._to_insert(tbl, rows))
        
        inserts_str = "\nGO\n".join(inserts)
            
        script = f"""
IF NOT EXISTS(SELECT * FROM sys.databases WHERE name = 'manufacturing_history')
BEGIN
    CREATE DATABASE manufacturing_history
END
GO
USE manufacturing_history;
GO

-- Create User (Passed via sqlcmd -v vars)
CREATE LOGIN [$(HISTORY_USER)] WITH PASSWORD = '$(HISTORY_PASS)';
CREATE USER [$(HISTORY_USER)] FOR LOGIN [$(HISTORY_USER)];
ALTER ROLE [db_owner] ADD MEMBER [$(HISTORY_USER)];
GO

{schema}
GO

{inserts_str}
GO
"""
        path.write_text(script, encoding="utf-8")

    @staticmethod
    def _write_env(path: pathlib.Path, secrets: Dict[str, str]):
        # Matches the template we removed
        content = f"""# NL2SQL Demo Environment Secrets
# Generated automatically. DO NOT COMMIT TO VERSION CONTROL.

# 1. Manufacturing Ref (Postgres)
DEMO_REF_USER=ref_admin
DEMO_REF_PASSWORD={secrets["DEMO_REF_PASSWORD"]}

# 2. Manufacturing Ops (Postgres)
DEMO_OPS_USER=ops_admin
DEMO_OPS_PASSWORD={secrets["DEMO_OPS_PASSWORD"]}

# Postgres Superuser (Container Root)
DEMO_POSTGRES_USER=postgres
DEMO_POSTGRES_PASSWORD={secrets["DEMO_POSTGRES_PASSWORD"]}

# 3. Manufacturing Supply (MySQL)
DEMO_SUPPLY_USER=supply_admin
DEMO_SUPPLY_PASSWORD={secrets["DEMO_SUPPLY_PASSWORD"]}
DEMO_MYSQL_ROOT_PASSWORD={secrets["DEMO_MYSQL_ROOT_PASSWORD"]}

# 4. Manufacturing History (MSSQL)
DEMO_HISTORY_USER=history_admin
DEMO_HISTORY_PASSWORD={secrets["DEMO_HISTORY_PASSWORD"]}
DEMO_MSSQL_SA_PASSWORD={secrets["DEMO_MSSQL_SA_PASSWORD"]}
"""
        path.write_text(content, encoding="utf-8")
