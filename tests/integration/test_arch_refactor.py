import unittest
from unittest.mock import MagicMock
from nl2sql.langgraph_pipeline import build_graph
from nl2sql.datasource_config import DatasourceProfile
from nl2sql.schemas import IntentModel, PlanModel, TableRef, ColumnRef

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
        plan_out = PlanModel(
            tables=[TableRef(name="users", alias="u")],
            select_columns=[ColumnRef(name="id", alias="u")],
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
        # Should not have proceeded to generator (sql_draft should be None or empty if validator failed)
        # Actually validator runs before generator. If validator fails, it goes to check_validation.
        # check_validation returns "end" for Security Violation.
        # So sql_generator should NOT be called.
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
        plan_out = PlanModel(
            tables=[TableRef(name="users", alias="u")],
            select_columns=[ColumnRef(name="id", alias="u")],
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
