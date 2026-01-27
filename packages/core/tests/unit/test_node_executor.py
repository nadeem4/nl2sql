
import unittest
from unittest.mock import MagicMock, patch
from concurrent.futures import Future

from nl2sql.pipeline.nodes.executor.node import ExecutorNode, _execute_in_process
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.common.errors import ErrorCode, ErrorSeverity
from nl2sql.common.contracts import ExecutionResult

class TestExecutorNode(unittest.TestCase):
    def setUp(self):
        self.mock_registry = MagicMock()
        self.mock_adapter = MagicMock()
        self.mock_registry.get_adapter.return_value = self.mock_adapter
        
        # Default Adapter Settings
        self.mock_adapter.row_limit = 100
        self.mock_adapter.max_bytes = 1000
        self.mock_adapter.connection_type = "mock_db"
        self.mock_adapter.datasource_engine_type = "mock_engine"
        self.mock_adapter.connection_args = {"host": "localhost"}
        self.mock_adapter.statement_timeout_ms = 5000
        
        self.ctx = MagicMock()
        self.ctx.ds_registry = self.mock_registry
        self.node = ExecutorNode(self.ctx)


    def test_missing_inputs(self):
        """Test error when SQL or Datasource ID is missing."""
        state = SubgraphExecutionState(
            trace_id="t",
            generator_response=GeneratorResponse(sql_draft=None),
            sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        )
        res = self.node(state)
        self.assertEqual(res["errors"][0].error_code, ErrorCode.MISSING_SQL)
        
        state = SubgraphExecutionState(
            trace_id="t",
            generator_response=GeneratorResponse(sql_draft="SELECT 1"),
            sub_query=None,
        )
        res = self.node(state)
        self.assertEqual(res["errors"][0].error_code, ErrorCode.MISSING_DATASOURCE_ID)


    @patch("nl2sql.pipeline.nodes.executor.node.get_execution_pool")
    def test_execution_success(self, mock_get_pool):
        """Test successful query execution via sandbox."""
        # 1. Setup Mock Pool and Future
        mock_pool = MagicMock()
        mock_get_pool.return_value = mock_pool
        
        expected_result = ExecutionResult(
            success=True,
            data={
                "row_count": 2,
                "rows": [{"id": 1, "val": "A"}, {"id": 2, "val": "B"}],
                "columns": ["id", "val"],
                "bytes_returned": 100
            },
            metrics={"execution_time_ms": 10}
        )
        
        future = Future()
        future.set_result(expected_result)
        mock_pool.submit.return_value = future
        
        # 2. Run
        state = SubgraphExecutionState(
            trace_id="t",
            generator_response=GeneratorResponse(sql_draft="SELECT * FROM table"),
            sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        )
        res = self.node(state)
        
        # 3. Verify
        self.assertFalse(res["errors"])
        self.assertEqual(res["executor_response"].execution.row_count, 2)
        self.assertEqual(res["executor_response"].execution.rows[0]["val"], "A")
        
        # Verify submit called with ExecutionRequest
        mock_pool.submit.assert_called_once()
        args, _ = mock_pool.submit.call_args
        self.assertEqual(args[0], _execute_in_process)
        request = args[1]
        self.assertEqual(request.engine_type, "mock_engine")
        self.assertEqual(request.sql, "SELECT * FROM table")

    @patch("nl2sql.pipeline.nodes.executor.node.get_execution_pool")
    def test_safeguard_max_bytes(self, mock_get_pool):
        """Test that execution halts if result size exceeds limit (Post-Execution Check)."""
        mock_pool = MagicMock()
        mock_get_pool.return_value = mock_pool
        
        # Result > 1000 bytes
        huge_result = ExecutionResult(
            success=True,
            data={
                "row_count": 1, 
                "rows": [{"d": "x"}], 
                "columns": ["d"],
                "bytes_returned": 2000
            }
        )
        
        future = Future()
        future.set_result(huge_result)
        mock_pool.submit.return_value = future
        
        state = SubgraphExecutionState(
            trace_id="t",
            generator_response=GeneratorResponse(sql_draft="SELECT * FROM blob"),
            sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        )
        res = self.node(state)
        
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(res["errors"][0].error_code, ErrorCode.SAFEGUARD_VIOLATION)

    @patch("nl2sql.pipeline.nodes.executor.node.get_execution_pool")
    def test_sandbox_crash(self, mock_get_pool):
        """Test handling of Sandbox Process Crash (Segfault)."""
        mock_pool = MagicMock()
        mock_get_pool.return_value = mock_pool
        
        future = Future()
        from concurrent.futures.process import BrokenProcessPool
        future.set_exception(BrokenProcessPool("A process in the process pool was terminated abruptly."))
        mock_pool.submit.return_value = future
        
        state = SubgraphExecutionState(
            trace_id="t",
            generator_response=GeneratorResponse(sql_draft="SELECT KILL"),
            sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        )
        res = self.node(state)
        
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(res["errors"][0].error_code, ErrorCode.EXECUTOR_CRASH)
        self.assertEqual(res["errors"][0].severity, ErrorSeverity.CRITICAL)
        self.assertIn("SANDBOX CRASH", res["errors"][0].message)

if __name__ == "__main__":
    unittest.main()
