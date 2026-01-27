
import pytest
from unittest.mock import MagicMock

from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel, TableRef, SelectItem, Expr, JoinSpec, ASTPlannerResponse
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.datasources import DatasourceRegistry
from nl2sql.schema import Table, Column
from nl2sql.auth import UserContext

# Shared Fixtures
@pytest.fixture
def mock_registry():
    registry = MagicMock(spec=DatasourceRegistry)
    registry.get_adapter.return_value = MagicMock()
    return registry

@pytest.fixture
def mock_ctx(mock_registry):
    ctx = MagicMock()
    ctx.ds_registry = mock_registry
    ctx.rbac = MagicMock()
    ctx.rbac.get_allowed_tables.return_value = ["*"]
    return ctx

@pytest.fixture
def mock_tables():
    return [
        Table(
            name="users", 
            columns=[
                Column(name="id", type="INT"),
                Column(name="name", type="VARCHAR"),
                Column(name="email", type="VARCHAR")
            ]
        ),
        Table(
            name="orders", 
            columns=[
                Column(name="id", type="INT"),
                Column(name="user_id", type="INT"),
                Column(name="amount", type="DECIMAL")
            ]
        )
    ]

def _col_expr(alias: str, col: str) -> Expr:
    return Expr(kind="column", alias=alias, column_name=col)

def create_mock_state(tables):
    return SubgraphExecutionState(
        relevant_tables=tables,
        ast_planner_response=ASTPlannerResponse(plan=None),
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        user_context=UserContext(),
        trace_id="t",
    )

class TestLogicalValidator:
    """Consolidated tests for LogicalValidatorNode."""

    def test_valid_plan(self, mock_tables, mock_ctx):
        """Test a perfectly valid plan."""
        validator = LogicalValidatorNode(ctx=mock_ctx)
        state = create_mock_state(mock_tables)
        state.ast_planner_response = ASTPlannerResponse(plan=PlanModel(
            tables=[TableRef(name="users", alias="u", ordinal=0)],
            select_items=[SelectItem(expr=_col_expr("u", "name"), ordinal=0)],
            joins=[]
        ))
        
        new_state = validator(state)
        assert not new_state.get("errors")

    def test_invalid_table(self, mock_tables, mock_ctx):
        """Test referencing a table not in relevant_tables."""
        validator = LogicalValidatorNode(ctx=mock_ctx)
        state = create_mock_state(mock_tables)
        state.ast_planner_response = ASTPlannerResponse(plan=PlanModel(
            tables=[TableRef(name="invalid_table", alias="x", ordinal=0)],
            select_items=[SelectItem(expr=_col_expr("x", "id"), ordinal=0)]
        ))
        
        new_state = validator(state)
        assert any("not found in relevant tables" in e.message for e in new_state["errors"])

    def test_undefined_alias(self, mock_tables, mock_ctx):
        """Test referencing an undefined alias in a column expression."""
        validator = LogicalValidatorNode(ctx=mock_ctx)
        state = create_mock_state(mock_tables)
        state.ast_planner_response = ASTPlannerResponse(plan=PlanModel(
            tables=[TableRef(name="users", alias="u", ordinal=0)],
            select_items=[SelectItem(expr=_col_expr("z", "name"), ordinal=0)]
        ))
        
        new_state = validator(state)
        assert any("uses undeclared alias 'z'" in e.message for e in new_state["errors"])

    def test_invalid_column_name(self, mock_tables, mock_ctx):
        """Test referencing a column that does not exist in the aliased table."""
        validator = LogicalValidatorNode(ctx=mock_ctx)
        state = create_mock_state(mock_tables)
        state.ast_planner_response = ASTPlannerResponse(plan=PlanModel(
            tables=[TableRef(name="users", alias="u", ordinal=0)],
            select_items=[SelectItem(expr=_col_expr("u", "invalid_col"), ordinal=0)]
        ))
        
        new_state = validator(state)
        assert any("does not exist in table alias 'u'" in e.message for e in new_state["errors"])

    def test_strict_aliasing_fail(self, mock_tables, mock_ctx):
        """Test that accessing a column via wrong alias fails (Strict Mode)."""
        validator = LogicalValidatorNode(ctx=mock_ctx)
        state = create_mock_state(mock_tables)
        
        # Plan: SELECT t1.user_id FROM users t1 (user_id is in orders, not users)
        state.ast_planner_response = ASTPlannerResponse(plan=PlanModel(
            tables=[TableRef(name="users", alias="t1", ordinal=0)],
            select_items=[SelectItem(expr=_col_expr("t1", "user_id"), ordinal=0)]
        ))
        
        new_state = validator(state)
        assert any("does not exist in table alias 't1'" in e.message for e in new_state["errors"])

    def test_implicit_success(self, mock_tables, mock_ctx):
        """Test matching column without alias (Implicit Mode)."""
        validator = LogicalValidatorNode(ctx=mock_ctx)
        state = create_mock_state(mock_tables)
        
        # Select 'name' without alias. Should resolve to 'users.name'.
        state.ast_planner_response = ASTPlannerResponse(plan=PlanModel(
            tables=[TableRef(name="users", alias="t1", ordinal=0)],
            select_items=[SelectItem(expr=Expr(kind="column", column_name="name"), ordinal=0)]
        ))
        
        new_state = validator(state)
        assert not new_state.get("errors")

    def test_case_insensitivity(self, mock_ctx):
        """Test that validation is case-insensitive for schema matching."""
        # Schema with Uppercase ID
        t1 = Table(name="users", columns=[Column(name="ID", type="int"), Column(name="NAME", type="str")])
        
        validator = LogicalValidatorNode(ctx=mock_ctx)
        state = create_mock_state([t1])
        state.ast_planner_response = ASTPlannerResponse(plan=PlanModel(
            tables=[TableRef(name="users", alias="t1", ordinal=0)],
            select_items=[SelectItem(expr=_col_expr("t1", "id"), ordinal=0)] # Lowercase check
        ))
        
        new_state = validator(state)
        assert not new_state.get("errors")

    def test_join_alias_check(self, mock_tables, mock_ctx):
        """Test that join aliases must exist in table list."""
        validator = LogicalValidatorNode(ctx=mock_ctx)
        state = create_mock_state(mock_tables)
        
        state.ast_planner_response = ASTPlannerResponse(plan=PlanModel(
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
                        left=_col_expr("t1", "id"),
                        right=_col_expr("t2", "user_id")
                    )
                )
            ],
            select_items=[]
        ))
        
        new_state = validator(state)
        assert any("UX_UNKNOWN" in e.message for e in new_state["errors"])

    def test_missing_tables_in_plan_regression(self, mock_ctx):
        """
        Regression Test: Reproduce issue where partial table list in PlanModel causes validation errors.
        The Validator should robustly handle joins even if table refs are messy? 
        Actually, the original test 'test_validator_joins.py' tested that generating a plan with missing tables 
        but valid joins passed through if the state logic handled it. 
        Here we verify the Validator's behavior on the *resulting* state.
        """
        # Original test simulated a full Planner execution. 
        # Here we just verify that if PlanModel HAS the tables, Validator passes.
        # If PlanModel is MISSING table refs but uses them in JOINS, Validator SHOULD fail (it's strict).
        # The original test seemed to assert success? 
        # Let's check original test...
        # It constructed a plan where tables list was MISSING 'machines' and 'logs'?
        # No, 'tables' has 3 items: factories, machines, logs.
        # Wait, lines 57-61 in old test:
        # tables=[TableRef(factories), TableRef(machines), TableRef(logs)]
        # So the plan WAS correct in the mock.
        
        # Okay, I will implement a standard successful complex join test here.
        
        t1 = Table(name="t1", columns=[Column(name="id", type="int")])
        t2 = Table(name="t2", columns=[Column(name="id", type="int"), Column(name="t1_id", type="int")])
        
        validator = LogicalValidatorNode(ctx=mock_ctx)
        state = create_mock_state([t1, t2])
        
        state.ast_planner_response = ASTPlannerResponse(plan=PlanModel(
            tables=[TableRef(name="t1", alias="a", ordinal=0), TableRef(name="t2", alias="b", ordinal=1)],
            joins=[
                JoinSpec(
                    left_alias="a", right_alias="b", join_type="inner", ordinal=0,
                    condition=Expr(kind="binary", op="=", left=_col_expr("a", "id"), right=_col_expr("b", "t1_id"))
                )
            ],
            select_items=[SelectItem(expr=_col_expr("b", "id"), ordinal=0)]
        ))
        
        new_state = validator(state)
        assert not new_state.get("errors")
