
import pytest
from unittest.mock import MagicMock
from pydantic import ValidationError
from nl2sql.pipeline.nodes.planner.schemas import Expr, CaseWhen, PlanModel
from nl2sql.pipeline.nodes.planner.node import PlannerNode
from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import ErrorCode
from nl2sql_adapter_sdk import Table, Column

# --- Schema Validation Tests (Preserved) ---

def test_expr_literal_validation():
    """Test strict validation for Literal kind."""
    Expr(kind="literal", value=1)
    with pytest.raises(ValidationError):
        Expr(kind="literal")

def test_expr_column_validation():
    """Test strict validation for Column kind."""
    Expr(kind="column", column_name="id", alias="users")
    with pytest.raises(ValidationError):
        Expr(kind="column", alias="users")

def test_expr_func_validation():
    """Test strict validation for Func kind."""
    Expr(kind="func", func_name="COUNT", args=[Expr(kind="column", column_name="id")])
    with pytest.raises(ValidationError):
        Expr(kind="func", args=[])

def test_expr_binary_validation():
    """Test strict validation for Binary kind."""
    Expr(kind="binary", op="=", left=Expr(kind="literal", value=1), right=Expr(kind="literal", value=1))
    with pytest.raises(ValidationError):
        Expr(kind="binary", op="=")

def test_case_when_validation():
    """Test strict validation for Case kind."""
    when = CaseWhen(condition=Expr(kind="literal", value=True), result=Expr(kind="literal", value="yes"), ordinal=0)
    Expr(kind="case", whens=[when])
    with pytest.raises(ValidationError):
        Expr(kind="case", whens=[])

# --- Node Logic Tests (New) ---

class TestPlannerNodeLogic:
    @pytest.fixture
    def mock_registry(self):
        return MagicMock()

    @pytest.fixture
    def mock_llm(self):
        return MagicMock()

    @pytest.fixture
    def basic_state(self):
        return GraphState(
            user_query="Show users",
            relevant_tables=[Table(name="users", columns=[Column(name="id", type="int")])]
        )

    def test_missing_llm_error(self, mock_registry, basic_state):
        """Test error when no LLM is provided."""
        node = PlannerNode(registry=mock_registry, llm=None)
        res = node(basic_state)
        
        assert len(res["errors"]) == 1
        assert res["errors"][0].error_code == ErrorCode.MISSING_LLM

    def test_successful_planning(self, mock_registry, mock_llm, basic_state):
        """Test successful plan generation."""
        node = PlannerNode(registry=mock_registry, llm=mock_llm)
        
        # Mock LLM Response
        mock_plan = PlanModel(
            query_type="READ",
            tables=[], select_items=[], joins=[], where=None,
            reasoning="Simple plan"
        )
        # Mock chain invoke because chain = prompt | llm
        node.chain = MagicMock()
        node.chain.invoke.return_value = mock_plan
        
        res = node(basic_state)
        
        assert res["plan"] == mock_plan
        assert not res["errors"]
        assert "Simple plan" in res["reasoning"][0]["content"][0]
        
        # Verify Context Passed to Chain
        args, _ = node.chain.invoke.call_args
        context = args[0]
        assert context["user_query"] == "Show users"
        assert "relevant_tables" in context
        assert "users" in context["relevant_tables"]

    def test_planner_exception_handling(self, mock_registry, mock_llm, basic_state):
        """Test handling of unexpected exceptions."""
        node = PlannerNode(registry=mock_registry, llm=mock_llm)
        node.chain = MagicMock()
        node.chain.invoke.side_effect = RuntimeError("LLM Crash")
        
        res = node(basic_state)
        
        assert res["plan"] is None
        assert len(res["errors"]) == 1
        assert res["errors"][0].error_code == ErrorCode.PLANNING_FAILURE
        assert "LLM Crash" in res["errors"][0].stack_trace
