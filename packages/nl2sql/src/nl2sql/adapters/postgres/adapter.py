from typing import Any, Dict, Literal, Optional

from pydantic import Field, SecretStr
from sqlalchemy import URL, Connection, text

from nl2sql_sdk import DatasourceCapability
from nl2sql_sdk.sqlalchemy import SQLAlchemyAdapter, SQLAlchemyConnection

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
                                         DatasourceCapability.ENFORCES_TIMEOUT}

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
