
import unittest
from unittest.mock import MagicMock, patch
from concurrent.futures import Future

from nl2sql.pipeline.nodes.validator.physical_node import PhysicalValidatorNode, _dry_run_in_process, _cost_estimate_in_process
from nl2sql.pipeline.state import GraphState
from nl2sql.datasources import DatasourceRegistry
from nl2sql.common.errors import ErrorCode
from nl2sql_adapter_sdk import DryRunResult, CostEstimate

class TestPhysicalValidatorNode(unittest.TestCase):
    def setUp(self):
        self.registry = MagicMock(spec=DatasourceRegistry)
        self.adapter = MagicMock()
        self.adapter.datasource_engine_type = "mock_engine"
        self.adapter.datasource_id = "ds1"
        self.adapter.connection_args = {}
        
        self.registry.get_adapter.return_value = self.adapter
        self.node = PhysicalValidatorNode(self.registry)

    @patch("nl2sql.pipeline.nodes.validator.physical_node.get_execution_pool")
    def test_physical_validator_dry_run_failure(self, mock_get_pool):
        """Test handling of Dry Run Failure (Semantic Check)."""
        mock_pool = MagicMock()
        mock_get_pool.return_value = mock_pool

        # Mock DryRunResult
        mock_res = MagicMock(spec=DryRunResult)
        mock_res.is_valid = False
        mock_res.error_message = "Syntax Error near..."
        
        future = Future()
        future.set_result(mock_res)
        mock_pool.submit.return_value = future

        state = GraphState(
            user_query="q",
            sql_draft="SELECT * FROM users",
            selected_datasource_id="ds1",
        )
        result = self.node(state)
        
        # Verify
        self.assertEqual(result["errors"][0].error_code, ErrorCode.EXECUTION_ERROR)
        self.assertIn("Dry Run Failed", result["errors"][0].message)
        
        # Verify Correct Function Submission
        mock_pool.submit.assert_any_call(
            _dry_run_in_process,
            engine_type="mock_engine",
            ds_id="ds1",
            connection_args={},
            sql="SELECT * FROM users"
        )

    @patch("nl2sql.pipeline.nodes.validator.physical_node.get_execution_pool")
    def test_physical_validator_perf_check(self, mock_get_pool):
        """Test performance check failure (row limit)."""
        mock_pool = MagicMock()
        mock_get_pool.return_value = mock_pool
        
        # Mock DryRun Success
        mock_dry_res = MagicMock(spec=DryRunResult)
        mock_dry_res.is_valid = True
        
        # Mock CostEstimate
        mock_cost = MagicMock(spec=CostEstimate)
        mock_cost.estimated_rows = 5000
        
        # The node calls submit twice: once for dry run, once for cost estimate
        f1 = Future()
        f1.set_result(mock_dry_res)
        f2 = Future()
        f2.set_result(mock_cost)
        
        mock_pool.submit.side_effect = [f1, f2]
        
        node_limited = PhysicalValidatorNode(self.registry, row_limit=1000)
        state = GraphState(
            user_query="q",
            sql_draft="SELECT * FROM users",
            selected_datasource_id="ds1",
        )
        result = node_limited(state)
        
        self.assertEqual(result["errors"][0].error_code, ErrorCode.PERFORMANCE_WARNING)
        self.assertIn("exceeds limit 1000", result["errors"][0].message)

