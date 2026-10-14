from pydantic import Field
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.dialects import sqlite
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from nl2sql.adapters.sqlalchemy_base import (
    CostEstimate,
    DryRunResult,
    QueryPlan,
    BaseSQLAlchemyAdapter
)

from nl2sql_sdk import ConnectionModel
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

# SQLite's messages for a query that is itself wrong; a new plan can fix these.
_QUERY_MISTAKES = (
    "no such column",
    "no such table",
    "no such function",
    "syntax error",
    "ambiguous column name",
    "wrong number of arguments",
    "misuse of aggregate",
    "misuse of window function",
)


class SqliteConnection(ConnectionModel):
    """A SQLite datasource's ``connection`` block."""

    database: str = Field(title="Database file", description="A path, relative to nl2sql.yaml",
                          examples=["shop.sqlite"])


class SqliteAdapter(BaseSQLAlchemyAdapter):
    title = "SQLite"
    connection_model = SqliteConnection
    driver_modules = ("sqlite3",)
    install_hint = 'sqlite3 ships with Python: use a Python build that includes it'

    def url(self, connection: SqliteConnection) -> str:
        """The file, always opened read-only.

        SQLite's own URI form with ``mode=ro`` makes the driver refuse every
        write before any SQL is parsed: the last line under the policy and
        validator checks. The engine never writes, so there is no setting to
        turn it off. A file that does not exist is an error, not a new
        database.
        """
        return f"sqlite:///file:{self.local_path(connection.database)}?mode=ro&uri=true"

    def build_engine(self) -> Engine:
        """The engine, with ``limits.timeout_ms`` as SQLite's lock-wait timeout.

        SQLite's ``timeout`` is how long to wait for a lock, not how long a
        statement may run, but it is the closest SQLite has.
        """
        connect_args = {}
        if self.limits.timeout_ms:
            connect_args["timeout"] = self.limits.timeout_ms / 1000.0
        return create_engine(self.url(self.connection), pool_pre_ping=True, connect_args=connect_args)

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

    def is_retryable_error(self, exc: SQLAlchemyError) -> bool:
        """SQLite reports a mistake in the query as an ``OperationalError``,
        the same kind as a locked or read-only database, and gives no
        SQLSTATE, so the query's own mistakes are told apart by message."""
        if isinstance(exc, OperationalError):
            message = str(getattr(exc, "orig", None) or exc).lower()
            return any(mistake in message for mistake in _QUERY_MISTAKES)
        return super().is_retryable_error(exc)

    @property
    def exclude_schemas(self) -> set[str]:
        return set()