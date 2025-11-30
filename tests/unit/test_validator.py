import pytest
from nl2sql.nodes.validator_node import ValidatorNode
from nl2sql.schemas import GraphState, SchemaInfo, TableInfo, ColumnRef, PlanModel, TableRef

@pytest.fixture
def mock_schema():
    return SchemaInfo(
        tables=[
            TableInfo(name="users", alias="u", columns=["id", "name", "email"]),
            TableInfo(name="orders", alias="o", columns=["id", "user_id", "amount"])
        ]
    )

def test_validator_valid_plan(mock_schema):
    validator = ValidatorNode()
    plan = PlanModel(
        tables=[TableRef(name="users", alias="u")],
        select_columns=[ColumnRef(alias="u", name="name")],
        filters=[],
        joins=[],
        group_by=[],
        order_by=[],
        aggregates=[]
    )
    state = GraphState(user_query="q", schema_info=mock_schema, plan=plan.model_dump())
    
    new_state = validator(state)
    assert not new_state.errors

def test_validator_invalid_table(mock_schema):
    validator = ValidatorNode()
    plan = PlanModel(
        tables=[TableRef(name="invalid_table", alias="x")],
        select_columns=[ColumnRef(alias="x", name="id")]
    )
    state = GraphState(user_query="q", schema_info=mock_schema, plan=plan.model_dump())
    
    new_state = validator(state)
    assert any("does not exist in schema" in e for e in new_state.errors)

def test_validator_missing_alias(mock_schema):
    # Pydantic validation might catch this first if alias is mandatory in TableRef,
    # but let's test logic if it somehow passes or if we construct dict manually.
    validator = ValidatorNode()
    plan_dict = {
        "tables": [{"name": "users", "alias": ""}], # Empty alias
        "select_columns": []
    }
    state = GraphState(user_query="q", schema_info=mock_schema, plan=plan_dict)
    
    new_state = validator(state)
    assert any("must have an alias" in e for e in new_state.errors)

def test_validator_invalid_column_alias(mock_schema):
    validator = ValidatorNode()
    plan = PlanModel(
        tables=[TableRef(name="users", alias="u")],
        select_columns=[ColumnRef(alias="x", name="name")] # Alias 'x' not in tables
    )
    state = GraphState(user_query="q", schema_info=mock_schema, plan=plan.model_dump())
    
    new_state = validator(state)
    assert any("Alias 'x' in select_columns is not defined" in e for e in new_state.errors)

def test_validator_invalid_column_name(mock_schema):
    validator = ValidatorNode()
    plan = PlanModel(
        tables=[TableRef(name="users", alias="u")],
        select_columns=[ColumnRef(alias="u", name="invalid_col")]
    )
    state = GraphState(user_query="q", schema_info=mock_schema, plan=plan.model_dump())
    
    new_state = validator(state)
    assert any("Column 'invalid_col' does not exist" in e for e in new_state.errors)
