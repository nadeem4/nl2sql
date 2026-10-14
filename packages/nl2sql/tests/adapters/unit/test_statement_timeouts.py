"""``limits.timeout_ms`` is enforced, not merely recorded.

SQLAlchemy ignores an unknown ``execution_options["timeout"]``, so the base
adapter must not pretend to set one; each adapter enforces the limit the way
its driver allows.
"""

import time
import types

from sqlalchemy import event
from sqlalchemy.dialects.mssql.pyodbc import MSDialect_pyodbc

from nl2sql_sdk.contracts import AdapterRequest
from nl2sql_sdk import Limits
from nl2sql.adapters.duckdb.adapter import DuckdbAdapter, DuckdbConnection
from nl2sql.adapters.mssql.adapter import MssqlAdapter, MssqlConnection

SLOW_SQL = "SELECT sum(range) FROM range(10000000000000)"


def _fake_pyodbc(monkeypatch):
    """Stand in for pyodbc, which CI does not install: create_engine only needs the module."""
    module = types.SimpleNamespace(paramstyle="qmark", version="5.0.0", Error=Exception,
                                   Cursor=type("Cursor", (), {"nextset": None}))
    monkeypatch.setattr(MSDialect_pyodbc, "import_dbapi", classmethod(lambda cls: module))


def _mssql(timeout_ms):
    return MssqlAdapter("ms", MssqlConnection(host="h", user="u", password="p", database="d"),
                        Limits(timeout_ms=timeout_ms))


def test_mssql_sets_the_pyodbc_query_timeout_on_each_new_connection(monkeypatch):
    # Arrange
    _fake_pyodbc(monkeypatch)
    adapter = _mssql(1500)
    dbapi_connection = types.SimpleNamespace(timeout=0)

    # Act
    adapter._set_query_timeout(dbapi_connection, None)

    # Assert: the hook runs on every new pooled connection, and pyodbc's
    # timeout is whole seconds, rounded up so 1500 ms is not 1 s.
    assert event.contains(adapter.engine, "connect", adapter._set_query_timeout)
    assert dbapi_connection.timeout == 2


def test_mssql_without_a_timeout_registers_no_hook(monkeypatch):
    # Arrange
    _fake_pyodbc(monkeypatch)

    # Act
    adapter = _mssql(None)

    # Assert
    assert not event.contains(adapter.engine, "connect", adapter._set_query_timeout)


def test_base_adapter_does_not_set_the_ignored_timeout_execution_option(monkeypatch):
    # Arrange
    _fake_pyodbc(monkeypatch)

    # Act
    adapter = _mssql(1500)

    # Assert
    assert "timeout" not in adapter.engine.get_execution_options()


def _duckdb(timeout_ms):
    return DuckdbAdapter("duck", DuckdbConnection(database=":memory:"), Limits(timeout_ms=timeout_ms))


def test_duckdb_interrupts_a_query_that_outlives_the_timeout():
    # Arrange
    adapter = _duckdb(200)

    # Act
    start = time.perf_counter()
    result = adapter.execute(AdapterRequest(plan_type="sql", payload={"sql": SLOW_SQL}))
    elapsed = time.perf_counter() - start

    # Assert
    assert result.success is False
    assert result.error.error_code == "QUERY_REFUSED"
    assert result.error.retryable is False
    assert elapsed < 10


def test_duckdb_runs_fast_queries_normally_under_a_timeout():
    # Arrange
    adapter = _duckdb(200)
    adapter.execute(AdapterRequest(plan_type="sql", payload={"sql": SLOW_SQL}))

    # Act: the connection that was interrupted goes back to the pool and is reused.
    first = adapter.execute(AdapterRequest(plan_type="sql", payload={"sql": "SELECT 42 AS x"}))
    time.sleep(0.4)
    second = adapter.execute(AdapterRequest(plan_type="sql", payload={"sql": "SELECT 43 AS x"}))

    # Assert: no timer left over from a finished query interrupts a later one.
    assert first.success is True and first.rows == [[42]]
    assert second.success is True and second.rows == [[43]]
