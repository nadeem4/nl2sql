import json
from typing import Any, Dict, Iterator, List, Literal, Optional, Tuple

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

        A ``Seq Scan`` counts its relation's ``pg_class.reltuples``, not its
        ``Plan Rows``, which is what is left after the filter; an index scan
        counts its ``Plan Rows``. A relation never analyzed (no
        ``reltuples``) counts the planner's estimate of a scan of the whole
        relation, which it makes from the relation's size on disk. ``cost``
        is the plan's ``Total Cost``. Runs read-only, under ``statement_timeout``.
        """
        try:
            with self.engine.connect() as conn:
                plan = _plan(self._run(conn, f"EXPLAIN (FORMAT JSON, VERBOSE) {sql}", parameters).scalar())
                seq_scans, rows = scans(plan)
                sizes: Dict[Tuple[str, str], int] = {}
                for schema in {schema for schema, _ in seq_scans}:
                    sizes.update({(schema, name): n for name, n in self.row_estimates(conn, schema).items()})
                for relation in seq_scans:
                    rows += sizes[relation] if relation in sizes else self._planned_size(conn, relation)
        except SQLAlchemyError as exc:
            raise RuntimeError(f"{self} could not estimate the query: {driver_message(exc)}") from None
        return Estimate(rows=rows, cost=float(plan.get("Total Cost") or 0),
                        source="EXPLAIN (FORMAT JSON) and pg_class.reltuples")

    def _planned_size(self, conn: Connection, relation: Tuple[str, str]) -> int:
        """The planner's estimate of a relation's rows: ``Plan Rows`` of an unfiltered scan of it."""
        quote = conn.dialect.identifier_preparer.quote
        name = f"{quote(relation[0])}.{quote(relation[1])}"
        return int(_plan(conn.execute(text(f"EXPLAIN (FORMAT JSON) SELECT * FROM {name}")).scalar())["Plan Rows"])

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


def _nodes(plan: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    yield plan
    for child in plan.get("Plans") or []:
        yield from _nodes(child)


def scans(plan: Dict[str, Any]) -> Tuple[List[Tuple[str, str]], int]:
    """``(the (schema, relation) of each Seq Scan, the Plan Rows of the index scans added up)``."""
    seq_scans, rows = [], 0
    for node in _nodes(plan):
        if node.get("Node Type") == "Seq Scan":
            seq_scans.append((node["Schema"], node["Relation Name"]))
        elif node.get("Node Type") in _INDEX_SCANS:
            rows += int(node.get("Plan Rows") or 0)
    return seq_scans, rows
