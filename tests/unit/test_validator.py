
import pytest
from nl2sql.nodes.validator_node import ValidatorNode
from nl2sql.schemas import GraphState, SchemaInfo

def test_validator_detects_missing_columns():
    """Test that validator catches columns not in schema."""
    schema_info = SchemaInfo(
        tables=["users"],
        columns={"users": ["id", "name"]},
        aliases={"users": "u"}
    )
    
    # 'age' is not in schema
    plan = {
        "tables": [{"name": "users", "alias": "u"}],
        "needed_columns": ["u.age"],
        "select_columns": ["u.age"],
        "limit": 10
    }
    
    state = GraphState(
        user_query="get ages",
        plan=plan,
        schema_info=schema_info
    )
    
    validator = ValidatorNode()
    new_state = validator(state)
    
    assert new_state.errors
    assert "Column 'age' does not exist in table 'users'" in new_state.errors[0]

def test_validator_allows_valid_columns():
    """Test that validator allows valid columns."""
    schema_info = SchemaInfo(
        tables=["users"],
        columns={"users": ["id", "name"]},
        aliases={"users": "u"}
    )
    
    plan = {
        "tables": [{"name": "users", "alias": "u"}],
        "needed_columns": ["u.name"],
        "select_columns": ["u.name"],
        "limit": 10
    }
    
    state = GraphState(
        user_query="get names",
        plan=plan,
        schema_info=schema_info
    )
    
    validator = ValidatorNode()
    new_state = validator(state)
    
    assert not new_state.errors

