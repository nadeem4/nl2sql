"""The SDK's SQLAlchemy base on real servers: reflect once, catalog estimates, the scope, values, issues.

docs/architecture/phase2.md, section 7, "IndexScope: what gets indexed" and
"Profiling and row estimates".
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import event, text

from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.registry import Registry
from nl2sql.secrets.manager import SecretManager
from nl2sql_sdk import IndexScope

from .servers import datasource

TESTBED = Path(__file__).resolve().parents[4] / "testbed"

# Each sample's (schema, table): the table ``servers.KINDS`` counts. MySQL's schema is its database.
SAMPLE = {"postgres": ("public", "film"), "mysql": ("sakila", "film"), "mssql": ("SalesLT", "Product")}


@pytest.fixture(scope="module")
def wide_schema():
    """The generator module the ``samples`` service ran, for the wide schema's names and kinds."""
    sys.path.insert(0, str(TESTBED))
    try:
        import wide_schema
    finally:
        sys.path.remove(str(TESTBED))
    return wide_schema


@pytest.fixture(scope="module")
def wide(wide_schema):
    return wide_schema.build(7)


@pytest.fixture
def adapter_on(server):
    """``adapter_on(database=None)``: the server's adapter, built as the engine builds it."""
    built = []

    def build(database=None):
        registry = DatasourceRegistry(SecretManager(), Registry.default())
        adapter = registry.register_datasource(datasource(server, database))
        built.append(adapter)
        return adapter

    yield build
    for adapter in built:
        adapter.close()


def _statements(adapter):
    """``(statement, parameters)`` for every statement the adapter's engine sends."""
    seen = []
    event.listen(adapter.engine, "before_cursor_execute",
                 lambda conn, cursor, statement, parameters, *args: seen.append((statement, str(parameters))))
    return seen


def _namespace(server, schema: str) -> str:
    """Wide-schema ``schema`` as the adapter names it: on MySQL a schema is a database."""
    return server.wide_database(schema) if server.name == "mysql" else schema


def test_wide_schema_catalog_snapshot_under_60_seconds(server, adapter_on, wide):
    if server.name != "postgres":
        pytest.skip("The 60-second target is set on PostgreSQL (phase2.md, section 7).")
    adapter = adapter_on("wide")

    start = time.perf_counter()
    snapshot = adapter.fetch_schema_snapshot(IndexScope(profile="catalog"))
    elapsed = time.perf_counter() - start

    got = {(t.table.schema_name, t.table.table_name) for t in snapshot.contract.tables.values()}
    expected = {(s.name, r.name) for s in wide for r in [*s.tables, *s.views]}
    assert expected <= got
    assert len(got) >= 1200
    assert snapshot.issues == []
    assert elapsed < 60, f"a catalog snapshot of the wide schema took {elapsed:.1f} s"


def test_pagila_payment_partitions_fold_into_payment(server, adapter_on):
    # wave_c.md, section 18.3 (C-W6): Pagila's payment is partitioned and its keys sit on the partitions.
    if server.name != "postgres":
        pytest.skip("Pagila's partitioned payment is on the PostgreSQL test bed.")
    adapter = adapter_on()

    snapshot = adapter.fetch_schema_snapshot(IndexScope(profile="catalog"))

    tables = {t.table.table_name: t for t in snapshot.contract.tables.values()}
    assert not [name for name in tables if name.startswith("payment_p")], "the partitions are not tables of their own"
    assert {fk.referred_table.table_name for fk in tables["payment"].foreign_keys} >= {"customer", "rental"}


def test_estimates_come_from_the_catalog(server, adapter_on):
    schema, table = SAMPLE[server.name]
    analyze = {"postgres": f"ANALYZE {table}", "mysql": f"ANALYZE TABLE {table}", "mssql": None}[server.name]
    if analyze:
        engine = server.engine()
        try:
            with engine.begin() as conn:
                conn.execute(text(analyze))
        finally:
            engine.dispose()
    adapter = adapter_on()
    seen = _statements(adapter)

    snapshot = adapter.fetch_schema_snapshot(IndexScope(include_tables=[f"{schema}.{table}"], profile="catalog"))

    [meta] = snapshot.metadata.tables.values()
    assert meta.row_count_source == "catalog"
    assert meta.row_count == pytest.approx(server.kind.sample_rows, rel=0.5)
    assert not [s for s, _ in seen if "count(*)" in s.lower()], "nothing counts rows"


def test_excluded_schema_is_never_queried(server, adapter_on, wide):
    numbered = [_namespace(server, s.name) for s in wide if s.name != "audit"]
    pattern = "wide_s*" if server.name == "mysql" else "*_[0-9][0-9]"
    adapter = adapter_on(server.wide_database("audit"))
    seen = _statements(adapter)

    snapshot = adapter.fetch_schema_snapshot(IndexScope(exclude_schemas=[pattern]))

    schemas = {t.table.schema_name for t in snapshot.contract.tables.values()}
    assert _namespace(server, "audit") in schemas
    assert not schemas & set(numbered)
    offenders = [s for s in seen if any(name in s[0] or name in s[1] for name in numbered)]
    assert not offenders, offenders[:3]


def test_no_values_without_sample_values(server, adapter_on):
    schema, table = SAMPLE[server.name]
    adapter = adapter_on()
    seen = _statements(adapter)
    scope = {"include_tables": [f"{schema}.{table}"]}

    [meta] = adapter.fetch_schema_snapshot(IndexScope(**scope)).metadata.tables.values()

    profiled = [c.statistics for c in meta.columns.values() if c.statistics is not None]
    assert profiled, "stats profiling ran"
    assert all(stats.sample_values == [] for stats in profiled)
    assert not [s for s, _ in seen if "group by" in s.lower() and table.lower() in s.lower()]

    [with_values] = adapter.fetch_schema_snapshot(IndexScope(**scope, sample_values=True)).metadata.tables.values()
    assert any(c.statistics and c.statistics.sample_values for c in with_values.columns.values())


def test_odd_columns_become_issues(server, adapter_on, wide, wide_schema):
    odd_kinds = set(wide_schema.ODD_KINDS)
    filled = [(s, t) for s in wide if s.name != "audit" for t in s.tables if 0 < t.rows < 1_000_000]
    schema, table = max(filled, key=lambda st: len({c.kind for c in st[1].columns if c.kind in odd_kinds}))
    odd = [c.name for c in table.columns if c.kind in odd_kinds]
    assert odd
    adapter = adapter_on(server.wide_database(schema.name))

    snapshot = adapter.fetch_schema_snapshot(
        IndexScope(include_tables=[f"{_namespace(server, schema.name)}.{table.name}"], sample_values=True))

    [meta] = snapshot.metadata.tables.values()
    with_issue = {i.column for i in snapshot.issues if i.stage in ("profile", "values")}
    for name in odd:
        assert meta.columns[name].statistics is not None or name in with_issue, name
