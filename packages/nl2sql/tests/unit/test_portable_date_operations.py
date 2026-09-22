"""Portable date operations in plans, rendered by each adapter.

The first gpt-5.4 tier 2 run (``benchmarks/tier2/chinook/2026-09-22_1a8101d_gpt-5.4.json``)
crashed three date questions on SQLite, which has neither function:

* chinook_004 and 010 planned ``DATE_TRUNC('year'|'month', t1.InvoiceDate)``;
* chinook_014 planned ``YEAR(t1.InvoiceDate)``.

Plans now say ``DATE_PART(unit, x)`` and ``DATE_TRUNC(unit, x)`` the same way
for every database. The generator builds sqlglot's typed nodes for them (and
for the raw ``YEAR``/``MONTH``/``EXTRACT``/``DATE_TRUNC`` a model may still
write) and hands the finished tree to the adapter's ``render_sql``. A date part
is an integer and a truncated date an ISO ``YYYY-MM-DD`` string on every adapter.
"""
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import nl2sql
from nl2sql.adapters.duckdb.adapter import DuckdbAdapter
from nl2sql.adapters.mssql.adapter import MssqlAdapter
from nl2sql.adapters.mysql.adapter import MysqlAdapter
from nl2sql.adapters.postgres.adapter import PostgresAdapter
from nl2sql.adapters.sqlite.adapter import SqliteAdapter
from nl2sql.pipeline.nodes.ast_planner.prompts import PLANNER_SYSTEM_PROMPT
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    ASTPlannerResponse,
    Expr,
    GroupByItem,
    OrderItem,
    PlanModel,
    SelectItem,
    TableRef,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import SubgraphExecutionState

CHINOOK = Path(nl2sql.__file__).resolve().parent / "cli" / "demo" / "data" / "chinook.sqlite"
DATE = Expr(kind="column", alias="t1", column_name="InvoiceDate")


def _func(name, *args):
    return Expr(kind="func", func_name=name, args=list(args))


def _lit(value):
    return Expr(kind="literal", value=value)


def _state(plan):
    return SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="chinook", intent="q"),
        ast_planner_response=ASTPlannerResponse(plan=plan),
    )


def _sql(plan, adapter):
    ctx = SimpleNamespace(ds_registry=SimpleNamespace(get_adapter=lambda _id: adapter))
    result = GeneratorNode(ctx)(_state(plan))
    assert not result.get("errors"), result.get("errors")
    return result["generator_response"].sql_draft


def _sqlite_adapter():
    return SqliteAdapter(datasource_id="chinook", datasource_engine_type="sqlite",
                         connection_args={"type": "sqlite", "database": str(CHINOOK)})


def _run(sql):
    conn = sqlite3.connect(f"file:{CHINOOK.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _revenue_by(period: Expr, where: Expr = None) -> PlanModel:
    """The recorded shape: SELECT <period>, SUM(Total) ... GROUP BY <period> ORDER BY <period>."""
    return PlanModel(
        tables=[TableRef(name="Invoice", alias="t1", ordinal=0)],
        select_items=[
            SelectItem(ordinal=0, expr=period, alias="period"),
            SelectItem(ordinal=1, alias="revenue", expr=Expr(
                kind="func", func_name="SUM", is_aggregate=True,
                args=[Expr(kind="column", alias="t1", column_name="Total")])),
        ],
        where=where,
        group_by=[GroupByItem(ordinal=0, expr=period)],
        order_by=[OrderItem(ordinal=0, direction="asc", expr=period)],
    )


def _periods(period: Expr, where: Expr = None):
    return _run(_sql(_revenue_by(period, where), _sqlite_adapter()))


# --- the recorded plans now run on Chinook (SQLite) -------------------------------


def test_chinook_004_date_trunc_year_runs_on_sqlite():
    rows = _periods(_func("DATE_TRUNC", _lit("year"), DATE))

    assert [r[0] for r in rows] == ["2009-01-01", "2010-01-01", "2011-01-01", "2012-01-01", "2013-01-01"]
    assert rows[0][1] == pytest.approx(449.46)


def test_chinook_010_date_trunc_month_runs_on_sqlite():
    usa_2013 = Expr(kind="binary", op="AND",
                    left=Expr(kind="binary", op="=", left=Expr(kind="column", alias="t1", column_name="BillingCountry"),
                              right=_lit("USA")),
                    right=Expr(kind="binary", op=">=", left=DATE, right=_lit("2013-01-01")))
    rows = _periods(_func("DATE_TRUNC", _lit("month"), DATE), usa_2013)

    assert rows[0][0] == "2013-02-01"  # the first USA invoice of 2013 (gold: 2013-02)
    assert all(r[0].endswith("-01") for r in rows)


def test_chinook_014_year_runs_on_sqlite_as_an_integer():
    rows = _periods(_func("YEAR", DATE))

    assert [r[0] for r in rows] == [2009, 2010, 2011, 2012, 2013]
    assert rows[-1][1] == pytest.approx(450.58)


# --- the portable operations, every unit, on SQLite -------------------------------


@pytest.mark.parametrize("unit, first, count", [
    ("year", 2009, 5), ("quarter", 1, 4), ("month", 1, 12), ("day", 1, 31),
])
def test_date_part_is_an_integer_on_sqlite(unit, first, count):
    rows = _periods(_func("DATE_PART", _lit(unit), DATE))

    assert rows[0][0] == first and isinstance(rows[0][0], int)
    assert len(rows) == count


@pytest.mark.parametrize("unit, first, count", [
    ("year", "2009-01-01", 5), ("quarter", "2009-01-01", 20), ("month", "2009-01-01", 60), ("day", "2009-01-01", 354),
])
def test_date_trunc_is_an_iso_date_on_sqlite(unit, first, count):
    rows = _periods(_func("DATE_TRUNC", _lit(unit), DATE))

    assert rows[0][0] == first
    assert len(rows) == count


def test_quarter_truncation_lands_on_the_quarter_start():
    rows = _periods(_func("DATE_TRUNC", _lit("quarter"), DATE))

    assert {r[0][5:] for r in rows} == {"01-01", "04-01", "07-01", "10-01"}


@pytest.mark.parametrize("raw, unit", [
    (_func("MONTH", DATE), "month"),
    (_func("DAY", DATE), "day"),
    (_func("QUARTER", DATE), "quarter"),
    (_func("EXTRACT", _lit("year"), DATE), "year"),
    (_func("DATE_TRUNC", DATE, _lit("month")), "month"),
])
def test_raw_date_functions_a_model_writes_are_normalised(raw, unit):
    portable = _func("DATE_TRUNC" if raw.func_name == "DATE_TRUNC" else "DATE_PART", _lit(unit), DATE)

    assert _periods(raw) == _periods(portable)


def test_a_year_filter_compares_integers():
    where = Expr(kind="binary", op="=", left=_func("DATE_PART", _lit("year"), DATE), right=_lit(2011))
    rows = _periods(_func("DATE_PART", _lit("month"), DATE), where)

    assert len(rows) == 12


# --- each adapter renders the same plan in its own dialect ------------------------


def _adapter(cls):
    """An adapter instance with no connection: rendering needs only its dialect."""
    adapter = cls.__new__(cls)
    adapter._row_limit = 1000
    return adapter


@pytest.mark.parametrize("adapter, part, trunc", [
    (_adapter(PostgresAdapter),
     "CAST(EXTRACT(YEAR FROM t1.InvoiceDate) AS INT)", "TO_CHAR(DATE_TRUNC('MONTH', t1.InvoiceDate), 'YYYY-MM-DD')"),
    (_adapter(MysqlAdapter),
     "CAST(EXTRACT(YEAR FROM t1.InvoiceDate) AS SIGNED)", "DATE_FORMAT("),
    (_adapter(MssqlAdapter),
     "CAST(DATEPART(YEAR, t1.InvoiceDate) AS INTEGER)", "FORMAT(DATETRUNC(MONTH, t1.InvoiceDate), 'yyyy-MM-dd')"),
    (_adapter(DuckdbAdapter),
     "CAST(EXTRACT(YEAR FROM t1.InvoiceDate) AS INT)", "STRFTIME(DATE_TRUNC('MONTH', t1.InvoiceDate), '%Y-%m-%d')"),
])
def test_default_render_uses_sqlglot_for_the_adapter_dialect(adapter, part, trunc):
    sql = _sql(PlanModel(
        tables=[TableRef(name="Invoice", alias="t1", ordinal=0)],
        select_items=[
            SelectItem(ordinal=0, alias="y", expr=_func("DATE_PART", _lit("year"), DATE)),
            SelectItem(ordinal=1, alias="m", expr=_func("DATE_TRUNC", _lit("month"), DATE)),
        ],
    ), adapter)

    assert part in sql
    assert trunc in sql


def test_duckdb_returns_the_same_types_as_sqlite():
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect()
    conn.execute("CREATE TABLE Invoice (InvoiceDate TIMESTAMP, Total DOUBLE)")
    conn.execute("INSERT INTO Invoice VALUES ('2009-05-17 00:00:00', 1.0)")
    adapter = _adapter(DuckdbAdapter)
    sql = _sql(PlanModel(
        tables=[TableRef(name="Invoice", alias="t1", ordinal=0)],
        select_items=[
            SelectItem(ordinal=0, alias="y", expr=_func("DATE_PART", _lit("year"), DATE)),
            SelectItem(ordinal=1, alias="q", expr=_func("DATE_PART", _lit("quarter"), DATE)),
            SelectItem(ordinal=2, alias="m", expr=_func("DATE_TRUNC", _lit("quarter"), DATE)),
        ],
    ), adapter)

    assert conn.execute(sql).fetchall() == [(2009, 2, "2009-04-01")]


def test_an_adapter_without_the_hook_falls_back_to_its_dialect():
    legacy = SimpleNamespace(row_limit=1000, get_dialect=lambda: "postgres")

    sql = _sql(_revenue_by(_func("DATE_PART", _lit("year"), DATE)), legacy)

    assert "EXTRACT(YEAR FROM t1.InvoiceDate)" in sql


def test_the_generator_hands_the_expression_to_the_adapter():
    adapter = SimpleNamespace(row_limit=1000, get_dialect=lambda: "sqlite",
                              render_sql=MagicMock(return_value="SELECT 1"))

    assert _sql(_revenue_by(_func("DATE_PART", _lit("year"), DATE)), adapter) == "SELECT 1"
    rendered = adapter.render_sql.call_args.args[0]
    assert rendered.find(__import__("sqlglot").exp.Extract) is not None


# --- the logical validator checks the units ---------------------------------------


def _validate(expr):
    plan = PlanModel(tables=[TableRef(name="Invoice", alias="t1", ordinal=0)],
                     select_items=[SelectItem(ordinal=0, alias="p", expr=expr)])
    return LogicalValidatorNode._date_operations(plan)


@pytest.mark.parametrize("expr", [
    _func("DATE_PART", _lit("year"), DATE),
    _func("DATE_TRUNC", _lit("Quarter"), DATE),
    _func("YEAR", DATE),
])
def test_validator_accepts_portable_units(expr):
    assert _validate(expr) is None


@pytest.mark.parametrize("expr", [
    _func("DATE_PART", _lit("week"), DATE),
    _func("DATE_TRUNC", _lit("hour"), DATE),
    _func("DATE_PART", DATE),
    _func("DATE_TRUNC", DATE, DATE),
])
def test_validator_rejects_other_units_and_shapes(expr):
    error = _validate(expr)

    assert error is not None
    assert "DATE_PART" in error.message and "year, quarter, month, day" in error.message


# --- the planner prompt is the same for every database ---------------------------


def test_planner_prompt_describes_the_portable_operations_and_no_dialect():
    prompt = PLANNER_SYSTEM_PROMPT.lower()

    assert "date_part(" in prompt and "date_trunc(" in prompt
    for dialect in ("sqlite", "postgres", "mysql", "t-sql", "duckdb", "strftime"):
        assert dialect not in prompt


# --- every other known function is a typed sqlglot node too -----------------------


def test_known_functions_render_in_the_adapter_dialect():
    name = Expr(kind="column", alias="t1", column_name="Name")
    plan = PlanModel(
        tables=[TableRef(name="Artist", alias="t1", ordinal=0)],
        select_items=[SelectItem(ordinal=0, alias="n", expr=_func("LENGTH", name))],
    )

    assert "LEN(t1.Name)" in _sql(plan, _adapter(MssqlAdapter))
    assert "LENGTH(t1.Name)" in _sql(plan, _sqlite_adapter())


def test_a_function_built_as_an_operator_keeps_its_operands_grouped():
    total = Expr(kind="column", alias="t1", column_name="Total")
    sum_ = Expr(kind="binary", op="+", left=total, right=_lit(1))
    plan = PlanModel(
        tables=[TableRef(name="Invoice", alias="t1", ordinal=0)],
        select_items=[SelectItem(ordinal=0, alias="m", expr=_func("MOD", sum_, _lit(3)))],
    )

    assert "(t1.Total + 1) % 3" in _sql(plan, _sqlite_adapter())
