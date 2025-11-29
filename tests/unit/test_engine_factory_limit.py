import unittest
from unittest.mock import MagicMock
from nl2sql.engine_factory import run_read_query

class TestEngineFactoryLimit(unittest.TestCase):
    def test_run_read_query_appends_limit_redundantly(self):
        """
        Test that run_read_query appends LIMIT if it doesn't detect the existing one due to formatting.
        """
        # Mock engine and connection
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.fetchall.return_value = []
        
        # SQL with newline before LIMIT
        sql = "SELECT * FROM users\nLIMIT 100"
        
        run_read_query(mock_engine, sql, row_limit=1000)
        
        # Check what was executed
        args, _ = mock_conn.execute.call_args
        executed_sql = str(args[0])
        print(f"Executed SQL: {executed_sql}")
        
        # If the bug exists, it will have appended LIMIT 1000
        # Expected: SELECT * FROM users\nLIMIT 100
        # Actual (bug): SELECT * FROM users\nLIMIT 100\nLIMIT 1000
        
        self.assertEqual(executed_sql.upper().count("LIMIT"), 1, "Should detect existing LIMIT and not append another")

if __name__ == "__main__":
    unittest.main()
