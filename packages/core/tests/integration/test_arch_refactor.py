import unittest
from unittest.mock import MagicMock
from nl2sql.core.graph import build_graph
from nl2sql.core.datasource_config import DatasourceProfile
from nl2sql.core.schemas import IntentModel, PlanModel, TableModel as TableRef, ColumnModel as ColumnRef

class TestArchRefactor(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_arch.db"
        self.profile = DatasourceProfile(
            id="test", 
            engine="sqlite", 
            sqlalchemy_url=f"sqlite:///{self.db_path}",
            auth=None,
            read_only_role=None
        )
        # Create table
        from sqlalchemy import create_engine, text
        self.engine = create_engine(self.profile.sqlalchemy_url)
        with self.engine.connect() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS users (id INTEGER, name TEXT)"))
            conn.commit()

    def tearDown(self):
        self.engine.dispose()
        import os
        import time
        # Retry cleanup a few times for Windows file locking
        for _ in range(3):
            if os.path.exists(self.db_path):
                try:
                    os.remove(self.db_path)
                    break
                except Exception:
                    time.sleep(0.1)

    def test_write_query_blocked_by_validator(self):
        # Mock LLM to return WRITE intent
        mock_llm = MagicMock()
        
        # Intent Node Output
        intent_out = IntentModel(
            query_type="WRITE",
            keywords=["insert"],
            query_expansion=["add user"]
        )
        
        # Planner Node Output (should copy query_type)
        # SchemaNode generates 't1' for the first table 'users'
        plan_out = PlanModel(
            tables=[TableRef(name="users", alias="t1")],
            select_columns=[ColumnRef(expr="t1.id")],
            query_type="WRITE" # Planner copies this
        )
        
        # Setup mock side effects
        # 1. Intent Node call
        # 2. Planner Node call
        mock_llm.side_effect = [intent_out, plan_out]
        
        graph = build_graph(self.profile, llm=mock_llm, execute=False)
        
        # Run graph
        result = graph.invoke({"user_query": "Insert a user"})
        
        # Verify
        self.assertTrue(result["errors"])
        self.assertTrue(any("Security Violation" in e for e in result["errors"]))
        self.assertIsNone(result.get("sql_draft"))

    def test_read_query_allowed(self):
        # Mock LLM to return READ intent
        mock_llm = MagicMock()
        
        # Intent Node Output
        intent_out = IntentModel(
            query_type="READ",
            keywords=["select"],
            query_expansion=["get users"]
        )
        
        # Planner Node Output
        # SchemaNode generates 't1' for the first table 'users'
        plan_out = PlanModel(
            tables=[TableRef(name="users", alias="t1")],
            select_columns=[ColumnRef(expr="t1.id")],
            query_type="READ"
        )
        
        mock_llm.side_effect = [intent_out, plan_out]
        
        graph = build_graph(self.profile, llm=mock_llm, execute=False)
        
        # Run graph
        result = graph.invoke({"user_query": "Get users"})
        
        # Verify
        self.assertFalse(result["errors"])
        self.assertIsNotNone(result.get("sql_draft"))

if __name__ == "__main__":
    unittest.main()
