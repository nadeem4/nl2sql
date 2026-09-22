"""A select alias that is not a bare identifier is quoted.

The first gpt-5.4 tier 2 run (``benchmarks/tier2/chinook/2026-09-22_1a8101d_gpt-5.4.json``)
crashed chinook_037 on ``... AS sales support agent``: the decomposer named the
column "sales support agent", the planner used that name as the select alias
(the validator requires aliases to equal the expected column names), and the
generator printed it unquoted. SQLite answered ``near "support": syntax error``.
The alias also appears as an ORDER BY tie-breaker, which must be quoted the
same way.
"""
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import nl2sql
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    ASTPlannerResponse, Expr, OrderItem, PlanModel, SelectItem, TableRef,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.state import SubgraphExecutionState

CHINOOK = Path(nl2sql.__file__).resolve().parent / "cli" / "demo" / "data" / "chinook.sqlite"


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


def _plan(alias):
    return PlanModel(
        tables=[TableRef(name="Employee", alias="t1", ordinal=0)],
        select_items=[SelectItem(ordinal=0, alias=alias,
                                 expr=Expr(kind="column", alias="t1", column_name="LastName"))],
        where=Expr(kind="binary", op="=", left=Expr(kind="column", alias="t1", column_name="Title"),
                   right=Expr(kind="literal", value="Sales Support Agent")),
    )


def test_the_recorded_alias_with_spaces_is_quoted_and_runs():
    sql = _sql(_plan("sales support agent"))

    assert 'AS "sales support agent"' in sql
    assert 'ORDER BY "sales support agent"' in sql
    assert sorted(_run(sql)) == [("Johnson",), ("Park",), ("Peacock",)]


@pytest.mark.parametrize("dialect, quoted", [("postgres", '"sales support agent"'),
                                             ("mysql", "`sales support agent`"),
                                             ("tsql", "[sales support agent]")])
def test_the_alias_is_quoted_the_dialects_way(dialect, quoted):
    assert f"AS {quoted}" in _sql(_plan("sales support agent"), dialect)


def test_a_bare_identifier_alias_stays_unquoted():
    sql = _sql(_plan("last_name"))

    assert "AS last_name" in sql and '"' not in sql


def test_an_order_by_on_the_spaced_alias_is_quoted_too():
    plan = _plan("sales support agent").model_copy(update={"order_by": [
        OrderItem(ordinal=0, direction="desc", expr=Expr(kind="column", column_name="sales support agent"))]})

    sql = _sql(plan)

    assert 'ORDER BY "sales support agent" DESC' in sql
    assert _run(sql) == [("Peacock",), ("Park",), ("Johnson",)]
