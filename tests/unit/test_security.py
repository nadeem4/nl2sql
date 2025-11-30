import unittest
from unittest.mock import MagicMock, patch
from nl2sql.security import enforce_read_only
from nl2sql.nodes.executor_node import ExecutorNode
from nl2sql.schemas import GraphState, GeneratedSQL
from nl2sql.datasource_config import DatasourceProfile

class TestSecurity(unittest.TestCase):
    def test_enforce_read_only_allowed(self):
        allowed = [
            "SELECT * FROM users",
            "SELECT count(*) FROM orders WHERE status = 'active'",
            "WITH cte AS (SELECT 1) SELECT * FROM cte",
            "SELECT * FROM users WHERE name = 'DROP TABLE'",  # Should be allowed now
            "SELECT * FROM users WHERE comment LIKE '%INSERT%'", # Should be allowed
            "SELECT 1; SELECT 2" # Multiple selects allowed
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

    @patch("nl2sql.nodes.executor_node.make_engine")
    @patch("nl2sql.nodes.executor_node.run_read_query")
    def test_executor_node_blocks_forbidden_sql(self, mock_run, mock_engine):
        profile = DatasourceProfile(
            id="test", 
            engine="sqlite", 
            sqlalchemy_url="sqlite:///:memory:",
            auth=None,
            read_only_role=None
        )
        node = ExecutorNode(profile)
        
        state = GraphState(user_query="drop table")
        state.sql_draft = GeneratedSQL(sql="DROP TABLE users", rationale="bad", limit_enforced=True, draft_only=False)
        
        new_state = node(state)
        
        self.assertTrue(any("Security Violation" in e for e in new_state.errors))
        self.assertEqual(new_state.execution.get("error"), "Security Violation")
        mock_run.assert_not_called()

    @patch("nl2sql.nodes.executor_node.make_engine")
    @patch("nl2sql.nodes.executor_node.run_read_query")
    def test_executor_node_allows_safe_sql(self, mock_run, mock_engine):
        profile = DatasourceProfile(
            id="test", 
            engine="sqlite", 
            sqlalchemy_url="sqlite:///:memory:",
            auth=None,
            read_only_role=None
        )
        node = ExecutorNode(profile)
        
        state = GraphState(user_query="select *")
        state.sql_draft = GeneratedSQL(sql="SELECT * FROM users", rationale="good", limit_enforced=True, draft_only=False)
        
        mock_run.return_value = []
        new_state = node(state)
        
        self.assertFalse(new_state.errors)
        mock_run.assert_called_once()

if __name__ == "__main__":
    unittest.main()
