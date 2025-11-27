from __future__ import annotations

from typing import Any, Dict, List

from langchain.tools import tool
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


class SqlTools:
    """
    Wraps DB + metadata into LangChain tools so they can be bound to specific agents.
    """

    def __init__(self, engine: Engine, row_limit: int = 100):
        self.engine = engine
        self.row_limit = row_limit

    @tool("list_tables", return_direct=False)
    def list_tables(self) -> List[str]:
        """List all tables in the current datasource."""
        inspector = inspect(self.engine)
        return inspector.get_table_names()

    @tool("describe_table", return_direct=False)
    def describe_table(self, table_name: str) -> Dict[str, Any]:
        """
        Return columns and types for a given table.
        table_name: Name of the table.
        """
        inspector = inspect(self.engine)
        cols = inspector.get_columns(table_name)
        return {col["name"]: str(col["type"]) for col in cols}

    @tool("run_sql", return_direct=False)
    def run_sql(self, sql: str) -> List[Dict[str, Any]]:
        """
        Execute a read-only SQL query and return rows.
        Rejects INSERT/UPDATE/DELETE and DDL; enforces a LIMIT clamp.
        """
        lowered = sql.lower().strip()
        forbidden = ("insert", "update", "delete", "drop", "alter", "create")
        if lowered.startswith(forbidden):
            raise ValueError("Writes/DDL are not allowed; this datasource is read-only.")

        # enforce limit clamp if missing
        if "limit" not in lowered:
            sql = f"{sql.rstrip(';')} LIMIT {self.row_limit}"

        with self.engine.connect() as conn:
            result = conn.execute(text(sql))
            rows = [dict(row._mapping) for row in result]
        return rows
