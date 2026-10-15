import math
import re
from typing import Any, Dict, Optional

from pydantic import Field, SecretStr
from sqlalchemy import URL, Connection, text

from nl2sql_sdk import DatasourceCapability
from nl2sql_sdk.sqlalchemy import SQLAlchemyAdapter, SQLAlchemyConnection

# SQL Server's error numbers for a failed connection, as the ODBC message
# carries them: "... (4060)". Checked in this order.
_STAGE_BY_ERROR = (
    (4060, "database"),    # cannot open the database requested by the login
    (18456, "auth"),       # login failed
    (916, "permission"),   # the login cannot access the database
)
# ODBC's SQLSTATEs for a server that was not reached: the first argument of pyodbc's error.
_NETWORK_SQLSTATES = {"08001", "08S01", "HYT00"}
# Microsoft's ODBC drivers for SQL Server, as pyodbc.drivers() names them: "ODBC Driver 18 for SQL Server".
_SQL_SERVER_DRIVER = re.compile(r"ODBC Driver (\d+) for SQL Server")


class OdbcDriverMissing(ImportError):
    """No Microsoft ODBC Driver for SQL Server is installed: the ``driver`` stage."""


def newest_sql_server_driver() -> str:
    """The newest "ODBC Driver N for SQL Server" that pyodbc can see."""
    import pyodbc

    versions = {int(m.group(1)): name for name in pyodbc.drivers() if (m := _SQL_SERVER_DRIVER.fullmatch(name))}
    if not versions:
        raise OdbcDriverMissing("No Microsoft ODBC Driver for SQL Server is installed.")
    return versions[max(versions)]


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
    driver: Optional[str] = Field(None, title="ODBC driver",
                                  description="Unset: the newest ODBC Driver for SQL Server installed",
                                  json_schema_extra={"group": "advanced"})
    trusted_connection: bool = Field(False, title="Windows authentication",
                                     json_schema_extra={"group": "advanced"})
    encrypt: bool = Field(True, title="Encrypt the connection",
                          description="Off: a local or test server without TLS",
                          json_schema_extra={"group": "advanced"})
    trust_server_certificate: bool = Field(False, title="Trust the server certificate",
                                           json_schema_extra={"group": "advanced"})


class MssqlAdapter(SQLAlchemyAdapter):
    title = "SQL Server"
    connection_model = MssqlConnection
    driver_modules = ("pyodbc",)
    install_hint = ('pip install "nl2sql-engine[mssql]", and Microsoft\'s ODBC Driver for SQL Server: '
                    'https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server')

    def url(self, connection: MssqlConnection) -> URL:
        """The connection's ``driver``, else the newest one installed, encrypting unless ``encrypt`` is off.

        Driver 18 encrypts unless told not to and Driver 17 only when told
        to, so ``Encrypt`` is always sent (``yes`` or ``no``) to make both
        behave alike, and ``trust_server_certificate`` means the same
        whichever is used.
        """
        query = {"driver": connection.driver or newest_sql_server_driver(),
                 "Encrypt": "yes" if connection.encrypt else "no"}
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

    def capabilities(self) -> set[DatasourceCapability]:
        """The timeout only: SQL Server cannot make a session read-only.

        ``ApplicationIntent=ReadOnly`` only routes to a readable replica, so
        the guard is a login with only ``db_datareader``, and ``check()``
        warns about every SQL Server datasource.
        """
        return super().capabilities() | {DatasourceCapability.ENFORCES_TIMEOUT}

    def session_setup(self, dbapi_connection: Any) -> None:
        """``limits.timeout_ms`` as pyodbc's query timeout: the client cancels.

        SQL Server has no session setting for a statement timeout. pyodbc's
        ``Connection.timeout`` counts whole seconds, so the limit is rounded
        up and a sub-second one is not read as "no timeout".
        """
        if self.limits.timeout_ms:
            dbapi_connection.timeout = math.ceil(self.limits.timeout_ms / 1000)

    def classify(self, exc: BaseException) -> Optional[str]:
        """SQL Server's error number in the message, else the ODBC SQLSTATE.

        A missing database also says the login failed, so 4060 is read first.
        """
        orig = getattr(exc, "orig", None) or exc
        message = str(orig)
        for number, stage in _STAGE_BY_ERROR:
            if f"({number})" in message:
                return stage
        args = getattr(orig, "args", ())
        if args and isinstance(args[0], str) and args[0] in _NETWORK_SQLSTATES:
            return "network"
        return super().classify(exc)

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
