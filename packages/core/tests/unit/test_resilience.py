import pytest
import time
from unittest.mock import MagicMock, patch
from nl2sql.common.contracts import ExecutionResult, ExecutionRequest
from nl2sql.pipeline.nodes.executor.schemas import ExecutionModel
from nl2sql.common.errors import ErrorCode, ErrorSeverity
from nl2sql.common.resilience import DB_BREAKER, LLM_BREAKER
import pybreaker
from nl2sql.pipeline.nodes.executor.node import ExecutorNode
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.datasources import DatasourceRegistry

class TestExecutorNodeResilience:
    
    def setup_method(self):
        DB_BREAKER.close()
        
    @patch("nl2sql.common.sandbox.execute_in_sandbox")
    @patch("nl2sql.common.sandbox.get_execution_pool")
    def test_executor_fail_fast(self, mock_pool, mock_sandbox):        
        """Verify ExecutorNode returns SERVICE_UNAVAILABLE when breaker is open."""
        
        # Configure Mock Registry and Adapter
        registry = MagicMock(spec=DatasourceRegistry)
        mock_adapter = MagicMock()
        mock_adapter.datasource_engine_type = "postgres" 
        mock_adapter.connection_args = {"host": "localhost"}
        mock_adapter.row_limit = 100
        mock_adapter.max_bytes = 1024
        # Important: statement_timeout_ms needed for limits
        mock_adapter.statement_timeout_ms = 1000 
        
        registry.get_adapter.return_value = mock_adapter
        
        ctx = MagicMock()
        ctx.ds_registry = registry
        node = ExecutorNode(ctx)
        
        # Mock request
        state = MagicMock()
        state.sub_query = SubQuery(id="sq1", datasource_id="test_ds", intent="q")
        state.generator_response = GeneratorResponse(sql_draft="SELECT * FROM users")
        
        # Simulate Crash Logic -> Trips Breaker
        mock_sandbox.return_value = ExecutionResult(
            success=False, 
            error="Segfault", 
            metrics={"is_crash": 1.0}
        )
        
        # 1. Trip the breaker (Attempt up to fail_max + buffer)
        # We need to ensure we are starting fresh
        DB_BREAKER.close()
        
        breaker_tripped = False
        for i in range(10):
            result = node(state)
            error_code = result["errors"][0].error_code
            
            if error_code == ErrorCode.SERVICE_UNAVAILABLE:
                breaker_tripped = True
                assert "Circuit Breaker Open" in result["errors"][0].message
                break
            
            # If not open yet, it should be the crash error
            assert error_code == ErrorCode.EXECUTOR_CRASH
            
        assert breaker_tripped, "DB_BREAKER did not trip after multiple failures"
        
        # 2. Verify Breaker is Open
        assert DB_BREAKER.current_state == "open"
        
        # 3. Next call should definitely be Service Unavailable
        mock_sandbox.reset_mock()
        result_fast = node(state)
        mock_sandbox.assert_not_called()
        assert result_fast["errors"][0].error_code == ErrorCode.SERVICE_UNAVAILABLE
