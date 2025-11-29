import pytest
from unittest.mock import MagicMock
import json
from nl2sql.nodes.planner import PlannerNode
from nl2sql.schemas import GraphState, SchemaInfo, PlanModel

def test_planner_validates_needed_columns():
    """Test that PlannerNode rejects plans with columns not in schema."""
    schema_info = SchemaInfo(
        tables=["users"],
        columns={"users": ["id", "name"]}
    )
    
    # Mock LLM to return a PlanModel object
    mock_llm = MagicMock()
    plan_data = {
        "tables": [{"name": "users"}],
        "needed_columns": ["users.id", "users.age"] # 'age' is invalid
    }
    # Create a real PlanModel with the data
    mock_plan = PlanModel(**plan_data)
    mock_llm.return_value = mock_plan
    
    node = PlannerNode(llm=mock_llm)
    
    state = GraphState(
        user_query="get user ages",
        schema_info=schema_info
    )
    
    new_state = node(state)
    
    assert new_state.plan is None
    assert new_state.errors
    assert "Plan references unknown columns" in new_state.errors[0]
    assert "users.age" in new_state.errors[0]

def test_planner_accepts_valid_columns():
    """Test that PlannerNode accepts valid plans."""
    schema_info = SchemaInfo(
        tables=["users"],
        columns={"users": ["id", "name"]}
    )
    
    mock_llm = MagicMock()
    plan_data = {
        "tables": [{"name": "users"}],
        "needed_columns": ["users.id", "users.name"]
    }
    mock_plan = PlanModel(**plan_data)
    mock_llm.return_value = mock_plan
    
    node = PlannerNode(llm=mock_llm)
    
    state = GraphState(
        user_query="get user names",
        schema_info=schema_info
    )
    
    new_state = node(state)
    
    assert new_state.plan is not None
    assert not new_state.errors
