from typing import Dict

from pydantic import Field, SecretStr
from sqlalchemy import URL, Connection, Engine, text
from sqlalchemy.dialects import mysql

from nl2sql_sdk.sqlalchemy import SQLAlchemyAdapter, SQLAlchemyConnection


class MysqlConnection(SQLAlchemyConnection):
    """A MySQL datasource's ``connection`` block."""

    host: str = Field(title="Host", examples=["db.internal"])
    port: int = Field(3306, title="Port")
    database: str = Field(title="Database")
    user: str = Field(title="User")
    password: SecretStr = Field(title="Password")


class MysqlAdapter(SQLAlchemyAdapter):
    title = "MySQL"
    connection_model = MysqlConnection
    driver_modules = ("pymysql",)
    install_hint = 'pip install "nl2sql-engine[mysql]"'

    def url(self, connection: MysqlConnection) -> URL:
        # URL.create escapes the credentials, so a password holding @ : / # %
        # stays a password instead of moving the host or port boundary.
        return URL.create(
            "mysql+pymysql",
            username=connection.user,
            password=connection.password.get_secret_value() or None,
            host=connection.host,
            port=connection.port,
            database=connection.database,
        )

    def build_engine(self) -> Engine:
        """The engine, with ``limits.timeout_ms`` as the session's ``MAX_EXECUTION_TIME`` (server-side)."""
        connect_args = {}
        if self.limits.timeout_ms:
            connect_args["init_command"] = f"SET MAX_EXECUTION_TIME={self.limits.timeout_ms}"
        return super().build_engine(connect_args=connect_args)

    def get_dialect(self) -> str:
        return mysql.dialect.name

    def system_schemas(self) -> set[str]:
        return {"mysql", "information_schema", "performance_schema", "sys"}

    def row_estimates(self, conn: Connection, schema: str) -> Dict[str, int]:
        """``information_schema.tables.table_rows``: InnoDB's estimate, refreshed by ANALYZE TABLE."""
        rows = conn.execute(text(
            "SELECT table_name, table_rows FROM information_schema.tables "
            "WHERE table_schema = :schema AND table_type = 'BASE TABLE' AND table_rows IS NOT NULL"
        ), {"schema": schema})
        return {name: int(count) for name, count in rows}
