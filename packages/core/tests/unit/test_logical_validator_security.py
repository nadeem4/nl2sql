from types import SimpleNamespace

from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel, TableRef, SelectItem, Expr, ASTPlannerResponse
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.schema import Table, Column
from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode


def test_logical_validator_enforces_policy_namespace():
    # Validates security enforcement because unauthorized tables must be blocked.
    # Arrange
    rbac = SimpleNamespace(get_allowed_tables=lambda _: ["ds1.allowed"])
    ctx = SimpleNamespace(ds_registry=SimpleNamespace(), rbac=rbac)
    node = LogicalValidatorNode(ctx)

    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="secret", alias="s", ordinal=0)],
        select_items=[SelectItem(expr=Expr(kind="column", alias="s", column_name="id"), ordinal=0)],
        joins=[],
    )
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[Table(name="secret", columns=[Column(name="id", type="int")])],
        ast_planner_response=ASTPlannerResponse(plan=plan),
        user_context=UserContext(roles=["user"]),
    )

    # Act
    result = node(state)

    # Assert
    assert any(e.error_code == ErrorCode.SECURITY_VIOLATION for e in result["errors"])
