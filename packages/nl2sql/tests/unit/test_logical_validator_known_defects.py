"""Two defects in ``LogicalValidatorNode``, found by moving the demo to Chinook.

Both are pinned with ``xfail(strict=True)``: they fail today, and the day
either is fixed this file turns red so the record is removed with the fix
rather than rotting. Neither is fixed here -- they are engine changes, not
part of retiring the manufacturing demo.

Both hid behind the old demo dataset. Its DDL declared no foreign keys, so the
validator rejected every join before reaching the ORDER BY, and no end-to-end
test ever put a literal filter through validation at all.
"""

from types import SimpleNamespace

import pytest

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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "LogicalValidatorNode._build_validation_query calls Select.order_by() with a "
        "bare expression. sqlglot only wraps a *parsed* argument in exp.Ordered, so the "
        "Anonymous node SqlVisitor emits for every function lands directly in "
        "Order.expressions, and qualify()'s positional-reference expansion then reads "
        "its string `this` as an expression: AttributeError 'str' object has no "
        "attribute 'is_int', reported as VALIDATOR_CRASH. GeneratorNode passes desc=, "
        "which makes sqlglot wrap it, so only validation is affected -- and validation "
        "runs first, so no 'top N by <aggregate>' plan ever reaches SQL."
    ),
)
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The validator treats Column.stats['sample_values'] as an exhaustive allowlist "
        "for = and IN. The SQLAlchemy adapter collects five sample values per text "
        "column (_get_sample_values(limit=5)), so on any higher-cardinality text column "
        "a correct equality filter on a real row value is rejected with "
        "INVALID_PLAN_STRUCTURE, and the refiner cannot recover from it."
    ),
)
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
