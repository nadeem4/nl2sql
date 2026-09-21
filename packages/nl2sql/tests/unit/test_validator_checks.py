"""The validator reports what passed, not only what failed.

A UI needs a per-check record ("structure_and_schema passed, policy denied"),
so ``LogicalValidatorResponse`` carries a ``checks`` list alongside ``errors``.
"""

from types import SimpleNamespace

import pytest

from nl2sql.auth import UserContext
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    ASTPlannerResponse,
    Expr,
    PlanModel,
    SelectItem,
    TableRef,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import SubgraphExecutionState


def _col(alias: str, name: str) -> Expr:
    return Expr(kind="column", alias=alias, column_name=name)


def _ctx(allowed_tables):
    rbac = SimpleNamespace(get_allowed_tables=lambda _ctx: list(allowed_tables))
    return SimpleNamespace(ds_registry=SimpleNamespace(), rbac=rbac)


def _plan() -> PlanModel:
    return PlanModel(
        query_type="READ",
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[SelectItem(expr=_col("u", "id"), ordinal=0)],
        joins=[],
    )


def _state(roles) -> SubgraphExecutionState:
    return SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        relevant_tables=[Table(name="users", columns=[Column(name="id", type="int")])],
        ast_planner_response=ASTPlannerResponse(plan=_plan()),
        user_context=UserContext(roles=list(roles)),
    )


@pytest.fixture
def validator_node():
    return LogicalValidatorNode(_ctx(["*"]))


@pytest.fixture
def valid_state():
    return _state(["admin"])


@pytest.fixture
def denying_validator_node():
    # A role whose allowlist covers nothing in this plan.
    return LogicalValidatorNode(_ctx(["other_ds.*"]))


@pytest.fixture
def valid_state_as_viewer_denied():
    return _state(["viewer"])


def test_successful_validation_lists_three_passed_checks(validator_node, valid_state):
    out = validator_node(valid_state)
    checks = out["logical_validator_response"].checks
    assert [c.name for c in checks] == ["plan_present", "structure_and_schema", "policy"]
    assert all(c.passed for c in checks)


def test_policy_denial_marks_policy_check_failed(
    denying_validator_node, valid_state_as_viewer_denied
):
    out = denying_validator_node(valid_state_as_viewer_denied)
    by_name = {c.name: c for c in out["logical_validator_response"].checks}
    assert by_name["structure_and_schema"].passed is True
    assert by_name["policy"].passed is False
    # The default refusal is generic: it names no table (see test_rbac_strict_refusal.py).
    assert by_name["policy"].message == "You do not have permission to see the data this question requires."


def test_missing_plan_marks_plan_present_failed(validator_node):
    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        user_context=UserContext(),
    )

    out = validator_node(state)

    checks = out["logical_validator_response"].checks
    assert [c.name for c in checks] == ["plan_present"]
    assert checks[0].passed is False
