
import pytest
from unittest.mock import MagicMock

from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel, TableRef, ASTPlannerResponse
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.common.errors import ErrorCode
from nl2sql.auth import UserContext

@pytest.fixture
def validator():
    ctx = MagicMock()
    ctx.ds_registry = MagicMock()
    ctx.rbac = MagicMock()
    return LogicalValidatorNode(ctx=ctx)

def create_state(datasource_id, plan_tables, role="test_role"):
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name=t, alias=t, ordinal=i) for i, t in enumerate(plan_tables)],
        joins=[],
        select_items=[],
        group_by=[],
        order_by=[]
    )
    return SubgraphExecutionState(
        ast_planner_response=ASTPlannerResponse(plan=plan),
        sub_query=SubQuery(id="sq1", datasource_id=datasource_id, intent="q") if datasource_id else None,
        user_context=UserContext(roles=[role]),
        trace_id="t",
    )

def test_missing_datasource_id_fails_closed(validator):
    validator.rbac.get_allowed_tables.return_value = ["*"]
    state = create_state(None, ["t1"])
    errors = validator._validate_policy(state)
    assert len(errors) == 1
    assert errors[0].message == "Security Enforcement Failed: No sub_query datasource_id in state."

def test_global_wildcard_allows_all(validator):
    validator.rbac.get_allowed_tables.return_value = ["*"]
    state = create_state("ds1", ["t1", "t2"])
    errors = validator._validate_policy(state)
    assert len(errors) == 0

def test_strict_namespace_match_passes(validator):
    validator.rbac.get_allowed_tables.return_value = ["ds1.orders"]
    state = create_state("ds1", ["orders"])
    errors = validator._validate_policy(state)
    assert len(errors) == 0

def test_legacy_policy_fails_in_strict_mode(validator):
    # Policy has "orders" (legacy), but Validator demands "ds1.orders"
    validator.rbac.get_allowed_tables.return_value = ["orders"]
    state = create_state("ds1", ["orders"])
    errors = validator._validate_policy(state)
    
    assert len(errors) == 1
    assert "denied access" in errors[0].message
    assert "Policy requires explicit 'datasource.table'" in errors[0].message
    assert "ds1.orders" in errors[0].message

def test_cross_datasource_access_fails(validator):
    # Policy allows "ds1.orders", but we are accessing "ds2.orders"
    validator.rbac.get_allowed_tables.return_value = ["ds1.orders"]
    state = create_state("ds2", ["orders"])
    errors = validator._validate_policy(state)
    
    assert len(errors) == 1
    assert "ds2.orders" in errors[0].message

def test_datasource_wildcard_passes(validator):
    validator.rbac.get_allowed_tables.return_value = ["ds1.*"]
    state = create_state("ds1", ["orders", "users"])
    errors = validator._validate_policy(state)
    assert len(errors) == 0

def test_partial_access_fails(validator):
    # One allowed, one denied
    validator.rbac.get_allowed_tables.return_value = ["ds1.orders"]
    state = create_state("ds1", ["orders", "secrets"])
    errors = validator._validate_policy(state)
    
    assert len(errors) == 1
    assert "ds1.secrets" in errors[0].message
