import json
from typing import Any, Dict, Iterator, Optional

from pydantic import Field, SecretStr
from sqlalchemy import URL, Connection, make_url, text
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import SQLAlchemyError

from nl2sql_sdk import DatasourceCapability, Estimate
from nl2sql_sdk.discovery import Found
from nl2sql_sdk.sqlalchemy import SQLAlchemyAdapter, SQLAlchemyConnection, driver_message

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
    # cryptography: PyMySQL needs it for MySQL 8's default login,
    # caching_sha2_password, over a connection without TLS.
    driver_modules = ("pymysql", "cryptography")
    install_hint = 'pip install "nl2sql-engine[mysql]"'
    file_suffixes = ()
    default_ports = (3306,)
    url_schemes = ("mysql", "mariadb")

    @classmethod
    def connection_for(cls, found: Found) -> Optional[Dict[str, Any]]:
        """The host and port of an open local port, or every field a ``mysql://`` URL carries. Never connects."""
        if found.kind == "port":
            host, _, port = found.value.rpartition(":")
            return {"host": host, "port": int(port)}
        if found.kind != "url":
            return None
        url = make_url(found.value)
        fields = {"host": url.host, "port": url.port, "database": url.database, "user": url.username,
                  "password": url.password}
        return {name: value for name, value in fields.items() if value is not None}

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
                                         DatasourceCapability.ENFORCES_TIMEOUT,
                                         DatasourceCapability.ESTIMATES_COST}

    def estimate(self, sql: str, parameters: Dict[str, Any]) -> Estimate:
        """The rows ``sql`` reads, from ``EXPLAIN FORMAT=JSON``, without running it.

        Each table access counts ``rows_examined_per_scan``, MySQL's estimate
        of the rows one scan reads (a full scan, ``access_type: ALL``, counts
        the table), times the scans it runs: in a ``nested_loop``, once per
        row the tables before it produce (``rows_produced_per_join``), and
        once when it is joined through a join buffer (a hash join). A
        materialized derived table is not counted; what it reads is. A
        dependent subquery counts one execution. ``cost`` is the query
        block's ``query_cost``. Runs read-only, under ``MAX_EXECUTION_TIME``.
        """
        try:
            with self.engine.connect() as conn:
                plan = json.loads(self._run(conn, f"EXPLAIN FORMAT=JSON {sql}", parameters).scalar())
            cost = (plan.get("query_block", {}).get("cost_info") or {}).get("query_cost")
            return Estimate(rows=rows_examined(plan), cost=float(cost) if cost is not None else None,
                            source="EXPLAIN FORMAT=JSON")
        except SQLAlchemyError as exc:
            raise RuntimeError(f"{self} could not estimate the query: {driver_message(exc)}") from None
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise RuntimeError(f"{self} could not estimate the query: the plan could not be read "
                               f"({type(exc).__name__})") from None

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
        """MySQL's error number, else the SQLSTATE.

        PyMySQL without ``cryptography`` cannot answer MySQL 8's default
        login; that is a missing piece of the driver, not a wrong password.
        """
        orig = getattr(exc, "orig", None) or exc
        if "'cryptography' package is required" in str(orig):
            return "driver"
        args = getattr(orig, "args", ())
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


def _examined(node: Any, times: float) -> Iterator[float]:
    """The rows read under ``node``, each access's ``rows_examined_per_scan`` times the scans it runs."""
    if isinstance(node, list):
        for value in node:
            yield from _examined(value, times)
        return
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if key == "nested_loop" and isinstance(value, list):
            produced = None  # rows the join has produced before this table, once there is a table before it
            for step in value:
                accessed = step.get("table", {}) if isinstance(step, dict) else {}
                scans = 1.0 if produced is None or accessed.get("using_join_buffer") else produced
                yield from _examined(step, times * scans)
                produced = float(accessed.get("rows_produced_per_join") or produced or 1)
        elif key == "table" and isinstance(value, dict):
            # A derived table (materialized_from_subquery) or a union's temporary table holds rows
            # already counted where they were read.
            if ("rows_examined_per_scan" in value and "materialized_from_subquery" not in value
                    and not str(value.get("table_name", "")).startswith("<")):
                yield float(value["rows_examined_per_scan"]) * times
            for inner_key, inner in value.items():
                yield from _examined(inner, 1.0 if inner_key == "materialized_from_subquery" else times)
        else:
            yield from _examined(value, times)


def rows_examined(plan: Dict[str, Any]) -> int:
    """The rows an ``EXPLAIN FORMAT=JSON`` plan reads, added up over its table accesses."""
    return int(sum(_examined(plan, 1.0)))
