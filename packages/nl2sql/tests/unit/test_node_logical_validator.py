from types import SimpleNamespace

import pytest

from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    PlanModel,
    TableRef,
    SelectItem,
    Expr,
    JoinSpec,
    ASTPlannerResponse,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery, ExpectedColumn
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.nodes.schema_retriever.schema import Table, Column
from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode
from nl2sql.common.settings import settings


def _col(alias: str, name: str):
    return Expr(kind="column", alias=alias, column_name=name)


def _ctx():
    rbac = SimpleNamespace(get_allowed_tables=lambda _ctx: ["*"])
    return SimpleNamespace(ds_registry=SimpleNamespace(), rbac=rbac)


def test_logical_validator_rejects_missing_plan():
    # Validates plan presence because validation must fail closed.
    # Arrange
    node = LogicalValidatorNode(_ctx())
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        user_context=UserContext(),
    )

    # Act
    result = node(state)

    # Assert
    assert result["errors"][0].error_code == ErrorCode.MISSING_PLAN


def test_logical_validator_detects_join_alias_mismatch():
    # Validates join alias checks because invalid joins must be blocked.
    # Arrange
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[SelectItem(expr=_col("u", "id"), ordinal=0)],
        joins=[
            JoinSpec(
                left_alias="u",
                right_alias="o",
                join_type="inner",
                ordinal=0,
                condition=Expr(kind="binary", op="=", left=_col("u", "id"), right=_col("o", "user_id")),
            )
        ],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[Table(name="users", columns=[Column(name="id", type="int")])],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    # Act
    result = node(state)

    # Assert
    assert any(e.error_code == ErrorCode.JOIN_TABLE_NOT_IN_PLAN for e in result["errors"])


def test_logical_validator_expected_schema_mismatch():
    # Validates expected schema enforcement because downstream expects strict columns.
    # Arrange
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[SelectItem(expr=_col("u", "id"), alias="user_id", ordinal=0)],
        joins=[],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(
            id="sq1",
            datasource_id="ds1",
            intent="q",
            expected_schema=[ExpectedColumn(name="id", dtype="int")],
        ),
        relevant_tables=[Table(name="users", columns=[Column(name="id", type="int")])],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    # Act
    result = node(state)

    # Assert
    assert any(e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE for e in result["errors"])


def test_logical_validator_duplicate_aliases():
    # Validates alias collision detection because duplicate aliases break scoping.
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[
            TableRef(name="users", alias="t", ordinal=0),
            TableRef(name="orders", alias="t", ordinal=1),
        ],
        select_items=[SelectItem(expr=_col("t", "id"), ordinal=0)],
        joins=[],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[
            Table(name="users", columns=[Column(name="id", type="int")]),
            Table(name="orders", columns=[Column(name="id", type="int")]),
        ],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    result = node(state)

    assert any(e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE for e in result["errors"])


def test_logical_validator_invalid_ordinals():
    # Validates ordinal checks because non-contiguous ordinals should be rejected.
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="users", alias="u", ordinal=1)],
        select_items=[SelectItem(expr=_col("u", "id"), ordinal=1)],
        joins=[],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[Table(name="users", columns=[Column(name="id", type="int")])],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    result = node(state)

    assert any(e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE for e in result["errors"])


def test_logical_validator_ambiguous_column_without_alias():
    # Validates ambiguous column detection when alias is omitted.
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[
            TableRef(name="users", alias="u", ordinal=0),
            TableRef(name="orders", alias="o", ordinal=1),
        ],
        select_items=[SelectItem(expr=Expr(kind="column", column_name="id"), ordinal=0)],
        joins=[],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[
            Table(name="users", columns=[Column(name="id", type="int")]),
            Table(name="orders", columns=[Column(name="id", type="int")]),
        ],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    result = node(state)

    assert any(e.error_code == ErrorCode.COLUMN_NOT_FOUND for e in result["errors"])


def test_logical_validator_column_not_found_strict_vs_warning(monkeypatch):
    # Validates strict columns toggle because severity depends on settings.
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[SelectItem(expr=_col("u", "missing"), ordinal=0)],
        joins=[],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[Table(name="users", columns=[Column(name="id", type="int")])],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    monkeypatch.setattr(settings, "logical_validator_strict_columns", False)
    node = LogicalValidatorNode(_ctx())
    result = node(state)
    assert any(e.error_code == ErrorCode.COLUMN_NOT_FOUND and e.severity.value == "WARNING" for e in result["errors"])

    monkeypatch.setattr(settings, "logical_validator_strict_columns", True)
    node = LogicalValidatorNode(_ctx())
    result = node(state)
    assert any(e.error_code == ErrorCode.COLUMN_NOT_FOUND and e.severity.value == "ERROR" for e in result["errors"])


def test_logical_validator_rejects_join_not_in_relationships():
    # Validates join relationship enforcement for deterministic planning.
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[
            TableRef(name="users", alias="u", ordinal=0),
            TableRef(name="orders", alias="o", ordinal=1),
        ],
        select_items=[SelectItem(expr=_col("u", "id"), ordinal=0)],
        joins=[
            JoinSpec(
                left_alias="u",
                right_alias="o",
                join_type="inner",
                ordinal=0,
                condition=Expr(
                    kind="binary",
                    op="=",
                    left=_col("u", "id"),
                    right=_col("o", "account_id"),
                ),
            )
        ],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[
            Table(
                name="users",
                columns=[Column(name="id", type="int")],
                relationships=[
                    {
                        "from_table": "users",
                        "to_table": "orders",
                        "from_columns": ["id"],
                        "to_columns": ["user_id"],
                    }
                ],
            ),
            Table(name="orders", columns=[Column(name="user_id", type="int")]),
        ],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    result = node(state)

    assert any(e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE for e in result["errors"])


def test_logical_validator_rejects_literal_not_in_stats():
    # Validates literal value enforcement using column stats.
    node = LogicalValidatorNode(_ctx())
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="orders", alias="o", ordinal=0)],
        select_items=[SelectItem(expr=_col("o", "status"), ordinal=0)],
        joins=[],
        where=Expr(
            kind="binary",
            op="=",
            left=_col("o", "status"),
            right=Expr(kind="literal", value="broken"),
        ),
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[
            Table(
                name="orders",
                columns=[
                    Column(
                        name="status",
                        type="string",
                        stats={"sample_values": ["active", "error", "maintenance"]},
                    )
                ],
            )
        ],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(),
    )

    result = node(state)

    assert any(e.error_code == ErrorCode.INVALID_PLAN_STRUCTURE for e in result["errors"])
