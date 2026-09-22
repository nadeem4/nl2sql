"""String concatenation renders as each dialect's concatenation, never as ``+``.

The first gpt-5.4 tier 2 run (``benchmarks/tier2/chinook/2026-09-22_1a8101d_gpt-5.4.json``)
planned a customer's full name as ``(t1.FirstName + ' ') + t1.LastName``. SQLite
reads ``+`` as numeric addition, so every name became ``0``: GROUP BY collapsed
all 59 customers into one row (chinook_002, 007, 012, 013) and chinook_036
answered "who does Jane Peacock report to?" with ``0``.

``+`` with a string literal on either side, or with another concatenation
under it, can only mean concatenation, so it renders as one. The plan can also
say so directly with the ``||`` operator.
"""
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlglot import expressions as exp

import nl2sql
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    ASTPlannerResponse,
    Expr,
    GroupByItem,
    JoinSpec,
    OrderItem,
    PlanModel,
    SelectItem,
    TableRef,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.generator.node import GeneratorNode, SqlVisitor
from nl2sql.pipeline.state import SubgraphExecutionState

CHINOOK = Path(nl2sql.__file__).resolve().parent / "cli" / "demo" / "data" / "chinook.sqlite"


def _col(alias, name):
    return Expr(kind="column", alias=alias, column_name=name)


def _str(value):
    return Expr(kind="literal", value=value)


def _plus(left, right, op="+"):
    return Expr(kind="binary", op=op, left=left, right=right)


def _full_name(alias="t1", op="+"):
    # (t1.FirstName + ' ') + t1.LastName, the shape in the record.
    return _plus(_plus(_col(alias, "FirstName"), _str(" "), op), _col(alias, "LastName"), op)


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


def _top_customers_plan(op="+"):
    """chinook_002's recorded plan shape: top 5 customers by total spend, name concatenated."""
    total = Expr(kind="func", func_name="SUM", is_aggregate=True, args=[_col("t2", "Total")])
    return PlanModel(
        tables=[TableRef(name="Customer", alias="t1", ordinal=0), TableRef(name="Invoice", alias="t2", ordinal=1)],
        joins=[JoinSpec(left_alias="t1", right_alias="t2", ordinal=0,
                        condition=_plus(_col("t1", "CustomerId"), _col("t2", "CustomerId"), "="))],
        select_items=[SelectItem(ordinal=0, expr=_full_name(op=op), alias="customer"),
                      SelectItem(ordinal=1, expr=total, alias="total_spend")],
        group_by=[GroupByItem(ordinal=0, expr=_full_name(op=op))],
        order_by=[OrderItem(ordinal=0, direction="desc", expr=total)],
        limit=5,
    )


def test_the_recorded_plus_on_names_concatenates_on_sqlite():
    sql = _sql(_top_customers_plan())

    assert "||" in sql and "+ ' '" not in sql
    rows = _run(sql)
    assert len(rows) == 5
    assert rows[0] == ("Helena Holý", pytest.approx(49.62))


def test_the_recorded_self_join_names_the_manager():
    # chinook_036: Who does Jane Peacock report to?
    plan = PlanModel(
        tables=[TableRef(name="Employee", alias="t1", ordinal=0), TableRef(name="Employee", alias="t2", ordinal=1)],
        joins=[JoinSpec(left_alias="t1", right_alias="t2", ordinal=0,
                        condition=_plus(_col("t1", "ReportsTo"), _col("t2", "EmployeeId"), "="))],
        select_items=[SelectItem(ordinal=0, expr=_full_name("t2"), alias="manager_name")],
        where=_plus(_plus(_col("t1", "FirstName"), _str("Jane"), "="),
                    _plus(_col("t1", "LastName"), _str("Peacock"), "="), "AND"),
    )

    assert _run(_sql(plan)) == [("Nancy Edwards",)]


def test_the_concat_operator_renders_for_each_dialect():
    visited = SqlVisitor().visit(_full_name(op="||"))

    assert visited.sql(dialect="sqlite") == "(t1.FirstName || ' ') || t1.LastName"
    assert visited.sql(dialect="postgres") == "(t1.FirstName || ' ') || t1.LastName"
    assert "CONCAT(" in visited.sql(dialect="mysql")


def test_the_concat_operator_plan_runs_on_chinook():
    rows = _run(_sql(_top_customers_plan(op="||")))

    assert rows[0] == ("Helena Holý", pytest.approx(49.62))


def test_plus_on_numbers_stays_addition():
    visited = SqlVisitor().visit(_plus(_col("t1", "UnitPrice"), Expr(kind="literal", value=1)))

    assert isinstance(visited, exp.Add)


def test_plus_on_two_bare_columns_stays_addition():
    # Without a type there is no telling two text columns from two numbers.
    visited = SqlVisitor().visit(_plus(_col("t1", "a"), _col("t1", "b")))

    assert isinstance(visited, exp.Add)


def test_plus_with_a_numeric_string_literal_stays_addition():
    visited = SqlVisitor().visit(_plus(_col("t1", "Total"), _str("1.5")))

    assert isinstance(visited, exp.Add)


def test_the_planner_is_told_to_concatenate_with_the_concat_operator():
    from nl2sql.pipeline.nodes.ast_planner.prompts import PLANNER_SYSTEM_PROMPT

    assert '"||"' in PLANNER_SYSTEM_PROMPT and 'never "+"' in PLANNER_SYSTEM_PROMPT
