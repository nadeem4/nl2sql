"""DATE_DIFF(unit, start, end): the whole days between two dates, as a number, on every database.

The keyed Pagila run (C-W4) timed out on "late rentals". The planner first tried EXTRACT_EPOCH
(UNSUPPORTED_FUNCTION), then subtracted two timestamp columns and compared the interval with
``film.rental_duration``: Postgres refused ``interval > smallint``. A plan now says
``DATE_DIFF('day', start, end)``, an integer, and the adapter's dialect renders it.
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
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, Expr, PlanModel, SelectItem, TableRef
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


@pytest.mark.parametrize("adapter, expected", [
    (PostgresAdapter, "EXTRACT(epoch FROM CAST(t1.return_date AS TIMESTAMP) - CAST(t1.rental_date AS TIMESTAMP)) / 86400"),
    (MssqlAdapter, "DATEDIFF(DAY, t1.rental_date, t1.return_date)"),
    (MysqlAdapter, "DATEDIFF(t1.return_date, t1.rental_date)"),
    (DuckdbAdapter, "DATE_DIFF('DAY', t1.rental_date, t1.return_date)"),
])
def test_each_adapter_renders_the_difference_in_its_dialect(adapter, expected):
    assert expected in _sql(_plan(DIFF), _adapter(adapter))


def test_sqlite_computes_whole_days_for_real(tmp_path):
    path = tmp_path / "rentals.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE rental (rental_date TEXT, return_date TEXT)")
    conn.execute("INSERT INTO rental VALUES ('2005-05-24 22:53:30', '2005-05-28 19:40:33')")
    conn.execute("INSERT INTO rental VALUES ('2005-05-24 00:00:00', '2005-06-03 00:00:00')")
    conn.commit()
    conn.close()
    adapter = SqliteAdapter("rentals", SqliteConnection(database=str(path)), Limits())

    sql = _sql(_plan(DIFF), adapter)
    conn = sqlite3.connect(path)
    rows = conn.execute(sql).fetchall()
    conn.close()

    assert rows == [(3,), (10,)]


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
    error = LogicalValidatorNode._date_operations(_plan(expr))

    assert error is not None and "DATE_DIFF(unit, start, end)" in error.message
    assert error.error_code == ErrorCode.INVALID_PLAN_STRUCTURE


def test_validator_rejects_subtracting_two_date_columns():
    minus = Expr(kind="binary", op="-", left=END, right=START)

    errors = _validate(minus)

    assert errors and "DATE_DIFF(unit, start, end)" in errors[0].message
    assert errors[0].error_code == ErrorCode.INVALID_PLAN_STRUCTURE


def test_validator_allows_subtracting_numbers():
    minus = Expr(kind="binary", op="-", left=END, right=START)

    assert _validate(minus, columns=(("rental_date", "INTEGER"), ("return_date", "INTEGER"))) == []


def test_the_prompt_offers_date_diff_and_no_dialect():
    assert "DATE_DIFF(unit, start, end)" in PLANNER_SYSTEM_PROMPT
    assert "never subtract" in PLANNER_SYSTEM_PROMPT.lower()
    assert "Never use any other date or time function" in PLANNER_SYSTEM_PROMPT
