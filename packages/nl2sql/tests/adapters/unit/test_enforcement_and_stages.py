"""Honest enforcement and stages (B3): read-only sessions, statement timeouts, and which stage a connection failed at.

docs/architecture/phase2.md, section 7, "Read-only and timeouts, honestly" and
"test_connection: which stage failed". The same behaviour on the real servers
is in ``tests/realdb/test_enforcement.py``.
"""
from __future__ import annotations

import sqlite3
import time
import types

import duckdb
import pytest
from sqlalchemy import exc as sa_exc
from sqlalchemy import text

from nl2sql.adapters.duckdb.adapter import DuckdbAdapter, DuckdbConnection
from nl2sql.adapters.mssql.adapter import MssqlAdapter, MssqlConnection
from nl2sql.adapters.mysql.adapter import MysqlAdapter, MysqlConnection
from nl2sql.adapters.postgres.adapter import PostgresAdapter, PostgresConnection
from nl2sql.adapters.sqlite.adapter import SqliteAdapter, SqliteConnection
from nl2sql_sdk import DatasourceCapability, Limits
from nl2sql_sdk.contracts import AdapterRequest

READ_ONLY = DatasourceCapability.ENFORCES_READ_ONLY
TIMEOUT = DatasourceCapability.ENFORCES_TIMEOUT

# Counts to a billion one row at a time: minutes on SQLite.
SQLITE_SLOW_SQL = ("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 1000000000) "
                   "SELECT COUNT(*) FROM n")


def _postgres(**limits):
    return PostgresAdapter("pg", PostgresConnection(host="h", database="d", user="u", password="p"),
                           Limits(**limits))


def _mysql(**limits):
    return MysqlAdapter("my", MysqlConnection(host="h", database="d", user="u", password="p"), Limits(**limits))


def _mssql(**limits):
    return MssqlAdapter("ms", MssqlConnection(host="h", database="d", user="u", password="p"), Limits(**limits))


class FakeDbapiConnection:
    """Records what a ``session_setup`` sends, as a DBAPI connection would receive it."""

    def __init__(self):
        self.statements = []
        self.commits = 0
        self.timeout = 0

    def cursor(self):
        connection = self

        class Cursor:
            def execute(self, statement):
                connection.statements.append(statement)

            def close(self):
                pass

        return Cursor()

    def commit(self):
        self.commits += 1


@pytest.fixture
def shop(tmp_path):
    path = tmp_path / "shop.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
        con.execute("INSERT INTO customers VALUES (1, 'Ada')")
    return path


def _sql(sql):
    return AdapterRequest(plan_type="sql", payload={"sql": sql})


# --------------------------------------------------------------- capabilities


@pytest.mark.parametrize("adapter, declared", [
    (_postgres(), {READ_ONLY, TIMEOUT}),
    (_mysql(), {READ_ONLY, TIMEOUT}),
    # SQL Server has no session-level read-only: ApplicationIntent=ReadOnly only routes to a replica.
    (_mssql(), {TIMEOUT}),
    (SqliteAdapter("lite", SqliteConnection(database="x.sqlite"), Limits()), {READ_ONLY, TIMEOUT}),
    (DuckdbAdapter("duck", DuckdbConnection(database="x.duckdb"), Limits()), {READ_ONLY, TIMEOUT}),
], ids=["postgres", "mysql", "mssql", "sqlite", "duckdb"])
def test_each_adapter_declares_only_what_it_enforces(adapter, declared):
    assert adapter.capabilities() & {READ_ONLY, TIMEOUT} == declared


def test_an_in_memory_duckdb_does_not_claim_read_only():
    # DuckDB cannot open an in-memory database read-only, and there is nothing in it to protect.
    adapter = DuckdbAdapter("duck", DuckdbConnection(database=":memory:"), Limits())

    assert READ_ONLY not in adapter.capabilities()


# --------------------------------------------------------------- session_setup


def test_postgres_session_is_read_only_with_the_statement_timeout():
    connection = FakeDbapiConnection()

    _postgres(timeout_ms=1500).session_setup(connection)

    assert connection.statements == ["SET default_transaction_read_only = on", "SET statement_timeout = 1500"]
    # psycopg2 opens a transaction for the SETs; a rollback would undo them, so they are committed.
    assert connection.commits == 1


def test_postgres_session_without_a_timeout_is_still_read_only():
    connection = FakeDbapiConnection()

    _postgres().session_setup(connection)

    assert connection.statements == ["SET default_transaction_read_only = on"]


def test_mysql_session_is_read_only_with_the_execution_time_limit():
    connection = FakeDbapiConnection()

    _mysql(timeout_ms=1500).session_setup(connection)

    assert connection.statements == ["SET SESSION TRANSACTION READ ONLY", "SET SESSION MAX_EXECUTION_TIME = 1500"]


def test_mssql_session_sets_only_the_query_timeout_rounded_up():
    connection = FakeDbapiConnection()

    _mssql(timeout_ms=1500).session_setup(connection)

    assert connection.statements == []
    assert connection.timeout == 2


def test_session_setup_runs_on_every_new_connection(shop):
    seen = []

    class Recording(SqliteAdapter):
        def session_setup(self, dbapi_connection):
            seen.append(dbapi_connection)

    adapter = Recording("lite", SqliteConnection(database=shop.as_posix()), Limits())
    with adapter.engine.connect() as first, adapter.engine.connect() as second:
        first.execute(text("SELECT 1"))
        second.execute(text("SELECT 1"))

    assert len(seen) == 2


# --------------------------------------------------------------- SQLite and DuckDB


def test_sqlite_slow_query_is_stopped(shop):
    adapter = SqliteAdapter("lite", SqliteConnection(database=shop.as_posix()), Limits(timeout_ms=300))

    start = time.perf_counter()
    result = adapter.execute(_sql(SQLITE_SLOW_SQL))
    elapsed = time.perf_counter() - start

    assert result.success is False
    assert result.error.retryable is False
    assert elapsed < 5
    # The deadline is per statement: the next one on the same connection runs.
    assert adapter.execute(_sql("SELECT name FROM customers")).rows == [["Ada"]]


def test_sqlite_fast_query_under_a_timeout_is_not_stopped_later(shop):
    adapter = SqliteAdapter("lite", SqliteConnection(database=shop.as_posix()), Limits(timeout_ms=200))
    assert adapter.execute(_sql("SELECT 1")).success

    time.sleep(0.3)

    assert adapter.execute(_sql("SELECT name FROM customers")).rows == [["Ada"]]


def test_duckdb_file_is_opened_read_only(tmp_path):
    path = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE t AS SELECT 1 AS x")
    adapter = DuckdbAdapter("duck", DuckdbConnection(database=path.as_posix()), Limits())

    reads = adapter.execute(_sql("SELECT x FROM t"))
    writes = adapter.execute(_sql("CREATE TABLE u (y INTEGER)"))
    adapter.close()

    assert reads.rows == [[1]]
    assert writes.success is False
    with duckdb.connect(str(path)) as con:
        assert con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'u'").fetchone() == (0,)


# --------------------------------------------------------------- stages


def _wrapped(cls, orig):
    return cls("SELECT 1", {}, orig)


class _PgError(Exception):
    pgcode = None


class _Sqlstate(Exception):
    def __init__(self, message, sqlstate):
        super().__init__(message)
        self.sqlstate = sqlstate


# SQLSTATE -> the stage the base reads from it.
SQLSTATE_STAGES = {
    "08006": "network",
    "28000": "auth",
    "3D000": "database",
    "42501": "permission",
    "42601": None,
}


@pytest.mark.parametrize("sqlstate", sorted(SQLSTATE_STAGES))
def test_the_base_reads_the_sqlstate_class(sqlstate):
    stage = SQLSTATE_STAGES[sqlstate]
    adapter = SqliteAdapter("lite", SqliteConnection(database="x"), Limits())

    assert adapter.classify(_wrapped(sa_exc.OperationalError, _Sqlstate("boom", sqlstate))) == stage


def test_a_missing_driver_module_is_the_driver_stage(monkeypatch):
    adapter = _postgres()

    def no_driver(**options):
        raise ModuleNotFoundError("No module named 'psycopg2'", name="psycopg2")

    monkeypatch.setattr(adapter, "build_engine", no_driver)
    check = adapter.test_connection()

    assert check.ok is False
    assert check.stage == "driver"
    assert check.fix == adapter.install_hint


@pytest.mark.parametrize("message, stage", [
    ("FATAL:  password authentication failed", "auth"),
    ('FATAL:  database "d" does not exist', "database"),
    ("connection failed: Connection refused", "network"),
    ("could not translate host name to address: Name or service not known", "network"),
    ("connection failed: timeout expired", "network"),
    ('FATAL:  permission denied for database "d"', "permission"),
])
def test_postgres_connection_failures_carry_no_sqlstate_so_the_message_decides(message, stage):
    assert _postgres().classify(_wrapped(sa_exc.OperationalError, _PgError(message))) == stage


@pytest.mark.parametrize("code, stage", [
    (1045, "auth"), (1049, "database"), (1044, "permission"), (2003, "network"), (2005, "network"), (1064, None),
])
def test_mysql_error_numbers_decide(code, stage):
    orig = Exception(code, "the server's message")

    assert _mysql().classify(_wrapped(sa_exc.OperationalError, orig)) == stage


@pytest.mark.parametrize("args, stage", [
    (("28000", "[28000] Login failed. (18456) (SQLDriverConnect)"), "auth"),
    # A missing database also says the login failed: 4060 decides first.
    (("42000", "[42000] Cannot open database requested by the login. The login failed. (4060); "
               "[28000] Login failed. (18456)"), "database"),
    (("08001", "[08001] [Microsoft][ODBC Driver 18 for SQL Server]TCP Provider: No connection could be made "
               "(10061) (SQLDriverConnect)"), "network"),
    (("HYT00", "[HYT00] [Microsoft][ODBC Driver 18 for SQL Server]Login timeout expired (0) (SQLDriverConnect)"),
     "network"),
])
def test_mssql_native_errors_decide(args, stage):
    assert _mssql().classify(_wrapped(sa_exc.OperationalError, Exception(*args))) == stage


def test_a_missing_sqlite_file_is_the_database_stage(tmp_path):
    adapter = SqliteAdapter("lite", SqliteConnection(database=(tmp_path / "missing.sqlite").as_posix()), Limits())

    check = adapter.test_connection()

    assert check.ok is False
    assert check.stage == "database"
    assert check.fix


def test_a_good_connection_has_no_stage(shop):
    check = SqliteAdapter("lite", SqliteConnection(database=shop.as_posix()), Limits()).test_connection()

    assert check.ok is True and check.stage is None and check.server_version


def test_a_connection_whose_catalog_cannot_be_read_is_the_permission_stage(shop, monkeypatch):
    import nl2sql_sdk.sqlalchemy.adapter as base

    adapter = SqliteAdapter("lite", SqliteConnection(database=shop.as_posix()), Limits())

    def refuse(conn):
        raise sa_exc.ProgrammingError("SELECT", {}, _Sqlstate("permission denied for schema x", "42501"))

    monkeypatch.setattr(base, "_read_catalog", refuse)
    check = adapter.test_connection()

    assert check.ok is False and check.stage == "permission" and check.fix
