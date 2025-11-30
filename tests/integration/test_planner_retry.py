import unittest
from unittest.mock import MagicMock, patch
import json
from nl2sql.langgraph_pipeline import build_graph
from nl2sql.schemas import IntentModel, PlanModel, SQLModel

class TestPlannerRetry(unittest.TestCase):
    def test_planner_retry_logic(self):
        # Mock LLM to fail planner twice (empty/invalid), then succeed
        mock_llm = MagicMock()
        
        # Sequence of LLM calls:
        # 1. Intent (success)
        # 2. Planner (fail - empty)
        # 3. Planner (fail - invalid json)
        # 4. Planner (success)
        # 5. Generator (success)
        
        intent_resp = IntentModel()
        # Planner failures: The node catches exceptions. 
        # To trigger retry, we need the node to fail validation or parsing.
        # Since we use structured output, parsing shouldn't fail unless the LLM returns garbage that Pydantic rejects.
        # But we mock the LLM to return objects.
        # So we can simulate failure by having the LLM raise an exception, 
        # OR by returning an object that fails validation (e.g. invalid columns).
        
        # Let's simulate LLM raising exception for the first two calls
        # Actually, side_effect can be a list of return values OR exceptions.
        
        planner_fail_1 = Exception("LLM Error 1")
        planner_fail_2 = Exception("LLM Error 2")
        
        planner_success = PlanModel(
            tables=[{"name": "t1", "alias": "t1"}], 
            select_columns=[{"alias": "t1", "name": "col1"}],
            joins=[], filters=[], group_by=[], aggregates=[], having=[], order_by=[]
        )
        # Generator is non-AI
        
        mock_llm.side_effect = [
            intent_resp,
            planner_fail_1,
            planner_fail_2,
            planner_success
        ]
        
        profile = MagicMock()
        profile.engine = "sqlite"
        profile.row_limit = 10
        
        with patch("nl2sql.langgraph_pipeline.get_capabilities") as mock_caps, \
             patch("nl2sql.nodes.schema_node.make_engine"), \
             patch("nl2sql.nodes.schema_node.inspect") as mock_inspect:
            
            mock_caps.return_value.dialect = "sqlite"
            mock_caps.return_value.limit_syntax = "limit"
            
            # Schema setup
            mock_inspect.return_value.get_table_names.return_value = ["t1"]
            mock_inspect.return_value.get_columns.return_value = [{"name": "col1"}]
            mock_inspect.return_value.get_foreign_keys.return_value = []
            
            graph = build_graph(profile, llm=mock_llm, execute=False)
            
            initial_state = {
                "user_query": "test",
                "validation": {"capabilities": "sqlite"}
            }
            
            result = graph.invoke(initial_state)
            
            # Verify retry count increased (failed twice, so retry_count should be 2)
            self.assertEqual(result["retry_count"], 2)
            
            # Verify we got a plan eventually
            self.assertIsNotNone(result["plan"])
            self.assertEqual(result["plan"]["tables"][0]["name"], "t1")
            
            # Verify generator ran
            self.assertIsNotNone(result["sql_draft"])
            
            # Verify LLM call count: 1 intent + 3 planner = 4
            self.assertEqual(mock_llm.call_count, 4)

if __name__ == "__main__":
    unittest.main()
