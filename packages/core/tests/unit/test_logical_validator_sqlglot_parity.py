"""Parity tests for column resolution in ``LogicalValidatorNode``.

These lock the observable behaviour of logical validation (error codes,
severities and message content) so the switch from the hand-written
``ValidatorVisitor`` to ``sqlglot``'s ``qualify()`` optimizer stays a pure
refactor. They are written to pass both before and after that change.
"""

from types import SimpleNamespace

import pytest

from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode, ErrorSeverity
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    ASTPlannerResponse,
    Expr,
    JoinSpec,
    PlanModel,
    SelectItem,
    TableRef,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import SubgraphExecutionState


USERS_TO_ORDERS = {
    "from_table": "users",
    "to_table": "orders",
    "from_columns": ["id"],
    "to_columns": ["user_id"],
}


def _col(alias, name):
    return Expr(kind="column", alias=alias, column_name=name)


def _ctx(allowed=("*",)):
    rbac = SimpleNamespace(get_allowed_tables=lambda _ctx: list(allowed))
    return SimpleNamespace(ds_registry=SimpleNamespace(), rbac=rbac)


def _tables():
    return [
        Table(
            name="users",
            columns=[Column(name="id", type="int"), Column(name="name", type="string")],
            relationships=[USERS_TO_ORDERS],
        ),
        Table(
            name="orders",
            columns=[
                Column(name="id", type="int"),
                Column(name="user_id", type="int"),
                Column(name="total", type="float"),
            ],
        ),
    ]


def _state(plan, tables=None, roles=("admin",)):
    return SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=_tables() if tables is None else tables,
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(roles=list(roles)),
    )


def _joined_plan(select_items, joins=None):
    return PlanModel(
        query_type="READ",
        tables=[
            TableRef(name="users", alias="u", ordinal=0),
            TableRef(name="orders", alias="o", ordinal=1),
        ],
        select_items=select_items,
        joins=[
            JoinSpec(
                left_alias="u",
                right_alias="o",
                join_type="inner",
                ordinal=0,
                condition=Expr(
                    kind="binary", op="=", left=_col("u", "id"), right=_col("o", "user_id")
                ),
            )
        ]
        if joins is None
        else joins,
    )


def _codes(result):
    return [e.error_code for e in result["errors"]]


def _messages(result):
    return " | ".join(e.message for e in result["errors"])


def test_unknown_column_is_reported_with_column_and_alias():
    # Validates that a qualified column missing from the schema is rejected.
    node = LogicalValidatorNode(_ctx())
    plan = _joined_plan([SelectItem(expr=_col("o", "nope"), ordinal=0)])

    result = node(_state(plan))

    assert ErrorCode.COLUMN_NOT_FOUND in _codes(result)
    message = _messages(result)
    assert "nope" in message
    assert "o" in message


def test_ambiguous_column_across_join_is_reported():
    # Validates ambiguity detection: 'id' exists in both joined tables.
    node = LogicalValidatorNode(_ctx())
    plan = _joined_plan(
        [SelectItem(expr=Expr(kind="column", column_name="id"), ordinal=0)]
    )

    result = node(_state(plan))

    assert ErrorCode.COLUMN_NOT_FOUND in _codes(result)
    message = _messages(result)
    assert "id" in message
    assert "mbiguous" in message


def test_valid_qualified_reference_across_join_passes():
    # Validates that a correct plan produces no blocking errors.
    node = LogicalValidatorNode(_ctx())
    plan = _joined_plan(
        [
            SelectItem(expr=_col("u", "name"), ordinal=0),
            SelectItem(expr=_col("o", "total"), ordinal=1),
        ]
    )

    result = node(_state(plan))

    assert not [
        e
        for e in result["errors"]
        if e.severity in (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL)
    ]


@pytest.mark.parametrize("star_alias", [None, "u"])
def test_select_star_is_accepted(star_alias):
    # Validates that wildcards are not treated as missing columns.
    node = LogicalValidatorNode(_ctx())
    plan = _joined_plan(
        [SelectItem(expr=Expr(kind="column", alias=star_alias, column_name="*"), ordinal=0)]
    )

    result = node(_state(plan))

    assert ErrorCode.COLUMN_NOT_FOUND not in _codes(result)


def test_undeclared_alias_is_reported():
    # Validates that a column bound to an alias absent from the plan is rejected.
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[SelectItem(expr=_col("x", "id"), ordinal=0)],
        joins=[],
    )

    result = node(_state(plan))

    assert ErrorCode.COLUMN_NOT_FOUND in _codes(result)
    message = _messages(result)
    assert "x" in message
    assert "id" in message


def test_column_present_in_only_one_joined_table_resolves_without_alias():
    # Validates that an unambiguous unqualified column is accepted.
    node = LogicalValidatorNode(_ctx())
    plan = _joined_plan(
        [SelectItem(expr=Expr(kind="column", column_name="total"), ordinal=0)]
    )

    result = node(_state(plan))

    assert ErrorCode.COLUMN_NOT_FOUND not in _codes(result)


def test_column_errors_in_where_and_having_are_reported():
    # Validates that resolution covers predicate clauses, not just SELECT.
    node = LogicalValidatorNode(_ctx())
    plan = _joined_plan([SelectItem(expr=_col("u", "name"), ordinal=0)])
    plan.where = Expr(
        kind="binary",
        op="=",
        left=_col("u", "ghost"),
        right=Expr(kind="literal", value=1),
    )

    result = node(_state(plan))

    assert ErrorCode.COLUMN_NOT_FOUND in _codes(result)
    assert "ghost" in _messages(result)


def test_rbac_denial_still_fires_when_column_resolution_fails():
    # Security: policy enforcement must never be skipped because logical
    # validation already failed.
    node = LogicalValidatorNode(_ctx(allowed=["ds1.allowed"]))
    plan = _joined_plan([SelectItem(expr=_col("o", "nope"), ordinal=0)])

    result = node(_state(plan, roles=["user"]))

    codes = _codes(result)
    assert ErrorCode.SECURITY_VIOLATION in codes
    assert ErrorCode.COLUMN_NOT_FOUND in codes
    assert any(
        e.error_code == ErrorCode.SECURITY_VIOLATION
        and e.severity == ErrorSeverity.CRITICAL
        for e in result["errors"]
    )


def test_missing_table_is_reported_as_table_not_found():
    # sqlglot's qualify() ignores relations absent from the schema, so the
    # validator keeps its own table-existence check.
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="ghosts", alias="g", ordinal=0)],
        select_items=[SelectItem(expr=_col("g", "id"), ordinal=0)],
        joins=[],
    )

    result = node(_state(plan))

    assert ErrorCode.TABLE_NOT_FOUND in _codes(result)
    assert "ghosts" in _messages(result)


def test_rbac_denial_still_fires_when_static_validation_crashes(monkeypatch):
    # Security: an unexpected failure inside structural/column validation must
    # not short-circuit policy enforcement.
    node = LogicalValidatorNode(_ctx(allowed=["ds1.allowed"]))
    monkeypatch.setattr(
        LogicalValidatorNode,
        "_validate_static",
        lambda self, state: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    plan = _joined_plan([SelectItem(expr=_col("u", "name"), ordinal=0)])

    result = node(_state(plan, roles=["user"]))

    codes = _codes(result)
    assert ErrorCode.SECURITY_VIOLATION in codes
    assert ErrorCode.VALIDATOR_CRASH in codes
