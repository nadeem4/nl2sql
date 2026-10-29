"""DATE_DIFF(unit, start, end): the elapsed days between two dates, fraction kept, on every database.

The keyed Pagila run (C-W4) timed out on "late rentals". The planner first tried EXTRACT_EPOCH
(UNSUPPORTED_FUNCTION), then subtracted two timestamp columns and compared the interval with
``film.rental_duration``: Postgres refused ``interval > smallint``. A plan now says
``DATE_DIFF('day', start, end)``, a number, and the adapter renders it. The first version used sqlglot's
day difference, which truncated on SQLite, rounded on Postgres and counted midnights elsewhere; the tests
now check results.
"""
import sqlite3
from types import SimpleNamespace

import pytest

from nl2sql.adapters.duckdb.adapter import DuckdbAdapter
from nl2sql.adapters.mssql.adapter import MssqlAdapter
from nl2sql.adapters.mysql.adapter import MysqlAdapter
from nl2sql.adapters.postgres.adapter import PostgresAdapter
from nl2sql.adapters.sqlite.adapter import SqliteAdapter, SqliteConnection
from nl2sql.common.errors import ErrorCode
from nl2sql.pipeline.nodes.ast_planner.functions import ALLOWED_FUNCTIONS, date_diff
from nl2sql.pipeline.nodes.ast_planner.prompts import PLANNER_SYSTEM_PROMPT
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    ASTPlannerResponse, Expr, OrderItem, PlanModel, SelectItem, TableRef)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.policies import UserContext
from nl2sql.testing.runtime import fake_runtime, stub_ds_registry
from nl2sql_sdk import Limits

START = Expr(kind="column", alias="t1", column_name="rental_date")
END = Expr(kind="column", alias="t1", column_name="return_date")


def _func(name, *args):
    return Expr(kind="func", func_name=name, args=list(args))


def _lit(value):
    return Expr(kind="literal", value=value)


def _plan(expr, where=None):
    return PlanModel(
        tables=[TableRef(name="rental", alias="t1")],
        select_items=[SelectItem(alias="d", expr=expr)],
        where=where,
    )


def _sql(plan, adapter):
    ctx = fake_runtime(ds_registry=stub_ds_registry(adapter, limits=Limits(row_limit=1000)))
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds", intent="q"),
        ast_planner_response=ASTPlannerResponse(plan=plan),
    )
    result = GeneratorNode(ctx)(state)
    assert not result.get("errors"), result.get("errors")
    return result["generator_response"].sql_draft


def _adapter(cls):
    return cls.__new__(cls)


DIFF = _func("DATE_DIFF", _lit("day"), START, END)


def test_date_diff_is_an_allowed_function():
    assert "DATE_DIFF" in ALLOWED_FUNCTIONS


def test_date_diff_reads_unit_start_and_end():
    assert date_diff("date_diff", DIFF.args) == ("day", START, END)
    assert date_diff("DATE_PART", DIFF.args) is None
    assert date_diff("DATE_DIFF", [START, END]) == (None, None, None)
    assert date_diff("DATE_DIFF", [_lit("week"), START, END])[0] == "week"


# (rental_date, return_date): about 3.87 days; two minutes across midnight; negative; NULL end.
CASES = [
    ("2005-05-24 22:53:30", "2005-05-28 19:40:33"),
    ("2020-01-01 23:59:00", "2020-01-02 00:01:00"),
    ("2020-01-05 00:00:00", "2020-01-01 00:00:00"),
    ("2020-01-01 00:00:00", None),
]


def _expected():
    from datetime import datetime
    fmt = "%Y-%m-%d %H:%M:%S"
    return [
        None if end is None else (datetime.strptime(end, fmt) - datetime.strptime(start, fmt)).total_seconds() / 86400
        for start, end in CASES
    ]


def _assert_days(rows, expected):
    assert len(rows) == len(expected)
    for (got,), want in zip(rows, expected):
        assert (got is None) if want is None else float(got) == pytest.approx(want, abs=1e-6)


def test_sqlite_keeps_the_fraction_of_a_day(tmp_path):
    path = tmp_path / "rentals.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE rental (id INTEGER, rental_date TEXT, return_date TEXT)")
    conn.executemany("INSERT INTO rental VALUES (?, ?, ?)", [(i, *c) for i, c in enumerate(CASES)])
    conn.commit()
    adapter = SqliteAdapter("rentals", SqliteConnection(database=str(path)), Limits())
    plan = _plan(DIFF)
    plan.order_by = [OrderItem(direction="asc", expr=Expr(kind="column", alias="t1", column_name="id"))]

    rows = conn.execute(_sql(plan, adapter)).fetchall()
    conn.close()

    _assert_days(rows, _expected())


def test_sqlite_date_only_values_give_whole_days(tmp_path):
    path = tmp_path / "dates.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE rental (rental_date TEXT, return_date TEXT)")
    conn.execute("INSERT INTO rental VALUES ('2020-01-01', '2020-01-05')")
    conn.commit()
    adapter = SqliteAdapter("rentals", SqliteConnection(database=str(path)), Limits())

    rows = conn.execute(_sql(_plan(DIFF), adapter)).fetchall()
    conn.close()

    assert rows == [(4.0,)]


def test_duckdb_keeps_the_fraction_of_a_day():
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect()
    conn.execute("CREATE TABLE rental (id INTEGER, rental_date TIMESTAMP, return_date TIMESTAMP)")
    for i, (start, end) in enumerate(CASES):
        conn.execute("INSERT INTO rental VALUES (?, ?, ?)", [i, start, end])
    plan = _plan(DIFF)
    plan.order_by = [OrderItem(direction="asc", expr=Expr(kind="column", alias="t1", column_name="id"))]

    rows = conn.execute(_sql(plan, _adapter(DuckdbAdapter))).fetchall()

    _assert_days(rows, _expected())


def test_duckdb_date_only_values_give_whole_days():
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect()
    conn.execute("CREATE TABLE rental (rental_date DATE, return_date DATE)")
    conn.execute("INSERT INTO rental VALUES ('2020-01-01', '2020-01-05')")

    rows = conn.execute(_sql(_plan(DIFF), _adapter(DuckdbAdapter))).fetchall()

    assert rows == [(4.0,)]


# Postgres, SQL Server and MySQL are not runnable here (tests/realdb/test_date_diff.py runs them).
# Each must divide an exact second count by 86400.0, with no integer cast of the days.
@pytest.mark.parametrize("adapter, expected", [
    (PostgresAdapter,
     "CAST(CAST(EXTRACT(epoch FROM CAST(t1.return_date AS TIMESTAMP) - CAST(t1.rental_date AS TIMESTAMP)) AS BIGINT)"
     " AS DOUBLE PRECISION) / 86400.0"),
    (MssqlAdapter,
     "CAST(DATEDIFF_BIG(SECOND, CAST(t1.rental_date AS DATETIME2), CAST(t1.return_date AS DATETIME2)) AS FLOAT)"
     " / 86400.0"),
    (MysqlAdapter, "CAST(TIMESTAMPDIFF(SECOND, t1.rental_date, t1.return_date) AS DOUBLE) / 86400.0"),
])
def test_each_adapter_divides_exact_seconds_by_a_day(adapter, expected):
    sql = _sql(_plan(DIFF), _adapter(adapter))

    assert expected in sql
    assert " AS INT" not in sql


def _validate(expr, columns=(("rental_date", "TIMESTAMP"), ("return_date", "TIMESTAMP"))):
    node = LogicalValidatorNode(fake_runtime(ds_registry=SimpleNamespace(), policies={"allow_all": True}))
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds", intent="q"),
        relevant_tables=[Table(name="rental", columns=[Column(name=n, type=t) for n, t in columns])],
        ast_planner_response=ASTPlannerResponse(plan=_plan(expr)),
        user_context=UserContext(roles=["admin"]),
    )
    return node(state).get("errors") or []


def test_validator_accepts_a_day_difference():
    assert _validate(DIFF) == []


@pytest.mark.parametrize("expr", [
    _func("DATE_DIFF", _lit("week"), START, END),
    _func("DATE_DIFF", START, END),
    _func("DATE_DIFF", _lit("day"), START),
])
def test_validator_rejects_other_units_and_shapes_with_the_usage(expr):
    errors = _validate(expr)

    assert [e.error_code for e in errors] == [ErrorCode.INVALID_PLAN_STRUCTURE]
    assert "DATE_DIFF(unit, start, end)" in errors[0].message


def test_a_bad_date_part_unit_is_a_plan_error_too():
    errors = _validate(_func("DATE_PART", _lit("week"), START))

    assert [e.error_code for e in errors] == [ErrorCode.INVALID_PLAN_STRUCTURE]


def test_validator_rejects_subtracting_two_date_columns():
    minus = Expr(kind="binary", op="-", left=END, right=START)

    errors = _validate(minus)

    assert errors and "DATE_DIFF(unit, start, end)" in errors[0].message
    assert errors[0].error_code == ErrorCode.INVALID_PLAN_STRUCTURE


@pytest.mark.parametrize("type_", ["TIME", "TIME WITHOUT TIME ZONE"])
def test_validator_allows_subtracting_clock_times(type_):
    minus = Expr(kind="binary", op="-", left=END, right=START)

    assert _validate(minus, columns=(("rental_date", type_), ("return_date", type_))) == []


def test_the_subtraction_check_reads_the_planned_schema_s_types():
    minus = Expr(kind="binary", op="-", left=END, right=START)
    plan = _plan(minus)
    plan.tables = [TableRef(name="rental", alias="t1", schema_name="b")]
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds", intent="q"),
        relevant_tables=[
            Table(name="a.rental", columns=[Column(name="rental_date", type="TIMESTAMP"),
                                            Column(name="return_date", type="TIMESTAMP")]),
            Table(name="b.rental", columns=[Column(name="rental_date", type="INTEGER"),
                                            Column(name="return_date", type="INTEGER")]),
        ],
    )

    assert LogicalValidatorNode._date_subtraction(state, plan) is None


def test_validator_allows_subtracting_numbers():
    minus = Expr(kind="binary", op="-", left=END, right=START)

    assert _validate(minus, columns=(("rental_date", "INTEGER"), ("return_date", "INTEGER"))) == []


def test_the_prompt_offers_date_diff_and_no_dialect():
    assert "DATE_DIFF(unit, start, end)" in PLANNER_SYSTEM_PROMPT
    assert "never subtract" in PLANNER_SYSTEM_PROMPT.lower()
    assert "Never use any other date or time function" in PLANNER_SYSTEM_PROMPT
