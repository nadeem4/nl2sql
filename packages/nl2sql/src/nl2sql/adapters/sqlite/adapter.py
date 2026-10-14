import time
from typing import Optional

from pydantic import Field
from sqlalchemy import Engine, event
from sqlalchemy.dialects import sqlite
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from nl2sql_sdk import ConnectionModel, DatasourceCapability
from nl2sql_sdk.sqlalchemy import SQLAlchemyAdapter
import sqlglot
from sqlglot import exp

# How many virtual-machine steps SQLite runs between calls to the progress handler.
_PROGRESS_STEPS = 10_000

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


class SqliteAdapter(SQLAlchemyAdapter):
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

    def capabilities(self) -> set[DatasourceCapability]:
        return super().capabilities() | {DatasourceCapability.ENFORCES_READ_ONLY,
                                         DatasourceCapability.ENFORCES_TIMEOUT}

    def build_engine(self) -> Engine:
        """The engine, stopping a statement that runs past ``limits.timeout_ms``.

        SQLite has no statement timeout (its ``timeout`` is a lock wait), so
        each statement installs a progress handler with its own deadline.
        SQLite calls it every few thousand steps of its virtual machine, while
        the statement runs and while its rows are fetched; past the deadline
        it returns true and SQLite stops the statement with "interrupted",
        reported as a refusal.
        """
        engine = super().build_engine()
        if not self.limits.timeout_ms:
            return engine
        seconds = self.limits.timeout_ms / 1000

        def set_deadline(conn, cursor, statement, parameters, context, executemany):
            deadline = time.monotonic() + seconds
            conn.connection.dbapi_connection.set_progress_handler(lambda: time.monotonic() > deadline,
                                                                  _PROGRESS_STEPS)

        event.listen(engine, "before_cursor_execute", set_deadline)
        return engine

    def classify(self, exc: BaseException) -> Optional[str]:
        """A file that cannot be opened is the ``database`` stage: read-only never creates one."""
        if "unable to open database file" in str(getattr(exc, "orig", None) or exc):
            return "database"
        return super().classify(exc)

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
