import json
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import Field, SecretStr
from sqlalchemy import URL, Connection, text
from sqlalchemy.exc import SQLAlchemyError

from nl2sql_sdk import DatasourceCapability, Estimate
from nl2sql_sdk.sqlalchemy import SQLAlchemyAdapter, SQLAlchemyConnection, driver_message

# libpq reports a failed connection before the server gives any SQLSTATE, so
# psycopg2 has none: the message is all there is. Checked in this order.
_STAGE_BY_MESSAGE = (
    ("password authentication failed", "auth"),
    ("no password supplied", "auth"),
    ("no pg_hba.conf entry", "auth"),
    ("does not exist", "database"),
    ("permission denied", "permission"),
    ("connection refused", "network"),
    ("could not translate host name", "network"),
    ("timeout expired", "network"),
    ("no route to host", "network"),
    ("could not connect", "network"),
)
# Plan nodes that read through an index: each counts its own Plan Rows.
_INDEX_SCANS = {"Index Scan", "Index Only Scan", "Bitmap Index Scan"}
# What reading an unexpected plan raises: it becomes the same RuntimeError as a failed EXPLAIN.
_UNREADABLE = (KeyError, TypeError, ValueError, AttributeError, IndexError)


class PostgresConnection(SQLAlchemyConnection):
    """A PostgreSQL datasource's ``connection`` block."""

    host: str = Field(title="Host", examples=["db.internal"])
    port: int = Field(5432, title="Port")
    database: str = Field(title="Database")
    user: str = Field(title="User")
    password: SecretStr = Field(title="Password")
    sslmode: Literal["disable", "prefer", "require", "verify-full"] = Field(
        "prefer", title="SSL mode", json_schema_extra={"group": "advanced"})


class PostgresAdapter(SQLAlchemyAdapter):
    title = "PostgreSQL"
    connection_model = PostgresConnection
    driver_modules = ("psycopg2",)
    install_hint = 'pip install "nl2sql-engine[postgres]"'

    def url(self, connection: PostgresConnection) -> URL:
        # Name the driver: the `postgres` extra installs psycopg2, and a bare
        # `postgresql://` means psycopg (v3) from SQLAlchemy 2.1 onwards.
        # URL.create escapes the credentials, so a password holding @ : / # %
        # stays a password instead of moving the host or port boundary.
        return URL.create(
            "postgresql+psycopg2",
            username=connection.user,
            password=connection.password.get_secret_value() or None,
            host=connection.host,
            port=connection.port,
            database=connection.database,
            query={"sslmode": connection.sslmode},
        )

    def capabilities(self) -> set[DatasourceCapability]:
        return super().capabilities() | {DatasourceCapability.ENFORCES_READ_ONLY,
                                         DatasourceCapability.ENFORCES_TIMEOUT,
                                         DatasourceCapability.ESTIMATES_COST}

    def estimate(self, sql: str, parameters: Dict[str, Any]) -> Estimate:
        """The rows ``sql`` reads, from ``EXPLAIN (FORMAT JSON, VERBOSE)``, without running it.

        A ``Seq Scan`` counts its whole relation, not its ``Plan Rows``
        (what is left after the filter): the ``Plan Rows`` of an unfiltered
        scan of it, which the planner makes from ``pg_class.reltuples``
        scaled to the relation's current size on disk, so a relation never
        analyzed has one too. ``Index Scan``, ``Index Only Scan`` and
        ``Bitmap Index Scan`` count their ``Plan Rows``. A scan on the inner
        side of a ``Nested Loop`` counts once per outer row, unless a
        ``Materialize`` keeps its rows. Not counted: ``Foreign Scan``,
        ``Function Scan`` and scans under a correlated ``SubPlan`` more than
        once. ``cost`` is the plan's ``Total Cost``. Runs read-only, under
        ``statement_timeout``.
        """
        try:
            with self.engine.connect() as conn:
                plan = _plan(self._run(conn, f"EXPLAIN (FORMAT JSON, VERBOSE) {sql}", parameters).scalar())
                seq_scans, rows = scans(plan)
                sizes = {(schema, name): self._planned_size(conn, schema, name)
                         for schema, name, _ in seq_scans}
            rows += sum(sizes[(schema, name)] * times for schema, name, times in seq_scans)
            cost = plan.get("Total Cost")
            return Estimate(rows=int(rows), cost=float(cost) if cost is not None else None,
                            source="EXPLAIN (FORMAT JSON) and pg_class.reltuples")
        except SQLAlchemyError as exc:
            raise RuntimeError(f"{self} could not estimate the query: {driver_message(exc)}") from None
        except _UNREADABLE as exc:
            raise RuntimeError(f"{self} could not estimate the query: the plan could not be read "
                               f"({type(exc).__name__})") from None

    def _planned_size(self, conn: Connection, schema: str, name: str) -> int:
        """The planner's estimate of a relation's rows: ``Plan Rows`` of an unfiltered scan of it."""
        quote = conn.dialect.identifier_preparer.quote
        return int(_plan(conn.execute(text(f"EXPLAIN (FORMAT JSON) SELECT * FROM {quote(schema)}.{quote(name)}"))
                         .scalar())["Plan Rows"])

    def session_setup(self, dbapi_connection: Any) -> None:
        """Every transaction read-only, and ``limits.timeout_ms`` as the server's ``statement_timeout``.

        psycopg2 runs the SETs in a transaction, and a rollback would undo
        them, so they are committed.
        """
        statements = ["SET default_transaction_read_only = on"]
        if self.limits.timeout_ms:
            statements.append(f"SET statement_timeout = {int(self.limits.timeout_ms)}")
        cursor = dbapi_connection.cursor()
        for statement in statements:
            cursor.execute(statement)
        cursor.close()
        dbapi_connection.commit()

    def classify(self, exc: BaseException) -> Optional[str]:
        """The SQLSTATE when there is one, else libpq's message."""
        stage = super().classify(exc)
        if stage is not None:
            return stage
        message = str(getattr(exc, "orig", None) or exc).lower()
        return next((stage for words, stage in _STAGE_BY_MESSAGE if words in message), None)

    def get_dialect(self) -> str:
        # A sqlglot dialect name (SQLAlchemy calls it "postgresql").
        return "postgres"

    def system_schemas(self) -> set[str]:
        return {"pg_catalog", "information_schema"}

    def row_estimates(self, conn: Connection, schema: str) -> Dict[str, int]:
        """``pg_class.reltuples``, which ANALYZE and autovacuum keep. A table never analyzed (-1) has none."""
        rows = conn.execute(text(
            "SELECT c.relname, c.reltuples FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relkind IN ('r', 'p', 'm', 'f') AND c.reltuples >= 0"
        ), {"schema": schema})
        return {name: int(count) for name, count in rows}


def _plan(explained: Any) -> Dict[str, Any]:
    """The top node of ``EXPLAIN (FORMAT JSON)``'s output, which psycopg2 returns parsed or as text."""
    return (json.loads(explained) if isinstance(explained, str) else explained)[0]["Plan"]


def scans(plan: Dict[str, Any]) -> Tuple[List[Tuple[str, str, float]], float]:
    """``(each Seq Scan's (schema, relation, times it runs), the rows the index scans read)``.

    A node runs once per run of its parent, and the inner side of a
    ``Nested Loop`` once per row of its outer side; below a ``Materialize``
    the rows are read once per run of the loop above it.
    """
    seq_scans: List[Tuple[str, str, float]] = []
    rows = 0.0

    def walk(node: Dict[str, Any], times: float, once: float) -> None:
        nonlocal rows
        kind = node.get("Node Type")
        if kind == "Seq Scan":
            seq_scans.append((node["Schema"], node["Relation Name"], times))
        elif kind in _INDEX_SCANS:
            rows += float(node.get("Plan Rows") or 0) * times
        children = node.get("Plans") or []
        if kind == "Nested Loop" and len(children) == 2:
            outer, inner = children
            walk(outer, times, times)
            walk(inner, times * max(1.0, float(outer.get("Plan Rows") or 1)), times)
            return
        for child in children:
            if kind == "Materialize":
                walk(child, once, once)
            else:
                walk(child, times, once)

    walk(plan, 1, 1)
    return seq_scans, rows
