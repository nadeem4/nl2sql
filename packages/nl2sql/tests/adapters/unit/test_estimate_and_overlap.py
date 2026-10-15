"""``estimate`` (rows read) and ``probe_overlap`` (a distinct sample) on the in-process built-ins (C1).

docs/architecture/wave_b.md, section 9, "The adapter method", and section 6,
"Join candidates" (D45, D52). PostgreSQL, MySQL and SQL Server are proven in
``tests/realdb/test_estimate.py``; their plan readers are checked here on
plans in the shape each server returns.
"""
from __future__ import annotations

import re
import sqlite3
import time
from contextlib import closing

import duckdb
import pytest

from nl2sql.adapters.duckdb.adapter import DuckdbAdapter, DuckdbConnection
from nl2sql.adapters.mssql.adapter import read_showplan
from nl2sql.adapters.mysql.adapter import rows_examined
from nl2sql.adapters.postgres.adapter import scans
from nl2sql.adapters.sqlite.adapter import SqliteAdapter, SqliteConnection
from nl2sql_sdk import (
    ColumnRef,
    DatasourceCapability,
    EstimatingAdapterProtocol,
    Limits,
    ProfilingAdapterProtocol,
    TableRef,
)
from nl2sql_sdk.sqlalchemy.adapter import overlap_statement

ESTIMATES = DatasourceCapability.ESTIMATES_COST


def _col(schema, table, column):
    return ColumnRef(table=TableRef(schema_name=schema, table_name=table), column_name=column)


@pytest.fixture
def duck(tmp_path):
    """A DuckDB file with ``big`` (1,000 rows, ``amount`` = 0..1998 step 2) and ``small`` (50 rows)."""
    path = str(tmp_path / "est.duckdb")
    with closing(duckdb.connect(path)) as conn:
        conn.execute("CREATE TABLE big (id INTEGER PRIMARY KEY, amount INTEGER)")
        conn.execute("INSERT INTO big SELECT range, range * 2 FROM range(1000)")
        conn.execute("CREATE TABLE small (id INTEGER, big_id INTEGER)")
        conn.execute("INSERT INTO small SELECT range, range FROM range(50)")
    adapter = DuckdbAdapter("duck", DuckdbConnection(database=path), Limits(timeout_ms=5000))
    yield adapter
    adapter.close()


@pytest.fixture
def lite(tmp_path):
    """A SQLite file: ``customers`` (ids 1..10), ``orders`` (100 rows over customers 1..5) and ``others`` (ids 50..59)."""
    path = str(tmp_path / "overlap.sqlite")
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
        conn.executemany("INSERT INTO customers VALUES (?, ?)", [(i, f"c{i}") for i in range(1, 11)])
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER)")
        conn.executemany("INSERT INTO orders VALUES (?, ?)", [(i, 1 + i % 5) for i in range(100)])
        conn.execute("INSERT INTO orders VALUES (100, NULL)")
        conn.execute("CREATE TABLE others (id INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO others VALUES (?)", [(i,) for i in range(50, 60)])
        conn.commit()
    adapter = SqliteAdapter("lite", SqliteConnection(database=path), Limits(timeout_ms=5000))
    yield adapter
    adapter.close()


# --- estimate ------------------------------------------------------------------


def test_duckdb_estimate_reads_rows_without_running(duck):
    assert ESTIMATES in duck.capabilities()
    assert isinstance(duck, EstimatingAdapterProtocol)

    start = time.perf_counter()
    slow = duck.estimate("SELECT SUM(range) FROM range(10000000000000)", {})
    assert time.perf_counter() - start < 1
    assert slow.rows == 10_000_000_000_000

    joined = duck.estimate("SELECT COUNT(*) FROM big JOIN small ON big.id = small.big_id WHERE small.id = :id",
                           {"id": 3})
    assert joined.rows == 1050
    assert joined.cost is None and joined.source


def test_duckdb_filtered_full_scan_counts_the_table(duck):
    # 51 lies between the column's min and max, so DuckDB cannot rule the scan out from its statistics.
    assert duck.execute_sql("SELECT id FROM big WHERE amount = 51").row_count == 0
    assert duck.estimate("SELECT id FROM big WHERE amount = 51", {}).rows == 1000


def test_duckdb_estimate_of_a_missing_table_raises(duck):
    with pytest.raises(RuntimeError, match="could not estimate"):
        duck.estimate("SELECT * FROM nowhere", {})


def test_sqlite_does_not_declare_estimates(lite):
    assert ESTIMATES not in lite.capabilities()
    assert not isinstance(lite, EstimatingAdapterProtocol)


# --- probe_overlap --------------------------------------------------------------


def test_overlap_counts_matches_within_the_sample(lite):
    assert isinstance(lite, ProfilingAdapterProtocol)
    # customers 1..10 against orders' customer ids 1..5.
    overlap = lite.probe_overlap(_col("main", "customers", "id"), _col("main", "orders", "customer_id"), 100)
    assert (overlap.sampled, overlap.matched) == (10, 5)
    # The sample bounds what is read.
    assert lite.probe_overlap(_col("main", "customers", "id"), _col("main", "orders", "customer_id"), 3).sampled == 3


def test_overlap_samples_distinct_values(lite):
    # 100 orders over five customers, and one with no customer: five distinct non-null values.
    overlap = lite.probe_overlap(_col("main", "orders", "customer_id"), _col("main", "customers", "id"), 1000)
    assert (overlap.sampled, overlap.matched) == (5, 5)


def test_overlap_of_disjoint_columns_is_zero(lite, duck):
    overlap = lite.probe_overlap(_col("main", "others", "id"), _col("main", "customers", "id"), 100)
    assert (overlap.sampled, overlap.matched) == (10, 0)
    # The base's default runs on DuckDB too.
    assert duck.probe_overlap(_col("main", "small", "big_id"), _col("main", "big", "id"), 100).matched == 50


def test_overlap_failure_never_shows_values(lite):
    with pytest.raises(RuntimeError) as failed:
        lite.probe_overlap(_col("main", "customers", "name"), _col("main", "missing", "id"), 100)
    assert "c1" not in str(failed.value) and "missing" in str(failed.value)


# --- The server adapters' plan readers --------------------------------------------


def test_overlap_aggregates_no_subquery():
    """SQL Server refuses an aggregate over a subquery (Msg 130): the EXISTS flag is computed one level down."""
    from sqlalchemy.dialects import mssql

    sql = str(overlap_statement(_col("s", "a", "x"), _col("s", "b", "y"), 10).compile(dialect=mssql.dialect()))
    assert "EXISTS" in sql
    assert not re.search(r"(count|sum)\((?:(?!\bFROM\b).)*\bEXISTS", sql, re.IGNORECASE | re.DOTALL), sql


def test_duckdb_counts_a_table_whose_name_needs_quoting(tmp_path):
    path = str(tmp_path / "quoted.duckdb")
    with closing(duckdb.connect(path)) as conn:
        conn.execute('CREATE SCHEMA "My.Schema"')
        conn.execute('CREATE TABLE "My.Schema"."Big Orders" (id INTEGER, amount INTEGER)')
        conn.execute('INSERT INTO "My.Schema"."Big Orders" SELECT range, range * 2 FROM range(1000)')
        conn.execute('CREATE TABLE "order" (id INTEGER)')
        conn.execute('INSERT INTO "order" SELECT range FROM range(300)')
    adapter = DuckdbAdapter("duck", DuckdbConnection(database=path), Limits(timeout_ms=5000))
    try:
        assert adapter.estimate('SELECT id FROM "My.Schema"."Big Orders" WHERE amount = 51', {}).rows == 1000
        assert adapter.estimate('SELECT id FROM "order" WHERE id % 1000 = 999', {}).rows == 300
    finally:
        adapter.close()


# --- The server adapters' plan readers --------------------------------------------


def _scan(relation, rows, kind="Seq Scan"):
    return {"Node Type": kind, "Schema": "public", "Relation Name": relation, "Plan Rows": rows}


def test_postgres_seq_scan_counts_the_table_and_an_index_scan_its_plan_rows():
    plan = {"Node Type": "Hash Join", "Plan Rows": 1, "Total Cost": 42.5, "Plans": [
        _scan("rental", 1),
        {"Node Type": "Hash", "Plans": [
            {"Node Type": "Bitmap Heap Scan", "Schema": "public", "Relation Name": "film", "Plan Rows": 9, "Plans": [
                {"Node Type": "Bitmap Index Scan", "Plan Rows": 10}]}]},
    ]}
    assert scans(plan) == ([("public", "rental", 1)], 10)


def test_postgres_nested_loop_counts_the_inner_side_once_per_outer_row():
    plan = {"Node Type": "Nested Loop", "Plan Rows": 5, "Plans": [
        _scan("rental", 1000),
        _scan("customer", 1, "Index Scan"),
    ]}
    assert scans(plan) == ([("public", "rental", 1)], 1000)

    # An inner Seq Scan is re-read for each outer row, unless a Materialize keeps its rows.
    rescanned = {"Node Type": "Nested Loop", "Plans": [_scan("a", 10), _scan("b", 3)]}
    assert scans(rescanned) == ([("public", "a", 1), ("public", "b", 10)], 0)
    materialized = {"Node Type": "Nested Loop", "Plans": [
        _scan("a", 10), {"Node Type": "Materialize", "Plans": [_scan("b", 3)]}]}
    assert scans(materialized) == ([("public", "a", 1), ("public", "b", 1)], 0)


def test_mysql_multiplies_per_scan_rows_by_the_rows_joined_before():
    plan = {"query_block": {"cost_info": {"query_cost": "1700.25"}, "nested_loop": [
        {"table": {"table_name": "rental", "access_type": "ALL", "rows_examined_per_scan": 16000,
                   "rows_produced_per_join": 1600}},
        {"table": {"table_name": "customer", "access_type": "eq_ref", "rows_examined_per_scan": 1,
                   "rows_produced_per_join": 1600}},
        # A hash join reads its table once.
        {"table": {"table_name": "film", "access_type": "ALL", "rows_examined_per_scan": 1000,
                   "using_join_buffer": "hash join"}},
    ]}}
    assert rows_examined(plan) == 16000 + 1600 + 1000


def test_mysql_skips_a_materialized_derived_table_but_counts_what_it_reads():
    plan = {"query_block": {"table": {
        "table_name": "dt", "access_type": "ALL", "rows_examined_per_scan": 500,
        "materialized_from_subquery": {"query_block": {"table": {
            "table_name": "film", "access_type": "ALL", "rows_examined_per_scan": 1000}}}}}}
    assert rows_examined(plan) == 1000
    union = {"query_block": {"union_result": {"table_name": "<union1,2>", "query_specifications": [
        {"query_block": {"table": {"table_name": "a", "rows_examined_per_scan": 7}}},
        {"query_block": {"table": {"table_name": "b", "rows_examined_per_scan": 8}}}]}}}
    assert rows_examined(union) == 15


def _showplan(body, cost=' StatementSubtreeCost="0.25"'):
    return ('<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan"><BatchSequence><Batch>'
            f'<Statements><StmtSimple{cost}><QueryPlan>{body}</QueryPlan></StmtSimple></Statements>'
            '</Batch></BatchSequence></ShowPlanXML>')


_JOIN = """<RelOp PhysicalOp="Nested Loops" EstimateRows="1" EstimatedTotalSubtreeCost="0.5">
 <RelOp PhysicalOp="Clustered Index Scan" EstimateRows="1" EstimatedRowsRead="847">
  <IndexScan><Object Schema="[SalesLT]" Table="[Customer]" Index="[PK_Customer]"/></IndexScan></RelOp>
 <RelOp PhysicalOp="Table Scan" EstimateRows="1" EstimateRebinds="0" EstimateRewinds="2">
  <TableScan><Object Schema="[dbo]" Table="[Heap]"/></TableScan></RelOp>
 <RelOp PhysicalOp="Clustered Index Seek" EstimateRows="1" EstimateRebinds="846" EstimateRewinds="0">
  <IndexScan><Object Schema="[SalesLT]" Table="[Address]"/></IndexScan></RelOp>
 <RelOp PhysicalOp="Columnstore Index Scan" EstimateRows="1" EstimatedRowsRead="5000">
  <IndexScan><Object Schema="[dbo]" Table="[Facts]"/></IndexScan></RelOp>
</RelOp>"""


def test_mssql_reads_rows_read_per_execution_and_names_the_scans_it_must_count():
    read, cost, uncounted = read_showplan(_showplan(_JOIN))
    # 847 + an inner seek run 847 times + a columnstore scan; the heap is scanned 3 times from the catalog.
    assert (read, cost, uncounted) == (847 + 847 + 5000, 0.25, [("dbo", "Heap", 3)])


def test_mssql_cost_falls_back_to_the_plan_and_is_none_when_absent():
    assert read_showplan(_showplan(_JOIN, cost=""))[1] == 0.5
    bare = '<RelOp PhysicalOp="Clustered Index Scan" EstimatedRowsRead="1"><IndexScan/></RelOp>'
    assert read_showplan(_showplan(bare, cost=""))[1] is None


def test_mssql_estimate_writes_parameters_as_literals():
    """Under SHOWPLAN, a statement sent with parameters (an RPC) returns no plan, so they are written in as literals.

    The values are escaped as SQL Server literals, so a quote cannot end one.
    """
    from nl2sql.adapters.mssql.adapter import MssqlAdapter, MssqlConnection

    adapter = MssqlAdapter("ms", MssqlConnection(host="h", database="d", user="u", password="p"), Limits())
    sql = adapter.inline_parameters("SELECT * FROM t WHERE d < :d AND name = :name AND n = :n",
                                    {"d": "1900-01-01", "name": "O'Brien; SET SHOWPLAN_XML OFF", "n": 3})
    assert ":d" not in sql and ":name" not in sql and ":n" not in sql
    assert "'1900-01-01'" in sql and "'O''Brien; SET SHOWPLAN_XML OFF'" in sql and "n = 3" in sql
    assert adapter.inline_parameters("SELECT 1 AS one", {}) == "SELECT 1 AS one"


def test_mssql_no_plan_is_an_error_not_zero_rows():
    """No plan is no estimate: zero rows would let a cost rule treat the query as free."""
    for empty in (None, "", _showplan("", cost="").replace("<StmtSimple><QueryPlan></QueryPlan></StmtSimple>", "")):
        with pytest.raises(ValueError, match="no plan"):
            read_showplan(empty)


def test_duckdb_no_plan_raises(duck):
    class NoRows:
        def all(self):
            return []

    duck._run = lambda conn, sql, parameters: NoRows()
    with pytest.raises(RuntimeError, match="could not estimate"):
        duck.estimate("SELECT 1", {})
