from typing import Dict, Literal

from pydantic import Field, SecretStr
from sqlalchemy import URL, Connection, Engine, text

from nl2sql_sdk.sqlalchemy import SQLAlchemyAdapter, SQLAlchemyConnection


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

    def build_engine(self) -> Engine:
        """The engine, with ``limits.timeout_ms`` as the server's ``statement_timeout``."""
        connect_args = {}
        if self.limits.timeout_ms:
            connect_args["options"] = f"-c statement_timeout={self.limits.timeout_ms}"
        return super().build_engine(connect_args=connect_args)

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
