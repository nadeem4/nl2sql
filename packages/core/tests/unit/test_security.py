import unittest
from unittest.mock import MagicMock
from nl2sql.common.security import enforce_read_only
from nl2sql.pipeline.nodes.executor.node import ExecutorNode
from nl2sql.pipeline.state import GraphState
from nl2sql.datasources import DatasourceRegistry
from nl2sql_adapter_sdk import DatasourceAdapter, QueryResult as ExecutionResult, CostEstimate as EstimationResult

class TestSecurity(unittest.TestCase):
    def test_enforce_read_only_allowed(self):
        allowed = [
            "SELECT * FROM users",
            "SELECT count(*) FROM orders WHERE status = 'active'",
            "WITH cte AS (SELECT 1) SELECT * FROM cte",
            "SELECT * FROM users WHERE name = 'DROP TABLE'",
            "SELECT * FROM users WHERE comment LIKE '%INSERT%'",
            "SELECT 1; SELECT 2"
        ]
        for sql in allowed:
            self.assertTrue(enforce_read_only(sql), f"Should allow: {sql}")

    def test_enforce_read_only_forbidden(self):
        forbidden = [
            "INSERT INTO users VALUES (1, 'test')",
            "UPDATE orders SET status = 'shipped'",
            "DELETE FROM users",
            "DROP TABLE users",
            "ALTER TABLE users ADD COLUMN x int",
            "TRUNCATE TABLE logs",
            "GRANT SELECT ON users TO public",
            "REVOKE ALL ON users FROM public",
            "CREATE TABLE x (id int)",
            "DROP DATABASE production"
        ]
        for sql in forbidden:
            self.assertFalse(enforce_read_only(sql), f"Should forbid: {sql}")


    def test_executor_node_allows_safe_sql(self):
        # Mock Registry and Adapter
        mock_registry = MagicMock(spec=DatasourceRegistry)
        mock_adapter = MagicMock(spec=DatasourceAdapter)
        mock_registry.get_adapter.return_value = mock_adapter

        mock_profile = MagicMock()
        mock_profile.engine = "sqlite"
        mock_registry.get_profile.return_value = mock_profile
        # Mock Estimate to be safe
        mock_adapter.cost_estimate.return_value = EstimationResult(estimated_rows=100, estimated_cost=10.0)
        # Mock Execute
        mock_adapter.execute.return_value = ExecutionResult(rows=[], columns=[], row_count=0)

        node = ExecutorNode(mock_registry)
        
        state = GraphState(user_query="select *", selected_datasource_id="test")
        state.sql_draft = "SELECT * FROM users"
        
        new_state = node(state)
        
        self.assertFalse(new_state["errors"])
        mock_adapter.execute.assert_called_once()

if __name__ == "__main__":
    unittest.main()
