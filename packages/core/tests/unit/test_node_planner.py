
import pytest
from unittest.mock import MagicMock
from pydantic import ValidationError

from nl2sql.pipeline.nodes.ast_planner.schemas import Expr, CaseWhen, PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.common.errors import ErrorCode
from nl2sql.schema import Table, Column

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
    def mock_llm(self):
        return MagicMock()

    @pytest.fixture
    def mock_ctx(self, mock_llm):
        ctx = MagicMock()
        ctx.llm_registry.get_llm.return_value = mock_llm
        return ctx

    @pytest.fixture
    def basic_state(self):
        return SubgraphExecutionState(
            sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="Show users"),
            relevant_tables=[Table(name="users", columns=[Column(name="id", type="int")])],
            trace_id="t",
        )

    def test_planner_failure(self, mock_ctx, basic_state):
        """Test error when planning fails."""
        node = ASTPlannerNode(ctx=mock_ctx)
        node.chain = MagicMock()
        node.chain.invoke.side_effect = RuntimeError("Missing LLM")
        res = node(basic_state)

        assert len(res["errors"]) == 1
        assert res["errors"][0].error_code == ErrorCode.PLANNING_FAILURE

    def test_successful_planning(self, mock_ctx, mock_llm, basic_state):
        """Test successful plan generation."""
        node = ASTPlannerNode(ctx=mock_ctx)
        
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
        
        assert res["ast_planner_response"].plan == mock_plan
        assert not res["errors"]
        assert "Simple plan" in res["reasoning"][0]["content"][0]
        
        # Verify Context Passed to Chain
        args, _ = node.chain.invoke.call_args
        context = args[0]
        assert context["user_query"] == "Show users"
        assert "relevant_tables" in context
        assert "users" in context["relevant_tables"]

    def test_planner_exception_handling(self, mock_ctx, mock_llm, basic_state):
        """Test handling of unexpected exceptions."""
        node = ASTPlannerNode(ctx=mock_ctx)
        node.chain = MagicMock()
        node.chain.invoke.side_effect = RuntimeError("LLM Crash")
        
        res = node(basic_state)
        
        assert res["ast_planner_response"].plan is None
        assert len(res["errors"]) == 1
        assert res["errors"][0].error_code == ErrorCode.PLANNING_FAILURE
        assert "LLM Crash" in res["errors"][0].stack_trace
