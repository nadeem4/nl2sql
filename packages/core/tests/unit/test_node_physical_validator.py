
import unittest
from unittest.mock import MagicMock, patch
from concurrent.futures import Future

from nl2sql.pipeline.nodes.validator.physical_node import PhysicalValidatorNode, _dry_run_in_process, _cost_estimate_in_process
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.errors import ErrorCode
from nl2sql.common.contracts import ExecutionResult

class TestPhysicalValidatorNode(unittest.TestCase):
    def setUp(self):
        self.registry = MagicMock(spec=DatasourceRegistry)
        self.adapter = MagicMock()
        self.adapter.datasource_engine_type = "mock_engine"
        self.adapter.datasource_id = "ds1"
        self.adapter.connection_args = {}
        
        self.registry.get_adapter.return_value = self.adapter
        self.ctx = MagicMock()
        self.ctx.ds_registry = self.registry
        self.node = PhysicalValidatorNode(self.ctx)


    @patch("nl2sql.pipeline.nodes.validator.physical_node.get_execution_pool")
    def test_physical_validator_dry_run_failure(self, mock_get_pool):
        """Test handling of Dry Run Failure (Semantic Check)."""
        mock_pool = MagicMock()
        mock_get_pool.return_value = mock_pool

        # Mock ExecutionResult (Success=True means worker didn't crash, Data=Invalid)
        mock_res = ExecutionResult(
            success=True,
            data={"is_valid": False, "error_message": "Syntax Error near..."}
        )
        
        future = Future()
        future.set_result(mock_res)
        mock_pool.submit.return_value = future

        state = SubgraphExecutionState(
            trace_id="t",
            generator_response=GeneratorResponse(sql_draft="SELECT * FROM users"),
            sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        )
        result = self.node(state)
        
        # Verify
        self.assertEqual(result["errors"][0].error_code, ErrorCode.EXECUTION_ERROR)
        self.assertIn("Dry Run Failed", result["errors"][0].message)
        
        # Verify Correct Function Submission
        match_call = None
        for call in mock_pool.submit.call_args_list:
            if call.args[0] == _dry_run_in_process:
                match_call = call
                break
        
        self.assertIsNotNone(match_call, "_dry_run_in_process was never called")
        args, _ = match_call
        req = args[1]
        self.assertEqual(req.mode, "dry_run")
        self.assertEqual(req.sql, "SELECT * FROM users")

    @patch("nl2sql.pipeline.nodes.validator.physical_node.get_execution_pool")
    def test_physical_validator_perf_check(self, mock_get_pool):
        """Test performance check failure (row limit)."""
        mock_pool = MagicMock()
        mock_get_pool.return_value = mock_pool
        
        # Mock DryRun Success
        res_dry = ExecutionResult(
            success=True,
            data={"is_valid": True, "error_message": None}
        )
        
        # Mock CostEstimate
        res_cost = ExecutionResult(
            success=True,
            data={"estimated_rows": 5000, "estimated_bytes": 0}
        )
        
        # The node calls submit twice: once for dry run, once for cost estimate
        f1 = Future()
        f1.set_result(res_dry)
        f2 = Future()
        f2.set_result(res_cost)
        
        mock_pool.submit.side_effect = [f1, f2]
        
        node_limited = PhysicalValidatorNode(self.ctx, row_limit=1000)
        state = SubgraphExecutionState(
            trace_id="t",
            generator_response=GeneratorResponse(sql_draft="SELECT * FROM users"),
            sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        )
        result = node_limited(state)
        
        self.assertEqual(result["errors"][0].error_code, ErrorCode.PERFORMANCE_WARNING)
        self.assertIn("exceeds limit 1000", result["errors"][0].message)

