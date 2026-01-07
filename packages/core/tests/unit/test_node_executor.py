
import unittest
from unittest.mock import MagicMock
from nl2sql.pipeline.nodes.executor.node import ExecutorNode
from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import ErrorCode
from nl2sql_adapter_sdk import QueryResult, Column, CostEstimate

class TestExecutorNode(unittest.TestCase):
    def setUp(self):
        self.mock_registry = MagicMock()
        self.mock_adapter = MagicMock()
        self.mock_registry.get_adapter.return_value = self.mock_adapter
        
        # Default Adapter Settings
        self.mock_adapter.row_limit = 100
        self.mock_adapter.max_bytes = 1000
        self.mock_adapter.connection_type = "mock_db"
        
        self.node = ExecutorNode(self.mock_registry)

    def test_missing_inputs(self):
        """Test error when SQL or Datasource ID is missing."""
        state = GraphState(user_query="q", sql_draft=None, selected_datasource_id="ds1")
        res = self.node(state)
        self.assertEqual(res["errors"][0].error_code, ErrorCode.MISSING_SQL)
        
        state = GraphState(user_query="q", sql_draft="SELECT 1", selected_datasource_id=None)
        res = self.node(state)
        self.assertEqual(res["errors"][0].error_code, ErrorCode.MISSING_DATASOURCE_ID)

    def test_safeguard_row_limit(self):
        """Test that execution halts if estimated rows exceed limit."""
        self.mock_adapter.cost_estimate.return_value = CostEstimate(estimated_rows=200, estimated_cost=10.0)
        
        state = GraphState(user_query="q", sql_draft="SELECT * FROM huge_table", selected_datasource_id="ds1")
        res = self.node(state)
        
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(res["errors"][0].error_code, ErrorCode.SAFEGUARD_VIOLATION)
        self.assertIn("exceeding limit of 100", res["errors"][0].message)
        self.mock_adapter.execute.assert_not_called()

    def test_safeguard_max_bytes(self):
        """Test that execution halts if result size exceeds limit."""
        # Cost estimate passes
        self.mock_adapter.cost_estimate.return_value = CostEstimate(estimated_rows=10, estimated_cost=1.0)
        
        # Execution returns huge result
        self.mock_adapter.execute.return_value = QueryResult(
            columns=["d"], 
            rows=[["x"]], 
            row_count=1, 
            bytes_returned=2000 # > 1000
        )
        
        state = GraphState(user_query="q", sql_draft="SELECT * FROM blob_table", selected_datasource_id="ds1")
        res = self.node(state)
        
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(res["errors"][0].error_code, ErrorCode.SAFEGUARD_VIOLATION)
        self.assertIn("exceeds configured limit", res["errors"][0].message)

    def test_execution_success(self):
        """Test successful query execution."""
        self.mock_adapter.cost_estimate.return_value = CostEstimate(estimated_rows=10, estimated_cost=1.0)
        self.mock_adapter.execute.return_value = QueryResult(
            columns=["id", "val"], 
            rows=[[1, "A"], [2, "B"]], 
            row_count=2, 
            bytes_returned=100
        )
        
        state = GraphState(user_query="q", sql_draft="SELECT * FROM table", selected_datasource_id="ds1")
        res = self.node(state)
        
        self.assertFalse(res["errors"])
        self.assertEqual(res["execution"].row_count, 2)
        self.assertEqual(res["execution"].rows[0]["val"], "A")

    def test_adapter_failure(self):
        """Test handling of DB execution errors."""
        self.mock_adapter.cost_estimate.return_value = CostEstimate(estimated_rows=10, estimated_cost=1.0)
        self.mock_adapter.execute.side_effect = Exception("DB Connection Lost")
        
        state = GraphState(user_query="q", sql_draft="SELECT 1", selected_datasource_id="ds1")
        res = self.node(state)
        
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(res["errors"][0].error_code, ErrorCode.DB_EXECUTION_ERROR)
        self.assertIn("DB Connection Lost", res["errors"][0].message)

if __name__ == "__main__":
    unittest.main()
