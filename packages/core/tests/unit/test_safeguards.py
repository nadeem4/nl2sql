import pytest
from unittest.mock import MagicMock
from nl2sql.pipeline.nodes.executor.node import ExecutorNode
from nl2sql.pipeline.state import GraphState
from nl2sql_adapter_sdk import CostEstimate as EstimationResult, QueryResult as ExecutionResult
from nl2sql.common.errors import ErrorCode

class TestSafeguards:
    
    @pytest.fixture
    def mock_registry(self):
        registry = MagicMock()
        profile = MagicMock()
        profile.engine = "sqlite"
        registry.get_profile.return_value = profile
        return registry
        
    @pytest.fixture
    def mock_adapter(self):
        adapter = MagicMock()
        return adapter

    def test_executor_rejects_expensive_query(self, mock_registry, mock_adapter):
        """Verify that Executor returns SAFEGUARD_VIOLATION if rows > limit."""
        
        # Setup Registry to return our mock adapter
        mock_registry.get_adapter.return_value = mock_adapter
        
        # Setup Adapter Estimation to return HIGH row count
        mock_adapter.cost_estimate.return_value = EstimationResult(
            estimated_rows=15000,
            estimated_cost=100.0
        )
        
        executor = ExecutorNode(mock_registry)
        
        # Setup State
        state = GraphState(
            user_query="big query",
            selected_datasource_id="ds_1",
            sql_draft="SELECT * FROM big_table"
        )
        
        # Run
        result = executor(state)
        
        # Assertions
        assert "errors" in result
        assert len(result["errors"]) >= 1
        error = result["errors"][0]
        assert error.error_code == ErrorCode.SAFEGUARD_VIOLATION
        assert "SAFEGUARD" in result["execution"].error
        
        # Verify execute() was NOT called
        mock_adapter.execute.assert_not_called()

    def test_executor_allows_cheap_query(self, mock_registry, mock_adapter):
        """Verify that Executor proceeds if rows < limit."""
        
        mock_registry.get_adapter.return_value = mock_adapter
        
        # Low row count
        mock_adapter.cost_estimate.return_value = EstimationResult(
            estimated_rows=500,
            estimated_cost=10.0
        )
        
        # Execution result
        mock_adapter.execute.return_value = ExecutionResult(
            rows=[[1]], columns=["id"], row_count=1
        )
        
        executor = ExecutorNode(mock_registry)
        state = GraphState(user_query="ok", selected_datasource_id="ds_1", sql_draft="SELECT * FROM small")
        
        result = executor(state)
        
        assert "execution" in result
        assert result["execution"].row_count == 1
        assert not result.get("errors")
        
        # Verify execute WAS called
        mock_adapter.execute.assert_called_once()
