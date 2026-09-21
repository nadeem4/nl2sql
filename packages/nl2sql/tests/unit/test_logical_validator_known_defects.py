"""Regression tests for two fixed defects in ``LogicalValidatorNode``.

Both were found by moving the demo to Chinook and were pinned here with
``xfail(strict=True)`` until they were fixed; the markers came off with the
fix, so these are now ordinary regression tests.

Both hid behind the old demo dataset. Its DDL declared no foreign keys, so the
validator rejected every join before reaching the ORDER BY, and no end-to-end
test ever put a literal filter through validation at all.
"""

from types import SimpleNamespace

from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode
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
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import SubgraphExecutionState


def _ctx():
    rbac = SimpleNamespace(get_allowed_tables=lambda _ctx: ["*"])
    return SimpleNamespace(ds_registry=SimpleNamespace(), rbac=rbac)


def _state(plan, tables):
    return SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=tables,
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(roles=["admin"]),
    )


def _codes(result):
    return [e.error_code for e in result["errors"]]


def test_order_by_over_a_function_does_not_crash_the_validator():
    tables = [Table(name="orders", columns=[Column(name="id", type="int")])]
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="orders", alias="o", ordinal=0)],
        select_items=[
            SelectItem(
                ordinal=0,
                alias="n",
                expr=Expr(
                    kind="func",
                    func_name="COUNT",
                    is_aggregate=True,
                    args=[Expr(kind="column", alias="o", column_name="id")],
                ),
            )
        ],
        group_by=[GroupByItem(ordinal=0, expr=Expr(kind="column", alias="o", column_name="id"))],
        order_by=[
            OrderItem(
                ordinal=0,
                direction="desc",
                expr=Expr(
                    kind="func",
                    func_name="COUNT",
                    is_aggregate=True,
                    args=[Expr(kind="column", alias="o", column_name="id")],
                ),
            )
        ],
    )

    result = LogicalValidatorNode(_ctx())(_state(plan, tables))

    assert ErrorCode.VALIDATOR_CRASH not in _codes(result), _codes(result)


def test_an_equality_filter_on_a_real_value_outside_the_sample_is_not_rejected():
    tables = [
        Table(
            name="artist",
            columns=[
                Column(name="artistid", type="int"),
                Column(
                    name="name",
                    type="string",
                    # Exactly what the adapter records: five of 275 real values.
                    stats={"sample_values": ["AC/DC", "Accept", "Aerosmith", "Alanis Morissette", "Alice In Chains"]},
                ),
            ],
        )
    ]
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="artist", alias="a", ordinal=0)],
        select_items=[SelectItem(ordinal=0, expr=Expr(kind="column", alias="a", column_name="artistid"))],
        where=Expr(
            kind="binary",
            op="=",
            left=Expr(kind="column", alias="a", column_name="name"),
            # A real Artist.Name, simply not one of the five sampled.
            right=Expr(kind="literal", value="Iron Maiden"),
        ),
    )

    result = LogicalValidatorNode(_ctx())(_state(plan, tables))

    assert ErrorCode.INVALID_PLAN_STRUCTURE not in _codes(result), [e.message for e in result["errors"]]
