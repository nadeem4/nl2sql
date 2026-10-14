from typing import Any, Dict, Optional

from pydantic import Field, SecretStr
from sqlalchemy import URL, Connection, text
from sqlalchemy.dialects import mysql

from nl2sql_sdk import DatasourceCapability
from nl2sql_sdk.sqlalchemy import SQLAlchemyAdapter, SQLAlchemyConnection

# MySQL's error numbers for a failed connection: the first argument of the driver's error.
_STAGE_BY_ERROR = {
    1045: "auth",          # access denied: wrong user or password
    1049: "database",      # unknown database
    1044: "permission",    # the login may not open this database
    2003: "network",       # cannot connect to the server
    2005: "network",       # unknown host
    2013: "network",       # lost connection while connecting
}


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

    def capabilities(self) -> set[DatasourceCapability]:
        return super().capabilities() | {DatasourceCapability.ENFORCES_READ_ONLY,
                                         DatasourceCapability.ENFORCES_TIMEOUT}

    def session_setup(self, dbapi_connection: Any) -> None:
        """Every transaction read-only, and ``limits.timeout_ms`` as the session's ``MAX_EXECUTION_TIME``.

        ``MAX_EXECUTION_TIME`` stops a ``SELECT``, which is all the engine runs.
        """
        statements = ["SET SESSION TRANSACTION READ ONLY"]
        if self.limits.timeout_ms:
            statements.append(f"SET SESSION MAX_EXECUTION_TIME = {int(self.limits.timeout_ms)}")
        cursor = dbapi_connection.cursor()
        for statement in statements:
            cursor.execute(statement)
        cursor.close()

    def classify(self, exc: BaseException) -> Optional[str]:
        """MySQL's error number, else the SQLSTATE."""
        args = getattr(getattr(exc, "orig", None) or exc, "args", ())
        if args and isinstance(args[0], int) and args[0] in _STAGE_BY_ERROR:
            return _STAGE_BY_ERROR[args[0]]
        return super().classify(exc)

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
