import pytest
from unittest.mock import MagicMock
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.nodes.planner.schemas import PlanModel, TableRef, SelectItem, Expr, JoinSpec
from nl2sql_adapter_sdk.models import Table, Column, ColumnStatistics

def create_mock_state():
    # Mock Schema
    t1 = Table(name="users", columns=[
        Column(name="id", type="int"),
        Column(name="name", type="str")
    ])
    t2 = Table(name="orders", columns=[
        Column(name="id", type="int"),
        Column(name="user_id", type="int")
    ])
    
    return GraphState(
        user_query="q", 
        relevant_tables=[t1, t2],
        user_context={"allowed_tables": ["*"]}
    )

def test_validator_strict_aliasing_success():
    """Test that correct alias usage passes."""
    state = create_mock_state()
    registry = MagicMock()
    validator = LogicalValidatorNode(registry)
    
    # Plan: SELECT t1.name FROM users t1
    state.plan = PlanModel(
        tables=[TableRef(name="users", alias="t1", ordinal=0)],
        select_items=[
            SelectItem(
                ordinal=0,
                expr=Expr(kind="column", alias="t1", column_name="name")
            )
        ]
    )
    
    result = validator(state)
    assert not result.get("errors"), f"Should pass: {result.get('errors')}"

def test_validator_strict_aliasing_fail():
    """Test that accessing a column via wrong alias fails."""
    state = create_mock_state()
    registry = MagicMock()
    validator = LogicalValidatorNode(registry)
    
    # Plan: SELECT t1.user_id FROM users t1 (user_id is in orders, not users)
    state.plan = PlanModel(
        tables=[TableRef(name="users", alias="t1", ordinal=0)],
        select_items=[
            SelectItem(
                ordinal=0,
                expr=Expr(kind="column", alias="t1", column_name="user_id")
            )
        ]
    )
    
    result = validator(state)
    errors = result.get("errors", [])
    assert len(errors) > 0
    assert "does not exist in table alias 't1'" in errors[0].message

def test_validator_implicit_success():
    """Test matching column without alias."""
    state = create_mock_state()
    registry = MagicMock()
    validator = LogicalValidatorNode(registry)
    
    state.plan = PlanModel(
        tables=[TableRef(name="users", alias="t1", ordinal=0)],
        select_items=[
            SelectItem(
                ordinal=0,
                expr=Expr(kind="column", column_name="name") # No alias specified
            )
        ]
    )
    
    result = validator(state)
    assert not result.get("errors")

def test_validator_crash_fix():
    """Verify that using column_name does not crash due to AttributeError."""
    state = create_mock_state()
    registry = MagicMock()
    validator = LogicalValidatorNode(registry)
    
    state.plan = PlanModel(
        tables=[TableRef(name="users", alias="t1", ordinal=0)],
        select_items=[
            SelectItem(
                ordinal=0,
                expr=Expr(kind="column", alias="t1", column_name="id")
            )
        ]
    )
    
    # If the code uses col.name, this will crash (return VALIDATOR_CRASH error)
    result = validator(state)
    assert "VALIDATOR_CRASH" not in str(result)

def test_validator_join_alias_check():
    """Test that join aliases must exist in table list."""
    state = create_mock_state()
    registry = MagicMock()
    validator = LogicalValidatorNode(registry)
    
    state.plan = PlanModel(
        tables=[
            TableRef(name="users", alias="t1", ordinal=0),
            TableRef(name="orders", alias="t2", ordinal=1)
        ],
        joins=[
            JoinSpec(
                left_alias="t1",
                right_alias="UX_UNKNOWN", # Invalid alias
                join_type="inner",
                ordinal=0,
                condition=Expr(
                    kind="binary", op="=",
                    left=Expr(kind="column", alias="t1", column_name="id"),
                    right=Expr(kind="column", alias="t2", column_name="user_id")
                )
            )
        ],
        select_items=[]
    )
    
    result = validator(state)
    errors = result.get("errors", [])
    assert any("UX_UNKNOWN" in e.message for e in errors)

def test_validator_case_insensitivity():
    """Test that ID in schema matches id in plan."""
    state = create_mock_state()
    # Mock Schema with Uppercase ID
    t1 = Table(name="users", columns=[
        Column(name="ID", type="int"),  # Uppercase
        Column(name="NAME", type="str") # Uppercase
    ])
    state.relevant_tables = [t1]
    
    registry = MagicMock()
    validator = LogicalValidatorNode(registry)
    
    state.plan = PlanModel(
        tables=[TableRef(name="users", alias="t1", ordinal=0)],
        select_items=[
            SelectItem(
                ordinal=0,
                expr=Expr(kind="column", alias="t1", column_name="id") # Lowercase
            )
        ]
    )
    
    result = validator(state)
    assert not result.get("errors"), f"Should pass case-insensitive check. Errors: {result.get('errors')}"
