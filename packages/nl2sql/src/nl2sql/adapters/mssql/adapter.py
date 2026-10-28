import math
import re
import xml.etree.ElementTree as ElementTree
from typing import Any, Dict, List, Optional, Tuple

from pydantic import Field, SecretStr
from sqlalchemy import URL, Connection, make_url, text
from sqlalchemy.dialects import mssql
from sqlalchemy.exc import SQLAlchemyError

from nl2sql_sdk import DatasourceCapability, Estimate
from nl2sql_sdk.discovery import Found
from nl2sql_sdk.sqlalchemy import SQLAlchemyAdapter, SQLAlchemyConnection, driver_message

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
_SHOWPLAN = "{http://schemas.microsoft.com/sqlserver/2004/07/showplan}"
# Plan operators that read a whole table or index, and those that seek into one.
_SCANS = {"Table Scan", "Clustered Index Scan", "Index Scan", "Columnstore Index Scan"}
_SEEKS = {"Index Seek", "Clustered Index Seek"}


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
    file_suffixes = ()
    default_ports = (1433,)
    url_schemes = ("mssql", "sqlserver")

    @classmethod
    def connection_for(cls, found: Found) -> Optional[Dict[str, Any]]:
        """The host and port of an open local port, or every field a ``mssql://`` URL carries. Never connects."""
        if found.kind == "port":
            host, _, port = found.value.rpartition(":")
            return {"host": host, "port": int(port)}
        if found.kind != "url":
            return None
        url = make_url(found.value)
        fields = {"host": url.host, "port": url.port, "database": url.database, "user": url.username,
                  "password": url.password}
        return {name: value for name, value in fields.items() if value is not None}

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
        return super().capabilities() | {DatasourceCapability.ENFORCES_TIMEOUT, DatasourceCapability.ESTIMATES_COST}

    def estimate(self, sql: str, parameters: Dict[str, Any]) -> Estimate:
        """The rows ``sql`` reads, from the estimated plan (``SET SHOWPLAN_XML ON``), without running it.

        Each scan or seek counts ``EstimatedRowsRead`` where the server
        reports it; else a scan counts its table's catalog row count
        (``sys.partitions``) and a seek its ``EstimateRows``. Both are per
        execution, so each is multiplied by the times the operator runs
        (``1 + EstimateRebinds + EstimateRewinds``, the inner side of a
        nested loop). ``cost`` is ``StatementSubtreeCost``, or the plan's
        top ``EstimatedTotalSubtreeCost``; None when the plan has neither.

        ``parameters`` are written into the statement as escaped literals
        (``inline_parameters``): sent as parameters, the statement is an RPC
        (``sp_executesql``), for which SHOWPLAN returns no plan. Nothing runs
        while SHOWPLAN is on, and a literal cannot end early, so a value
        cannot turn SHOWPLAN off.

        SHOWPLAN is a session setting, and pyodbc's ODBC pooling hands a
        closed connection's session to the next one, so it is turned off
        again before the connection is closed; the connection is then
        discarded rather than pooled. The login needs the ``SHOWPLAN``
        permission; without it, this raises.
        """
        try:
            with self.engine.connect() as conn:
                try:
                    conn.exec_driver_sql("SET SHOWPLAN_XML ON")
                    result = conn.exec_driver_sql(self.inline_parameters(sql, parameters))
                    plan = result.scalar() if result.returns_rows else None
                finally:
                    try:
                        conn.exec_driver_sql("SET SHOWPLAN_XML OFF")
                    finally:
                        conn.invalidate()
            rows, cost, uncounted = read_showplan(plan)
            if uncounted:
                with self.engine.connect() as conn:
                    for schema in {schema for schema, _, _ in uncounted}:
                        counts = self.row_estimates(conn, schema)
                        rows += math.ceil(sum(counts[name] * times for s, name, times in uncounted if s == schema))
            return Estimate(rows=rows, cost=cost, source="SHOWPLAN_XML and sys.partitions")
        except SQLAlchemyError as exc:
            raise RuntimeError(f"{self} could not estimate the query: {driver_message(exc)}") from None
        except (KeyError, TypeError, ValueError, AttributeError, ElementTree.ParseError) as exc:
            raise RuntimeError(f"{self} could not estimate the query: the plan could not be read "
                               f"({type(exc).__name__})") from None

    def inline_parameters(self, sql: str, parameters: Optional[Dict[str, Any]]) -> str:
        """``sql`` with its ``:name`` parameters written in as SQL Server literals, quotes escaped.

        A value SQLAlchemy cannot write as a literal raises ``CompileError``.
        """
        if not parameters:
            return sql
        statement = text(sql).bindparams(**parameters)
        return str(statement.compile(dialect=mssql.dialect(), compile_kwargs={"literal_binds": True}))

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


def read_showplan(plan: str) -> Tuple[int, Optional[float], List[Tuple[str, str, float]]]:
    """``(rows read, cost, (schema, table, executions) of each scan whose rows the plan does not give)`` of a showplan.

    Raises ``ValueError`` when there is no plan (no estimate is never zero rows), and
    ``ElementTree.ParseError``, ``KeyError`` or ``AttributeError`` on a plan it cannot read.
    """
    if not plan:
        raise ValueError("SQL Server returned no plan")
    root = ElementTree.fromstring(plan)
    if next(root.iter(f"{_SHOWPLAN}StmtSimple"), None) is None:
        raise ValueError("SQL Server returned no plan for a statement")
    costs = []
    for statement in root.iter(f"{_SHOWPLAN}StmtSimple"):
        cost = statement.get("StatementSubtreeCost")
        if cost is None:
            top = statement.find(f"{_SHOWPLAN}QueryPlan/{_SHOWPLAN}RelOp")
            cost = top.get("EstimatedTotalSubtreeCost") if top is not None else None
        if cost is not None:
            costs.append(float(cost))
    rows, uncounted = 0.0, []
    for operator in root.iter(f"{_SHOWPLAN}RelOp"):
        kind = operator.get("PhysicalOp")
        if kind not in _SCANS and kind not in _SEEKS:
            continue
        times = 1 + float(operator.get("EstimateRebinds") or 0) + float(operator.get("EstimateRewinds") or 0)
        if operator.get("EstimatedRowsRead") is not None:
            rows += float(operator.get("EstimatedRowsRead")) * times
        elif kind in _SEEKS:
            rows += float(operator.get("EstimateRows") or 0) * times
        else:
            source = operator.find(f".//{_SHOWPLAN}Object")
            uncounted.append((source.get("Schema").strip("[]"), source.get("Table").strip("[]"), times))
    return math.ceil(rows), (sum(costs) if costs else None), uncounted
