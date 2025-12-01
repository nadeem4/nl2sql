import pytest
from nl2sql.nodes.validator_node import ValidatorNode
from nl2sql.schemas import GraphState, SchemaInfo, TableInfo, ColumnRef, PlanModel, TableRef

@pytest.fixture
def mock_schema():
    return SchemaInfo(
        tables=[
            TableInfo(name="users", alias="u", columns=["u.id", "u.name", "u.email"]),
            TableInfo(name="orders", alias="o", columns=["o.id", "o.user_id", "o.amount"])
        ]
    )

def test_validator_valid_plan(mock_schema):
    validator = ValidatorNode()
    plan = PlanModel(
        tables=[TableRef(name="users", alias="u")],
        select_columns=[ColumnRef(expr="u.name")],
        filters=[],
        joins=[],
        group_by=[],
        order_by=[],
        having=[]
    )
    state = GraphState(user_query="q", schema_info=mock_schema, plan=plan.model_dump())
    
    new_state = validator(state)
    assert not new_state.errors

def test_validator_invalid_table(mock_schema):
    validator = ValidatorNode()
    plan = PlanModel(
        tables=[TableRef(name="invalid_table", alias="x")],
        select_columns=[ColumnRef(expr="x.id")]
    )
    state = GraphState(user_query="q", schema_info=mock_schema, plan=plan.model_dump())
    
    new_state = validator(state)
    assert any("not found in schema" in e for e in new_state.errors)

def test_validator_alias_mismatch(mock_schema):
    validator = ValidatorNode()
    plan = PlanModel(
        tables=[TableRef(name="users", alias="x")], # Wrong alias, schema has 'u'
        select_columns=[ColumnRef(expr="x.name")]
    )
    state = GraphState(user_query="q", schema_info=mock_schema, plan=plan.model_dump())
    
    new_state = validator(state)
    assert any("not found in schema or alias mismatch" in e for e in new_state.errors)

def test_validator_invalid_column_alias_usage(mock_schema):
    validator = ValidatorNode()
    # Alias used in filter (not allowed)
    plan = PlanModel(
        tables=[TableRef(name="users", alias="u")],
        select_columns=[ColumnRef(expr="u.name", alias="user_name")],
        filters=[{"column": {"expr": "u.name", "alias": "user_name"}, "op": "=", "value": "John"}]
    )
    state = GraphState(user_query="q", schema_info=mock_schema, plan=plan.model_dump())
    
    new_state = validator(state)
    assert any("Aliases are only allowed in 'select_columns'" in e for e in new_state.errors)

def test_validator_invalid_column_name(mock_schema):
    validator = ValidatorNode()
    plan = PlanModel(
        tables=[TableRef(name="users", alias="u")],
        select_columns=[ColumnRef(expr="u.invalid_col")]
    )
    state = GraphState(user_query="q", schema_info=mock_schema, plan=plan.model_dump())
    
    new_state = validator(state)
    assert any("Column 'u.invalid_col' not found" in e for e in new_state.errors)
