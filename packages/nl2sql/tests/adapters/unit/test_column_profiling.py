"""One column that cannot be profiled no longer fails the whole index.

Every column got MIN/MAX/COUNT DISTINCT with no error handling, so ``MIN`` on a
Postgres boolean or uuid (or a SQL Server bit) raised, and on Postgres the
failed statement aborted the snapshot's transaction, failing every query after
it. Each column is now profiled on its own: a failure rolls the read-only
transaction back, the column keeps no statistics, and the snapshot goes on.
Types with no ordering skip MIN/MAX up front.
"""
import datetime
import decimal
import logging
import os
import sqlite3
import uuid

import pytest
from sqlalchemy import Boolean, Date, Integer, JSON, LargeBinary, Numeric, String, Uuid, event, text
from sqlalchemy.dialects import mssql, postgresql

from nl2sql.adapters.sqlalchemy_base.adapter import BaseSQLAlchemyAdapter, as_scalar, min_max_skip_reason
from nl2sql.adapters.sqlite.adapter import SqliteAdapter, SqliteConnection
from nl2sql_sdk import IndexScope, Limits


def _sqlite(path):
    return SqliteAdapter("t", SqliteConnection(database=path.as_posix()), Limits())


def _failing_on(adapter, monkeypatch, bad_column):
    """Makes profiling ``bad_column`` run a statement the database rejects."""
    original = BaseSQLAlchemyAdapter._get_column_stats

    def stats(self, conn, table_ref, column_name, *args, **kwargs):
        if column_name == bad_column:
            conn.execute(text("SELECT * FROM no_such_table_for_profiling"))
        return original(self, conn, table_ref, column_name, *args, **kwargs)

    monkeypatch.setattr(type(adapter), "_get_column_stats", stats)


@pytest.mark.parametrize("sa_type", [
    Boolean(), mssql.BIT(), Uuid(), postgresql.UUID(), mssql.UNIQUEIDENTIFIER(),
    JSON(), postgresql.JSONB(), LargeBinary(), postgresql.BYTEA(), postgresql.ARRAY(Integer()),
])
def test_types_without_an_ordering_skip_min_and_max(sa_type):
    assert min_max_skip_reason(sa_type)


@pytest.mark.parametrize("sa_type", [Integer(), Numeric(10, 2), String(20), Date()])
def test_ordered_types_keep_min_and_max(sa_type):
    assert min_max_skip_reason(sa_type) is None


@pytest.mark.parametrize("value, expected", [
    (datetime.date(2024, 1, 31), "2024-01-31"),
    (datetime.datetime(2024, 1, 31, 8, 30), "2024-01-31T08:30:00"),
    (datetime.time(8, 30), "08:30:00"),
    (decimal.Decimal("12.50"), 12.5),
    (uuid.UUID(int=1), "00000000-0000-0000-0000-000000000001"),
    (7, 7), ("a", "a"), (None, None),
])
def test_min_and_max_values_become_scalars(value, expected):
    # A Postgres or SQL Server date column returns datetime.date, which the
    # statistics model (str, int, float, bool) rejected, failing the index.
    assert as_scalar(value) == expected


def test_a_boolean_column_has_counts_but_no_min_or_max(tmp_path):
    path = tmp_path / "flags.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE T (id INTEGER PRIMARY KEY, active BOOLEAN)")
        con.executemany("INSERT INTO T VALUES (?, ?)", [(1, 1), (2, 0), (3, None)])

    [table] = _sqlite(path).fetch_schema_snapshot(IndexScope()).metadata.tables.values()

    active = table.columns["active"].statistics
    assert active.distinct_count == 2
    assert active.min_value is None and active.max_value is None
    assert table.columns["id"].statistics.max_value == 3


def test_a_column_whose_stats_fail_does_not_stop_the_snapshot(tmp_path, monkeypatch, caplog):
    path = tmp_path / "odd.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE A (id INTEGER PRIMARY KEY, odd TEXT, later INTEGER)")
        con.executemany("INSERT INTO A VALUES (?, ?, ?)", [(1, "x", 10), (2, "y", 20)])
        con.execute("CREATE TABLE B (id INTEGER PRIMARY KEY, n INTEGER)")
        con.executemany("INSERT INTO B VALUES (?, ?)", [(1, 5), (2, 7)])
    adapter = _sqlite(path)
    _failing_on(adapter, monkeypatch, "odd")
    events = []
    event.listen(adapter.engine, "rollback", lambda conn: events.append("ROLLBACK"))
    event.listen(adapter.engine, "before_cursor_execute",
                 lambda conn, cursor, statement, *args: events.append(statement))

    with caplog.at_level(logging.WARNING):
        snapshot = adapter.fetch_schema_snapshot(IndexScope())

    failed_at = next(i for i, e in enumerate(events) if "no_such_table_for_profiling" in e)
    assert events[failed_at + 1] == "ROLLBACK", "the next statement ran in the failed transaction"

    tables = {m.table.table_name: m for m in snapshot.metadata.tables.values()}
    a, b = tables["A"], tables["B"]
    assert a.columns["odd"].statistics is None
    assert a.columns["later"].statistics.min_value == 10
    assert a.columns["later"].statistics.max_value == 20
    assert b.columns["n"].statistics.max_value == 7
    assert b.row_count == 2
    [summary] = [r for r in caplog.records if "odd" in r.getMessage()]
    assert "[main].[A]" in summary.getMessage()
    assert "no_such_table_for_profiling" in summary.getMessage()


def test_a_failed_row_count_does_not_stop_the_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "count.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE A (id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE B (id INTEGER PRIMARY KEY, n INTEGER)")
        con.executemany("INSERT INTO B VALUES (?, ?)", [(1, 5), (2, 7)])
    adapter = _sqlite(path)
    original = BaseSQLAlchemyAdapter._get_row_count

    def row_count(self, conn, table_ref):
        if table_ref.table_name == "A":
            conn.execute(text("SELECT * FROM no_such_table_for_counting"))
        return original(self, conn, table_ref)

    monkeypatch.setattr(type(adapter), "_get_row_count", row_count)

    tables = {m.table.table_name: m for m in adapter.fetch_schema_snapshot(IndexScope()).metadata.tables.values()}

    assert tables["B"].row_count == 2
    assert tables["B"].columns["n"].statistics.max_value == 7


def test_a_table_that_cannot_be_profiled_does_not_stop_the_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "table.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE A (id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE B (id INTEGER PRIMARY KEY, n INTEGER)")
        con.executemany("INSERT INTO B VALUES (?, ?)", [(1, 5), (2, 7)])
    adapter = _sqlite(path)
    original = BaseSQLAlchemyAdapter._get_columns_metadata

    def columns_metadata(self, inspector, conn, table_ref, *args):
        if table_ref.table_name == "A":
            raise RuntimeError("cannot reflect A")
        return original(self, inspector, conn, table_ref, *args)

    monkeypatch.setattr(type(adapter), "_get_columns_metadata", columns_metadata)

    tables = {m.table.table_name: m for m in adapter.fetch_schema_snapshot(IndexScope()).metadata.tables.values()}

    assert tables["A"].columns == {}
    assert tables["B"].columns["n"].statistics.max_value == 7


POSTGRES_URL = os.environ.get("NL2SQL_TEST_POSTGRES_URL")


@pytest.mark.skipif(not POSTGRES_URL, reason="set NL2SQL_TEST_POSTGRES_URL to run against Postgres")
def test_postgres_odd_columns_do_not_poison_the_transaction(monkeypatch):
    from nl2sql.adapters.postgres.adapter import PostgresAdapter

    from sqlalchemy.engine import make_url

    from nl2sql.adapters.postgres.adapter import PostgresConnection

    url = make_url(POSTGRES_URL)
    schema = f"profiling_{uuid.uuid4().hex[:8]}"
    adapter = PostgresAdapter("pg", PostgresConnection(host=url.host, port=url.port or 5432, user=url.username,
                                                       password=url.password or "", database=url.database),
                              Limits())
    with adapter.engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
        conn.execute(text(f"CREATE TABLE {schema}.t (flag boolean, id uuid, doc jsonb, odd text, later integer)"))
        conn.execute(text(f"INSERT INTO {schema}.t VALUES "
                          "(true, gen_random_uuid(), '{}', 'x', 1), (false, gen_random_uuid(), '{}', 'y', 2)"))
    try:
        _failing_on(adapter, monkeypatch, "odd")

        snapshot = adapter.fetch_schema_snapshot(IndexScope())

        [table] = [m for m in snapshot.metadata.tables.values() if m.table.schema_name == schema]
        assert table.columns["flag"].statistics.distinct_count == 2
        assert table.columns["id"].statistics.distinct_count == 2
        assert table.columns["odd"].statistics is None
        assert table.columns["later"].statistics.max_value == 2
    finally:
        with adapter.engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
