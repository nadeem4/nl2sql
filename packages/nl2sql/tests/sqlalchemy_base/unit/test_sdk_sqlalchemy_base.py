"""The SQLAlchemy base in the SDK: reflect once, row estimates, type-aware profiling, issues.

docs/architecture/phase2.md, section 7, "Profiling and row estimates" and
"The SQLAlchemy base, public". The real servers are in ``tests/realdb``.
"""
import datetime
import decimal
import sqlite3
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import Boolean, Date, Integer, JSON, LargeBinary, Numeric, String, Text, Uuid, event, text
from sqlalchemy.dialects import mssql, postgresql

from nl2sql.adapters.duckdb.adapter import DuckdbAdapter, DuckdbConnection
from nl2sql.adapters.mssql.adapter import MssqlAdapter, MssqlConnection
from nl2sql.adapters.mysql.adapter import MysqlAdapter, MysqlConnection
from nl2sql.adapters.postgres.adapter import PostgresAdapter, PostgresConnection
from nl2sql.adapters.sqlite.adapter import SqliteAdapter, SqliteConnection
from nl2sql_sdk import IndexScope, Limits
from nl2sql_sdk.sqlalchemy import (
    ColumnProfile,
    Pool,
    ReflectedTable,
    SQLAlchemyAdapter,
    SQLAlchemyConnection,
    as_scalar,
    column_profile,
)


def _sqlite(path):
    return SqliteAdapter("t", SqliteConnection(database=path.as_posix()), Limits())


def _statements(adapter):
    """Every statement (with its parameters) the adapter's engine sends, as text."""
    seen = []
    event.listen(adapter.engine, "before_cursor_execute",
                 lambda conn, cursor, statement, parameters, *args: seen.append(f"{statement} {parameters}"))
    return seen


@pytest.fixture
def shop(tmp_path):
    path = tmp_path / "shop.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT, total NUMERIC)")
        con.executemany("INSERT INTO orders VALUES (?, ?, ?)",
                        [(1, "open", 2.5), (2, "shipped", 4.0), (3, "open", None)])
    return path


def _table(snapshot, name):
    [table] = [m for m in snapshot.metadata.tables.values() if m.table.table_name == name]
    return table


# --- The built-ins build on the SDK's base --------------------------------

@pytest.mark.parametrize("adapter_cls", [PostgresAdapter, MysqlAdapter, MssqlAdapter, SqliteAdapter, DuckdbAdapter])
def test_every_built_in_builds_on_the_sdk_base(adapter_cls):
    assert issubclass(adapter_cls, SQLAlchemyAdapter)
    for dead in ("dry_run", "explain", "cost_estimate", "exclude_schemas"):
        assert not hasattr(adapter_cls, dead), f"{adapter_cls.__name__}.{dead} is gone (B24)"


@pytest.mark.parametrize("model", [PostgresConnection, MysqlConnection, MssqlConnection])
def test_server_connections_have_a_pool(model):
    assert issubclass(model, SQLAlchemyConnection)
    assert model.model_fields["pool"].default_factory() == Pool()


@pytest.mark.parametrize("model", [SqliteConnection, DuckdbConnection])
def test_file_databases_have_no_pool(model):
    with pytest.raises(ValidationError):
        model(database="x.db", pool={"size": 2})


def test_pool_defaults_cap_concurrent_queries():
    assert Pool() == Pool(size=5, max_overflow=0, timeout_s=30, recycle_s=1800)
    with pytest.raises(ValidationError):
        Pool(sise=3)


def test_pool_settings_reach_the_engine():
    connection = PostgresConnection(host="db", database="d", user="u", password="p",
                                    pool={"size": 3, "max_overflow": 1, "timeout_s": 7, "recycle_s": 60})
    adapter = PostgresAdapter("pg", connection, Limits())

    pool = adapter.engine.pool

    assert (pool.size(), pool._max_overflow, pool._timeout, pool._recycle) == (3, 1, 7, 60)


# --- Type-aware statistics -------------------------------------------------

@pytest.mark.parametrize("sa_type", [Boolean(), mssql.BIT(), Uuid(), postgresql.UUID(), mssql.UNIQUEIDENTIFIER()])
def test_unordered_types_get_no_min_or_max(sa_type):
    assert column_profile(sa_type) == ColumnProfile(min_max=False, distinct=True, values=False)


@pytest.mark.parametrize("sa_type", [JSON(), postgresql.JSONB(), LargeBinary(), postgresql.BYTEA(),
                                     postgresql.ARRAY(Integer()), mssql.XML()])
def test_json_binary_xml_and_arrays_get_no_statistics(sa_type):
    assert column_profile(sa_type) == ColumnProfile(min_max=False, distinct=False, values=False)


@pytest.mark.parametrize("sa_type, values", [(Integer(), False), (Numeric(10, 2), False), (Date(), False),
                                             (String(20), True), (Text(), True)])
def test_ordered_types_keep_min_and_max_and_only_text_has_values(sa_type, values):
    assert column_profile(sa_type) == ColumnProfile(min_max=True, distinct=True, values=values)


@pytest.mark.parametrize("value, expected", [
    (datetime.date(2024, 1, 31), "2024-01-31"),
    (datetime.datetime(2024, 1, 31, 8, 30), "2024-01-31T08:30:00"),
    (datetime.time(8, 30), "08:30:00"),
    (decimal.Decimal("12.50"), 12.5),
    (uuid.UUID(int=1), "00000000-0000-0000-0000-000000000001"),
    (7, 7), ("a", "a"), (None, None),
])
def test_min_and_max_values_become_scalars(value, expected):
    # A PostgreSQL or SQL Server date column returns datetime.date, which the
    # statistics model (str, int, float, bool) rejects.
    assert as_scalar(value) == expected


def test_a_boolean_column_has_counts_but_no_min_or_max(tmp_path):
    path = tmp_path / "flags.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE T (id INTEGER PRIMARY KEY, active BOOLEAN)")
        con.executemany("INSERT INTO T VALUES (?, ?)", [(1, 1), (2, 0), (3, None)])

    table = _table(_sqlite(path).fetch_schema_snapshot(IndexScope()), "T")

    active = table.columns["active"].statistics
    assert active.distinct_count == 2
    assert active.min_value is None and active.max_value is None
    assert table.columns["id"].statistics.max_value == 3


# --- Sample values: off unless asked (owner decision 9, B23) ---------------

def test_no_values_without_sample_values(shop):
    adapter = _sqlite(shop)
    seen = _statements(adapter)

    orders = _table(adapter.fetch_schema_snapshot(IndexScope()), "orders")

    assert orders.columns["status"].statistics.distinct_count == 2
    assert orders.columns["status"].statistics.sample_values == []
    assert not [s for s in seen if "GROUP BY" in s.upper()]


def test_sample_values_collect_text_values(shop):
    orders = _table(_sqlite(shop).fetch_schema_snapshot(IndexScope(sample_values=True)), "orders")

    assert orders.columns["status"].statistics.sample_values == ["open", "shipped"]
    assert orders.columns["total"].statistics.sample_values == []


# --- Row counts: the catalog's estimate, COUNT(*) only without one (B22) ---

def test_sqlite_counts_rows_under_stats(shop):
    orders = _table(_sqlite(shop).fetch_schema_snapshot(IndexScope()), "orders")

    assert (orders.row_count, orders.row_count_source) == (3, "exact")
    assert orders.columns["status"].statistics.null_percentage == 0
    assert orders.columns["total"].statistics.null_percentage == pytest.approx(1 / 3)


def test_catalog_profile_reads_no_table_data(shop):
    adapter = _sqlite(shop)
    seen = _statements(adapter)

    snapshot = adapter.fetch_schema_snapshot(IndexScope(profile="catalog", sample_values=True))

    orders = _table(snapshot, "orders")
    assert (orders.row_count, orders.row_count_source) == (None, None)
    assert all(column.statistics is None for column in orders.columns.values())
    assert sorted(snapshot.contract.tables["[main].[orders]"].columns) == ["id", "status", "total"]
    assert not [s for s in seen if "orders" in s.lower() and "select" in s.lower() and "from" in s.lower()
                and "sqlite_" not in s.lower()]


def test_duckdb_estimates_come_from_its_catalog():
    adapter = DuckdbAdapter("d", DuckdbConnection(database=":memory:"), Limits())
    with adapter.engine.begin() as conn:
        conn.execute(text("CREATE TABLE t AS SELECT * FROM range(42)"))
        conn.execute(text("CREATE SCHEMA s2"))
        conn.execute(text("CREATE TABLE s2.u AS SELECT * FROM range(7)"))

    with adapter.engine.connect() as conn:
        assert adapter.row_estimates(conn, "memory.main") == {"t": 42}
        assert adapter.row_estimates(conn, "memory.s2") == {"u": 7}


def test_a_table_over_max_profile_rows_is_profiled_from_the_catalog(shop):
    snapshot = _sqlite(shop).fetch_schema_snapshot(IndexScope(max_profile_rows=2))

    orders = _table(snapshot, "orders")
    assert orders.row_count == 3
    assert all(column.statistics is None for column in orders.columns.values())
    [issue] = snapshot.issues
    assert (issue.table, issue.column, issue.stage) == ("main.orders", None, "profile")
    assert "max_profile_rows" in issue.message


# --- Failures become issues, not log lines ---------------------------------

def test_a_column_whose_statistics_fail_becomes_an_issue(shop):
    adapter = _sqlite(shop)

    def fail_on_status(conn, cursor, statement, *args):
        if "status" in statement and "count" in statement.lower():
            raise RuntimeError("cannot profile status")

    event.listen(adapter.engine, "before_cursor_execute", fail_on_status)

    snapshot = adapter.fetch_schema_snapshot(IndexScope())

    orders = _table(snapshot, "orders")
    assert orders.columns["status"].statistics is None
    assert orders.columns["total"].statistics is not None
    [issue] = snapshot.issues
    assert (issue.table, issue.column, issue.stage) == ("main.orders", "status", "profile")
    assert "cannot profile status" in issue.message


def test_the_failed_statement_is_rolled_back_before_the_next(shop):
    adapter = _sqlite(shop)
    events = []
    event.listen(adapter.engine, "rollback", lambda conn: events.append("ROLLBACK"))

    def fail_on_status(conn, cursor, statement, *args):
        events.append(statement)
        if "status" in statement and "count" in statement.lower():
            raise RuntimeError("cannot profile status")

    event.listen(adapter.engine, "before_cursor_execute", fail_on_status)

    adapter.fetch_schema_snapshot(IndexScope())

    failed_at = next(i for i, e in enumerate(events) if "status" in e and "count" in e.lower())
    assert events[failed_at + 1] == "ROLLBACK", "the next statement ran in the failed transaction"


def test_a_failed_row_count_becomes_an_issue(shop):
    adapter = _sqlite(shop)

    def fail_on_count(conn, cursor, statement, *args):
        if statement.lower().startswith("select count(*)") and "case" not in statement.lower():
            raise RuntimeError("cannot count")

    event.listen(adapter.engine, "before_cursor_execute", fail_on_count)

    snapshot = adapter.fetch_schema_snapshot(IndexScope())

    orders = _table(snapshot, "orders")
    assert (orders.row_count, orders.row_count_source) == (None, None)
    assert orders.columns["status"].statistics is not None
    assert [(i.table, i.stage) for i in snapshot.issues] == [("main.orders", "estimate")]


# --- The scope is applied before any per-schema query ----------------------

def test_a_schema_no_include_tables_pattern_names_is_not_wanted():
    scope = IndexScope(include_tables=["sales.orders", "rep*.daily_*"])

    assert [s for s in ("sales", "hr", "reporting") if scope.wants_schema(s)] == ["sales", "reporting"]
    assert IndexScope(include_tables=["*.orders"]).wants_schema("hr")


def test_an_excluded_schema_is_never_queried(shop, tmp_path):
    hidden = tmp_path / "hidden.sqlite"
    with sqlite3.connect(hidden) as con:
        con.execute("CREATE TABLE secret (y INTEGER)")
    adapter = _sqlite(shop)
    # SQLite lists each attached database as a schema.
    event.listen(adapter.engine, "connect",
                 lambda dbapi_conn, record: dbapi_conn.execute(f"ATTACH DATABASE '{hidden.as_posix()}' AS hidden_zone"))
    seen = _statements(adapter)

    everything = adapter.fetch_schema_snapshot(IndexScope(profile="catalog"))
    assert {t.table.schema_name for t in everything.contract.tables.values()} == {"main", "hidden_zone"}
    seen.clear()

    snapshot = adapter.fetch_schema_snapshot(IndexScope(exclude_schemas=["hidden*"]))

    assert [t.table.table_name for t in snapshot.contract.tables.values()] == ["orders"]
    assert not [s for s in seen if "hidden_zone" in s]


# --- An adapter can read its own catalog ------------------------------------

def test_an_adapter_reads_its_own_catalog_through_schema_names_and_reflect(shop):
    class OwnCatalog(SqliteAdapter):
        def schema_names(self, conn):
            return ["main"]

        def reflect(self, conn, schema, wants_table, issues):
            columns = [{"name": "id", "type": "BIGINT", "nullable": False, "comment": "The id"}]
            return {"orders": ReflectedTable(columns=columns, primary_key={"id"}, foreign_keys=[],
                                             comment="Orders, as the catalog says")}

    adapter = OwnCatalog("t", SqliteConnection(database=shop.as_posix()), Limits())

    snapshot = adapter.fetch_schema_snapshot(IndexScope(profile="catalog"))

    [contract] = snapshot.contract.tables.values()
    assert contract.columns["id"].data_type == "BIGINT" and contract.columns["id"].is_primary_key
    orders = _table(snapshot, "orders")
    assert (orders.description, orders.columns["id"].description) == ("Orders, as the catalog says", "The id")


# --- The demo turns sample values on ----------------------------------------

def test_the_demo_turns_sample_values_on():
    from nl2sql.config import demo_config

    assert all(d.index.sample_values for d in demo_config().datasources)
