from unittest.mock import MagicMock, ANY
import pytest
from nl2sql.pipeline.nodes.aggregator.node import AggregatorNode
from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode

class TestAggregatorNode:
    """Unit tests for the AggregatorNode."""

    @pytest.fixture
    def mock_llm(self):
        """Creates a mock LLM runnable."""
        mock = MagicMock()
        mock.invoke.return_value = MagicMock(summary="Summary", content="Content", format_type="text")
        return mock

    def test_sanitization_of_sensitive_errors(self, mock_llm):
        """Verifies that sensitive database errors are sanitized before reaching the LLM."""
        # Setup
        node = AggregatorNode(llm=mock_llm)
        
        # Create a state with a sensitive DB error
        secret_message = "Syntax error in table 'confidential_users', column 'ssn'"
        error = PipelineError(
            node="executor",
            message=secret_message,
            severity=ErrorSeverity.ERROR,
            error_code=ErrorCode.DB_EXECUTION_ERROR,
            stack_trace="Traceback: ..."
        )
        
        state = GraphState(
            user_query="SELECT * FROM users",
            intermediate_results=[],
            errors=[error]
        )

        # Mock the chain directly to avoid LangChain internals complexity
        node.chain = MagicMock()
        node.chain.invoke.return_value = MagicMock(summary="Safe", content="Safe", format_type="text")

        # Execute internal method that prepares prompt
        node._display_result_with_llm(state)

        # Verify CHAIN invoke arguments (input dict)
        call_args = node.chain.invoke.call_args[0][0]
        intermediate_res_str = call_args["intermediate_results"]
        
        # Assertion: Secrets should NOT be present
        assert "confidential_users" not in intermediate_res_str
        assert "ssn" not in intermediate_res_str
        
        # Assertion: Safe message SHOULD be present
        assert "An internal database error occurred" in intermediate_res_str

    def test_pass_through_of_safe_errors(self, mock_llm):
        """Verifies that non-sensitive errors are passed through safely."""
        node = AggregatorNode(llm=mock_llm)
        node.chain = MagicMock()
        node.chain.invoke.return_value = MagicMock(summary="Safe", content="Safe", format_type="text")

        safe_message = "I could not find a plan for this query."
        error = PipelineError(
            node="planner",
            message=safe_message,
            severity=ErrorSeverity.WARNING,
            error_code=ErrorCode.PLANNING_FAILURE
        )
        
        state = GraphState(
            user_query="Help",
            intermediate_results=[],
            errors=[error]
        )
        
        node._display_result_with_llm(state)
        
        call_args = node.chain.invoke.call_args[0][0]
        intermediate_res_str = call_args["intermediate_results"]
        
        assert safe_message in intermediate_res_str
