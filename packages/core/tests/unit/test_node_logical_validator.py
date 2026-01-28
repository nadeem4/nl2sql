from types import SimpleNamespace

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
from nl2sql.schema import Table, Column
from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode


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
