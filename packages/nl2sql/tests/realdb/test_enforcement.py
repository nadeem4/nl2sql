"""Read-only sessions, statement timeouts and connection stages, proven on each real server (B3).

docs/architecture/phase2.md, section 7, "Read-only and timeouts, honestly" and
"test_connection: which stage failed". The adapters connect with the test
bed's admin login, so a write that fails was stopped by the session, not by
a missing grant.
"""
from __future__ import annotations

import time

import pytest
from sqlalchemy import inspect, text

from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.registry import Registry
from nl2sql.secrets.manager import SecretManager
from nl2sql_sdk import DatasourceCapability
from nl2sql_sdk.contracts import AdapterRequest

from .servers import datasource

PROBE = "nl2sql_b3_probe"

# A query that runs well past one second on each server.
SLOW_SQL = {
    "postgres": "SELECT pg_sleep(5)",
    # MAX_EXECUTION_TIME applies to a SELECT that reads tables; SLEEP alone is not stopped with an error,
    # and a bare COUNT(*) of a cross join is answered quickly. A billion MD5s are not.
    "mysql": ("SELECT COUNT(*) FROM film a CROSS JOIN film b CROSS JOIN film c "
              "WHERE MD5(CONCAT(a.title, b.title, c.title)) LIKE 'zzzz%'"),
    "mssql": "WAITFOR DELAY '00:00:05'",
}


@pytest.fixture
def adapter_for(server):
    """``adapter_for(connection=None, **fields)``: the server's adapter, built as the engine builds it."""
    built = []

    def build(connection=None, **fields):
        source = datasource(server, **fields)
        if connection is not None:
            source = source.model_copy(update={"connection": {**source.connection, **connection}})
        adapter = DatasourceRegistry(SecretManager(), Registry.default()).register_datasource(source)
        built.append(adapter)
        return adapter

    yield build
    for adapter in built:
        adapter.close()


def _sql(sql):
    return AdapterRequest(plan_type="sql", payload={"sql": sql})


@pytest.fixture
def probe(server):
    """An empty table made with a writable admin connection outside the adapter, and dropped after."""
    admin = server.engine()
    with admin.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {PROBE}_made"))
        conn.execute(text(f"DROP TABLE IF EXISTS {PROBE}"))
        conn.execute(text(f"CREATE TABLE {PROBE} (id INTEGER)"))
    yield admin
    with admin.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {PROBE}_made"))
        conn.execute(text(f"DROP TABLE IF EXISTS {PROBE}"))
    admin.dispose()


def test_write_through_execute_fails(server, adapter_for, probe):
    adapter = adapter_for()
    if DatasourceCapability.ENFORCES_READ_ONLY not in adapter.capabilities():
        # Owner decision 4: SQL Server has no session-level read-only, says so, and check() warns.
        pytest.skip(f"{adapter.title} does not declare ENFORCES_READ_ONLY: the login's grants are the only guard")

    inserted = adapter.execute(_sql(f"INSERT INTO {PROBE} (id) VALUES (1)"))
    created = adapter.execute(_sql(f"CREATE TABLE {PROBE}_made (id INTEGER)"))
    with pytest.raises(Exception, match=r"(?i)read.only"):
        adapter.execute_sql(f"INSERT INTO {PROBE} (id) VALUES (2)")

    assert inserted.success is False and inserted.error.retryable is False
    assert created.success is False
    with probe.connect() as conn:
        assert conn.execute(text(f"SELECT COUNT(*) FROM {PROBE}")).scalar() == 0
    assert not inspect(probe).has_table(f"{PROBE}_made")


def test_slow_query_is_stopped(server, adapter_for):
    adapter = adapter_for(limits={"timeout_ms": 1000})
    assert DatasourceCapability.ENFORCES_TIMEOUT in adapter.capabilities()

    start = time.perf_counter()
    result = adapter.execute(_sql(SLOW_SQL[server.name]))
    elapsed = time.perf_counter() - start

    assert result.success is False
    assert result.error.retryable is False
    assert elapsed < 3, f"stopped after {elapsed:.1f}s"
    # The connection the timeout stopped goes back to the pool and still serves.
    assert adapter.execute(_sql("SELECT 1")).success


def test_wrong_password_is_auth(adapter_for):
    check = adapter_for({"password": "not-the-password"}).test_connection()

    assert (check.ok, check.stage) == (False, "auth"), check.message
    assert check.fix and "not-the-password" not in (check.message or "")


def test_closed_port_is_network(adapter_for):
    check = adapter_for({"port": 1}).test_connection()

    assert (check.ok, check.stage) == (False, "network"), check.message
    assert check.fix


def test_missing_database_is_database(adapter_for):
    check = adapter_for({"database": "nl2sql_missing_db"}).test_connection()

    assert (check.ok, check.stage) == (False, "database"), check.message
    assert check.fix
