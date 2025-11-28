
import unittest
from unittest.mock import MagicMock, patch
import json
from nl2sql.langgraph_pipeline import build_graph
from nl2sql.schemas import GraphState

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
        
        intent_resp = json.dumps({"entities": [], "filters": [], "keywords": [], "clarifications": []})
        planner_fail_1 = "" # Empty string
        planner_fail_2 = "NOT JSON" # Invalid
        planner_success = json.dumps({
            "tables": [{"name": "t1"}], 
            "needed_columns": ["t1.col1"],
            "joins": [], "filters": [], "group_by": [], "aggregates": [], "having": [], "order_by": []
        })
        generator_success = json.dumps({"sql": "SELECT col1 FROM t1 LIMIT 10"})
        
        mock_llm.side_effect = [
            intent_resp,
            planner_fail_1,
            planner_fail_2,
            planner_success,
            generator_success
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
            
            # Verify LLM call count: 1 intent + 3 planner + 1 generator = 5
            self.assertEqual(mock_llm.call_count, 5)

if __name__ == "__main__":
    unittest.main()
