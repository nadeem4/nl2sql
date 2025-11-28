
import pytest
import json
from nl2sql.nodes.validator_node import ValidatorNode
from nl2sql.schemas import GraphState, SchemaInfo

def test_validator_detects_unqualified_hallucinations():
    """Test that validator catches columns not in schema, even if unqualified."""
    schema_info = SchemaInfo(
        tables=["users"],
        columns={"users": ["id", "name"]}
    )
    
    # 'age' is not in schema
    sql = "SELECT age FROM users LIMIT 10"
    
    state = GraphState(
        user_query="get ages",
        sql_draft={"sql": sql},
        schema_info=schema_info
    )
    
    validator = ValidatorNode()
    new_state = validator(state)
    
    assert new_state.errors
    assert "age (unqualified)" in new_state.errors[0] or "References missing columns" in new_state.errors[0]
    assert new_state.sql_draft is None

def test_validator_allows_valid_columns():
    """Test that validator allows valid columns."""
    schema_info = SchemaInfo(
        tables=["users"],
        columns={"users": ["id", "name"]}
    )
    sql = "SELECT name FROM users LIMIT 10"
    
    state = GraphState(
        user_query="get names",
        sql_draft={"sql": sql},
        schema_info=schema_info
    )
    
    validator = ValidatorNode()
    new_state = validator(state)
    
    assert not new_state.errors
    assert new_state.sql_draft is not None
