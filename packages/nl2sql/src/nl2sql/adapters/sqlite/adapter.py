from typing import Any, List, Dict
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.dialects import sqlite
from nl2sql.adapters.sqlalchemy_base import (
    CostEstimate,
    DryRunResult,
    QueryPlan,
    BaseSQLAlchemyAdapter
)

from pydantic import BaseModel, Field
from typing import Optional
import sqlglot
from sqlglot import exp

# SQLite has no EXTRACT and no date truncation. Each template reads the date
# with STRFTIME/DATE; ``__x__`` stands for the operand. The engine wraps a
# date part in CAST(... AS INT) and a truncation in a YYYY-MM-DD format, so
# the types match every other adapter.
_DATE_PARTS = {
    "YEAR": "STRFTIME('%Y', __x__)",
    "QUARTER": "(CAST(STRFTIME('%m', __x__) AS INTEGER) + 2) / 3",
    "MONTH": "STRFTIME('%m', __x__)",
    "DAY": "STRFTIME('%d', __x__)",
}
_DATE_TRUNCS = {
    "YEAR": "DATE(__x__, 'start of year')",
    "QUARTER": "DATE(__x__, 'start of month', '-' || ((CAST(STRFTIME('%m', __x__) AS INTEGER) - 1) % 3) || ' months')",
    "MONTH": "DATE(__x__, 'start of month')",
    "DAY": "DATE(__x__)",
}


def _from_template(template: str, operand: exp.Expression) -> exp.Expression:
    tree = sqlglot.parse_one(template, read="sqlite")
    return tree.transform(
        lambda node: operand.copy() if isinstance(node, exp.Column) and node.name == "__x__" else node
    )


def _sqlite_dates(node: exp.Expression) -> exp.Expression:
    """Rewrites the engine's portable date nodes with SQLite's date functions."""
    if isinstance(node, exp.Extract) and node.name.upper() in _DATE_PARTS:
        return _from_template(_DATE_PARTS[node.name.upper()], node.expression)
    if isinstance(node, exp.TimestampTrunc) and node.unit and node.unit.name.upper() in _DATE_TRUNCS:
        return _from_template(_DATE_TRUNCS[node.unit.name.upper()], node.this)
    return node

class SqliteConnectionConfig(BaseModel):
    """Strict configuration schema for SQLite adapter."""
    type: str
    database: str = Field(..., description="Path to SQLite database file")
    options: Dict[str, Any] = Field(default_factory=dict)
    
    model_config = {"extra": "ignore"}

class SqliteAdapter(BaseSQLAlchemyAdapter):

    def construct_uri(self, args: Dict[str, Any]) -> str:
        """Constructs the SQLite connection URI.

        Args:
            args: The raw connection arguments dictionary.

        Returns:
            str: The fully constructed SQLAlchemy connection URI.
        
        Raises:
            ValidationError: If the configuration is invalid.
        """
        config = SqliteConnectionConfig(**args)
        return f"sqlite:///{config.database}"

    def connect(self) -> None:
        """Sqlite-specific connection with Locking Timeout."""
        if not self.connection_string:
             raise ValueError(f"Connection string is required for {self}")
             
        connect_args = {}
        if self.statement_timeout_ms:
            # SQLite 'timeout' is for waiting for the lock, not execution duration.
            # But it's the closest/best we can do for "timeout".
            connect_args["timeout"] = self.statement_timeout_ms / 1000.0

        try:
            self.engine = create_engine(
                self.connection_string, 
                pool_pre_ping=True, # Less relevant for sqlite but harmless
                execution_options=self.execution_options,
                connect_args=connect_args
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to connect to Sqlite: {e}")
            raise
    def dry_run(self, query: str) -> DryRunResult:
        try:
            with self.engine.connect() as conn:
                conn.execute(text(f"EXPLAIN QUERY PLAN {query}"))
            return DryRunResult(is_valid=True, error_message=None)
        except Exception as e:
            return DryRunResult(is_valid=False, error_message=str(e))

    def explain(self, query: str) -> QueryPlan:
         return QueryPlan(original_query=query, plan="EXPLAIN QUERY PLAN not fully parsed")


    
    def cost_estimate(self, query: str) -> CostEstimate:
        try:
            with self.engine.connect() as conn:
                conn.execute(text(f"EXPLAIN QUERY PLAN {query}"))
            return CostEstimate(estimated_cost=1.0, estimated_rows=10) # Stub
        except Exception:
            return CostEstimate(estimated_cost=-1.0, estimated_rows=0)


    def get_dialect(self) -> str:
        return sqlite.dialect.name

    def render_sql(self, expression: exp.Expression) -> str:
        """sqlglot's SQLite rendering, with date parts and truncation rewritten."""
        return expression.copy().transform(_sqlite_dates).sql(dialect=self.get_dialect())

    @property
    def exclude_schemas(self) -> set[str]:
        return set()