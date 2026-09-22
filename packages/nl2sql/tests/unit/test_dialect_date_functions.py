"""Date functions render for the target dialect, and the planner is told the dialect.

The first gpt-5.4 tier 2 run (``benchmarks/tier2/chinook/2026-09-22_1a8101d_gpt-5.4.json``)
crashed three date questions on SQLite, which has neither function:

* chinook_004 and 010 planned ``DATE_TRUNC('year'|'month', t1.InvoiceDate)``;
* chinook_014 planned ``YEAR(t1.InvoiceDate)``.

The planner was never told which database it plans for. It now is, with the
functions SQLite has for dates. The generator also renders the portable date
functions (``YEAR``, ``MONTH``, ``DAY``, ``DATE_TRUNC``, ``EXTRACT``) as sqlglot's
typed nodes, so each dialect gets its own spelling, and on SQLite, where sqlglot
has none, as ``STRFTIME``/``DATE`` with the same meaning.
"""
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import nl2sql
from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
from nl2sql.pipeline.nodes.ast_planner.prompts import PLANNER_PROMPT
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
from nl2sql.pipeline.nodes.generator.node import GeneratorNode, SqlVisitor
from nl2sql.pipeline.state import SubgraphExecutionState

CHINOOK = Path(nl2sql.__file__).resolve().parent / "cli" / "demo" / "data" / "chinook.sqlite"
DATE = Expr(kind="column", alias="t1", column_name="InvoiceDate")


def _func(name, *args):
    return Expr(kind="func", func_name=name, args=list(args))


def _lit(value):
    return Expr(kind="literal", value=value)


def _sql(plan, dialect="sqlite"):
    adapter = SimpleNamespace(row_limit=1000, get_dialect=lambda: dialect)
    ctx = SimpleNamespace(ds_registry=SimpleNamespace(get_adapter=lambda _id: adapter))
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="chinook", intent="q"),
        ast_planner_response=ASTPlannerResponse(plan=plan),
    )
    result = GeneratorNode(ctx)(state)
    assert not result.get("errors"), result.get("errors")
    return result["generator_response"].sql_draft


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


# --- the recorded crashes now run on SQLite --------------------------------------


def test_chinook_004_date_trunc_year_runs_on_sqlite():
    rows = _run(_sql(_revenue_by(_func("DATE_TRUNC", _lit("year"), DATE))))

    assert [r[0] for r in rows] == ["2009-01-01", "2010-01-01", "2011-01-01", "2012-01-01", "2013-01-01"]
    assert rows[0][1] == pytest.approx(449.46)


def test_chinook_010_date_trunc_month_runs_on_sqlite():
    usa_2013 = Expr(kind="binary", op="AND",
                    left=Expr(kind="binary", op="=", left=Expr(kind="column", alias="t1", column_name="BillingCountry"),
                              right=_lit("USA")),
                    right=Expr(kind="binary", op=">=", left=DATE, right=_lit("2013-01-01")))
    rows = _run(_sql(_revenue_by(_func("DATE_TRUNC", _lit("month"), DATE), usa_2013)))

    assert rows[0][0] == "2013-02-01"  # the first USA invoice of 2013 (gold: 2013-02)
    assert all(r[0].endswith("-01") for r in rows)


def test_chinook_014_year_runs_on_sqlite_as_an_integer():
    rows = _run(_sql(_revenue_by(_func("YEAR", DATE))))

    assert [r[0] for r in rows] == [2009, 2010, 2011, 2012, 2013]
    assert rows[-1][1] == pytest.approx(450.58)


@pytest.mark.parametrize("name, first", [("MONTH", 1), ("DAY", 1)])
def test_month_and_day_run_on_sqlite(name, first):
    assert _run(_sql(_revenue_by(_func(name, DATE))))[0][0] == first


def test_extract_year_runs_on_sqlite():
    rows = _run(_sql(_revenue_by(_func("EXTRACT", _lit("year"), DATE))))

    assert [r[0] for r in rows][:2] == [2009, 2010]


def test_strftime_is_left_as_written():
    sql = _sql(_revenue_by(_func("STRFTIME", _lit("%Y"), DATE)))

    assert "STRFTIME('%Y', t1.InvoiceDate)" in sql
    assert _run(sql)[0][0] == "2009"


# --- other dialects get their own spelling -----------------------------------------


@pytest.mark.parametrize("dialect, expected", [
    ("postgres", "EXTRACT(YEAR FROM t1.InvoiceDate)"),
    ("mysql", "YEAR(t1.InvoiceDate)"),
    ("tsql", "YEAR(t1.InvoiceDate)"),
])
def test_year_renders_per_dialect(dialect, expected):
    assert SqlVisitor().visit(_func("YEAR", DATE)).sql(dialect=dialect) == expected


@pytest.mark.parametrize("dialect, expected", [
    ("postgres", "DATE_TRUNC('MONTH', t1.InvoiceDate)"),
    ("tsql", "DATETRUNC(MONTH, t1.InvoiceDate)"),
    ("duckdb", "DATE_TRUNC('MONTH', t1.InvoiceDate)"),
])
def test_date_trunc_renders_per_dialect(dialect, expected):
    assert SqlVisitor().visit(_func("DATE_TRUNC", _lit("month"), DATE)).sql(dialect=dialect) == expected


def test_an_unknown_function_is_still_passed_through():
    assert SqlVisitor().visit(_func("JULIANDAY", DATE)).sql(dialect="sqlite") == "JULIANDAY(t1.InvoiceDate)"


# --- the planner is told the dialect ------------------------------------------------


def _planner(dialect):
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = SimpleNamespace(llm_registry=MagicMock(),
                          ds_registry=SimpleNamespace(get_dialect=lambda _id: dialect))
    ctx.llm_registry.get_llm.return_value = llm
    node = ASTPlannerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = PlanModel(tables=[], select_items=[])
    return node


def _system_message(node):
    node(SubgraphExecutionState(trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="chinook", intent="q")))
    values = node.chain.invoke.call_args.args[0]
    return PLANNER_PROMPT.format_messages(**values)[0].content


def test_the_planner_is_told_it_plans_for_sqlite_and_how_to_read_dates():
    system = _system_message(_planner("sqlite"))

    assert "sqlite" in system
    assert "STRFTIME('%Y'" in system
    assert "no DATE_TRUNC" in system


def test_another_dialect_is_named_without_the_sqlite_notes():
    system = _system_message(_planner("postgresql"))

    assert "postgresql" in system
    assert "STRFTIME" not in system


def test_the_prompt_renders_without_a_dialect():
    # Callers that format the template directly (tests, the token script) need not pass one.
    messages = PLANNER_PROMPT.format_messages(examples="", relevant_tables="", expected_schema="",
                                              semantic_context="", feedback="", user_query="q")

    assert "[RELEVANT_TABLES]" in messages[0].content
