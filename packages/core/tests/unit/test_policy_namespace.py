
import pytest
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.nodes.planner.schemas import PlanModel, TableRef
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.datasources import DatasourceRegistry

@pytest.fixture
def validator():
    # Registry not strictly needed for _validate_policy but required for init
    return LogicalValidatorNode(registry=DatasourceRegistry([]))

def create_state(datasource_id, plan_tables, allowed_tables, role="test_role"):
    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name=t, alias=t, ordinal=i) for i, t in enumerate(plan_tables)],
        joins=[],
        select_items=[],
        group_by=[],
        order_by=[]
    )
    return GraphState(
        user_query="test",
        plan=plan,
        selected_datasource_id=datasource_id,
        user_context={
            "role": role,
            "allowed_tables": allowed_tables
        }
    )

def test_missing_datasource_id_fails_closed(validator):
    state = create_state(None, ["t1"], ["*"])
    errors = validator._validate_policy(state)
    assert len(errors) == 1
    assert errors[0].message == "Security Enforcement Failed: No 'selected_datasource_id' in state."

def test_global_wildcard_allows_all(validator):
    state = create_state("ds1", ["t1", "t2"], ["*"])
    errors = validator._validate_policy(state)
    assert len(errors) == 0

def test_strict_namespace_match_passes(validator):
    state = create_state("ds1", ["orders"], ["ds1.orders"])
    errors = validator._validate_policy(state)
    assert len(errors) == 0

def test_legacy_policy_fails_in_strict_mode(validator):
    # Policy has "orders" (legacy), but Validator demands "ds1.orders"
    state = create_state("ds1", ["orders"], ["orders"])
    errors = validator._validate_policy(state)
    
    assert len(errors) == 1
    assert "denied access" in errors[0].message
    assert "Policy requires explicit 'datasource.table'" in errors[0].message
    assert "ds1.orders" in errors[0].message

def test_cross_datasource_access_fails(validator):
    # Policy allows "ds1.orders", but we are accessing "ds2.orders"
    state = create_state("ds2", ["orders"], ["ds1.orders"])
    errors = validator._validate_policy(state)
    
    assert len(errors) == 1
    assert "ds2.orders" in errors[0].message

def test_datasource_wildcard_passes(validator):
    state = create_state("ds1", ["orders", "users"], ["ds1.*"])
    errors = validator._validate_policy(state)
    assert len(errors) == 0

def test_partial_access_fails(validator):
    # One allowed, one denied
    state = create_state("ds1", ["orders", "secrets"], ["ds1.orders"])
    errors = validator._validate_policy(state)
    
    assert len(errors) == 1
    assert "ds1.secrets" in errors[0].message
