import math
from typing import Dict, Optional

from pydantic import Field, SecretStr
from sqlalchemy import URL, Connection, Engine, event, text

from nl2sql_sdk.sqlalchemy import SQLAlchemyAdapter, SQLAlchemyConnection


class MssqlConnection(SQLAlchemyConnection):
    """A SQL Server datasource's ``connection`` block.

    Give ``user`` and ``password``, or set ``trusted_connection`` to log in
    as the account the engine runs under.
    """

    host: str = Field(title="Host", examples=["db.internal"])
    port: int = Field(1433, title="Port")
    database: str = Field(title="Database")
    user: Optional[str] = Field(None, title="User")
    password: Optional[SecretStr] = Field(None, title="Password")
    driver: str = Field("ODBC Driver 17 for SQL Server", title="ODBC driver",
                        json_schema_extra={"group": "advanced"})
    trusted_connection: bool = Field(False, title="Windows authentication",
                                     json_schema_extra={"group": "advanced"})
    trust_server_certificate: bool = Field(False, title="Trust the server certificate",
                                           json_schema_extra={"group": "advanced"})


class MssqlAdapter(SQLAlchemyAdapter):
    title = "SQL Server"
    connection_model = MssqlConnection
    driver_modules = ("pyodbc",)
    install_hint = 'pip install "nl2sql-engine[mssql]", and the Microsoft ODBC Driver for SQL Server'

    def url(self, connection: MssqlConnection) -> URL:
        query = {"driver": connection.driver}
        if connection.trusted_connection:
            query["Trusted_Connection"] = "yes"
        if connection.trust_server_certificate:
            query["TrustServerCertificate"] = "yes"
        # URL.create escapes the credentials, so a password holding @ : / # %
        # stays a password instead of moving the host or port boundary.
        return URL.create(
            "mssql+pyodbc",
            username=connection.user or None,
            password=connection.password.get_secret_value() if connection.password else None,
            host=connection.host,
            port=connection.port,
            database=connection.database,
            query=query,
        )

    def build_engine(self) -> Engine:
        """The engine, enforcing ``limits.timeout_ms`` through pyodbc.

        SQL Server has no session setting for a statement timeout; the client
        cancels instead. pyodbc's ``Connection.timeout`` is that query timeout,
        in whole seconds, so it is set on every new pooled connection, rounded
        up so a sub-second limit is not read as "no timeout".
        """
        engine = super().build_engine()
        if self.limits.timeout_ms:
            event.listen(engine, "connect", self._set_query_timeout)
        return engine

    def _set_query_timeout(self, dbapi_connection, connection_record) -> None:
        dbapi_connection.timeout = math.ceil(self.limits.timeout_ms / 1000)

    def get_dialect(self) -> str:
        """MSSQL uses T-SQL dialect."""
        # A sqlglot dialect name (SQLAlchemy calls it "mssql").
        return "tsql"

    def system_schemas(self) -> set[str]:
        return {"sys", "INFORMATION_SCHEMA"}

    def row_estimates(self, conn: Connection, schema: str) -> Dict[str, int]:
        """``sys.partitions``: the row count SQL Server keeps for each table's heap or clustered index."""
        rows = conn.execute(text(
            "SELECT o.name, SUM(p.rows) FROM sys.objects o "
            "JOIN sys.schemas s ON s.schema_id = o.schema_id "
            "JOIN sys.partitions p ON p.object_id = o.object_id AND p.index_id IN (0, 1) "
            "WHERE s.name = :schema AND o.type = 'U' GROUP BY o.name"
        ), {"schema": schema})
        return {name: int(count) for name, count in rows}
