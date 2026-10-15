"""``estimate`` on PostgreSQL, MySQL and SQL Server in the test bed: rows read, not rows returned (C1).

docs/architecture/wave_b.md, section 9, "The adapter method" (D52). The kit's
checks run in ``test_conformance.py``; these use the samples' larger tables.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.registry import Registry
from nl2sql.secrets.manager import SecretManager
from nl2sql_sdk import DatasourceCapability

from .servers import READER, datasource

# Per server: a large table of its sample, and a filter on an unindexed column that matches no row.
LARGE = {
    "postgres": ("public.rental", "return_date < '1900-01-01'"),
    "mysql": ("rental", "return_date < '1900-01-01'"),
    "mssql": ("SalesLT.Customer", "ModifiedDate < '1900-01-01'"),
}


@pytest.fixture
def adapter_for(server):
    """``adapter_for(**connection)``: the server's adapter, built as the engine builds it, with connection changes."""
    built = []

    def build(**connection):
        source = datasource(server)
        source = source.model_copy(update={"connection": {**source.connection, **connection}})
        adapter = DatasourceRegistry(SecretManager(), Registry.default()).register_datasource(source)
        built.append(adapter)
        return adapter

    yield build
    for adapter in built:
        adapter.close()


def _admin(server, sql):
    engine = server.engine()
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar()
    finally:
        engine.dispose()


def test_estimate_counts_rows_read_not_returned(server, adapter_for):
    table, where = LARGE[server.name]
    total = _admin(server, f"SELECT COUNT(*) FROM {table}")
    adapter = adapter_for()
    assert DatasourceCapability.ESTIMATES_COST in adapter.capabilities()

    sql = f"SELECT * FROM {table} WHERE {where}"
    assert _admin(server, f"SELECT COUNT(*) FROM {table} WHERE {where}") == 0
    estimate = adapter.estimate(sql, {})

    assert estimate.rows is not None and estimate.rows >= total / 2, (estimate, total)
    assert estimate.cost is not None and estimate.cost > 0
    assert estimate.source


def test_estimate_needs_no_write(server, adapter_for):
    """The reader login, which may only read the sample, can estimate (SQL Server's also needs SHOWPLAN)."""
    table, where = LARGE[server.name]
    if server.name == "mssql":
        _grant_showplan(server, True)
    try:
        estimate = adapter_for(user=READER).estimate(f"SELECT * FROM {table} WHERE {where}", {})
    finally:
        if server.name == "mssql":
            _grant_showplan(server, False)
    assert estimate.rows is not None and estimate.rows > 0


def test_mssql_estimate_without_showplan_raises(server, adapter_for):
    if server.name != "mssql":
        pytest.skip("SHOWPLAN is SQL Server's permission")
    table, where = LARGE[server.name]
    with pytest.raises(RuntimeError, match="(?i)showplan"):
        adapter_for(user=READER).estimate(f"SELECT * FROM {table} WHERE {where}", {})


def _grant_showplan(server, grant: bool):
    engine = server.engine()
    try:
        with engine.begin() as conn:
            conn.execute(text(f"{'GRANT' if grant else 'REVOKE'} SHOWPLAN {'TO' if grant else 'FROM'} {READER}"))
    finally:
        engine.dispose()
