import unittest
from unittest.mock import MagicMock, patch
import json
from nl2sql.langgraph_pipeline import build_graph
from nl2sql.schemas import GraphState, IntentModel, PlanModel, SQLModel

class TestRefinements(unittest.TestCase):
    def test_error_recovery_loop(self):
        # Mock LLM that returns invalid Plan first, then valid Plan
        mock_llm = MagicMock()
        
        # 1. Intent: returns empty json
        intent_model = IntentModel()
        
        # 2. Planner (1st try): returns invalid plan (column not in schema)
        # Schema has t1(col1). We ask for col2.
        plan_invalid = PlanModel(
            tables=[{"name": "t1", "alias": "t1"}],
            needed_columns=["t1.col2"], # Invalid
            select_columns=["t1.col2"],
            limit=10
        )
        
        # 3. Planner (2nd try): returns valid plan
        plan_valid = PlanModel(
            tables=[{"name": "t1", "alias": "t1"}],
            needed_columns=["t1.col1"],
            select_columns=["t1.col1"],
            limit=10
        )
        
        mock_llm.side_effect = [
            intent_model,
            plan_invalid,
            plan_valid
        ]
        
        profile = MagicMock()
        profile.engine = "sqlite"
        profile.row_limit = 10
        
        # Mock capabilities
        with patch("nl2sql.langgraph_pipeline.get_capabilities") as mock_caps:
            mock_caps.return_value.dialect = "sqlite"
            mock_caps.return_value.limit_syntax = "limit"
            
            # Mock schema retriever to return tables
            with patch("nl2sql.nodes.schema_node.make_engine"), \
                 patch("nl2sql.nodes.schema_node.inspect") as mock_inspect:
                
                mock_inspect.return_value.get_table_names.return_value = ["t1"]
                mock_inspect.return_value.get_columns.return_value = [{"name": "col1"}]
                mock_inspect.return_value.get_foreign_keys.return_value = []
                
                graph = build_graph(profile, llm=mock_llm, execute=False)
                
                initial_state = {
                    "user_query": "test",
                    "validation": {"capabilities": "sqlite"}
                }
                
                # Run graph
                result = graph.invoke(initial_state)
                
                # Verify that retry_count increased
                self.assertGreater(result["retry_count"], 0)
                # Verify final SQL is valid
                self.assertIsNotNone(result["sql_draft"])
                self.assertIn("LIMIT", result["sql_draft"]["sql"])
                # Verify LLM calls: Intent + Planner(1) + Planner(2) = 3
                self.assertEqual(mock_llm.call_count, 3)

    def test_intent_utilization_in_planner(self):
        mock_llm = MagicMock()
    
        # Intent returns specific entities (as object now)
        intent_data = {"entities": ["User"], "filters": ["active=True"]}
        intent_model = IntentModel(**intent_data)
        
        # Planner returns valid plan
        plan_data = {
            "tables": [{"name": "t1", "alias": "t"}],
            "needed_columns": ["t.col1"],
            "select_columns": ["t.col1"],
            "limit": 10
        }
        plan_model = PlanModel(**plan_data)
        
        # Generator is not called via LLM anymore
    
        mock_llm.side_effect = [
            intent_model, # Intent
            plan_model,   # Planner
        ]
    
        profile = MagicMock()
        profile.engine = "sqlite"
        profile.row_limit = 10
    
        with patch("nl2sql.langgraph_pipeline.get_capabilities") as mock_caps, \
             patch("nl2sql.nodes.schema_node.make_engine"), \
             patch("nl2sql.nodes.schema_node.inspect") as mock_inspect:
    
            mock_caps.return_value.dialect = "sqlite"
            mock_inspect.return_value.get_table_names.return_value = ["t1"]
            mock_inspect.return_value.get_columns.return_value = [{"name": "col1"}]
            mock_inspect.return_value.get_foreign_keys.return_value = []
    
            graph = build_graph(profile, llm=mock_llm, execute=False)
            graph.invoke({"user_query": "test", "validation": {}})
    
            # Check Planner prompt (2nd call)
            planner_call_args = mock_llm.call_args_list[1]
            prompt = planner_call_args[0][0]
            
            # The prompt format changed, so we check for the intent context string
            self.assertIn("Extracted Intent", prompt)
            self.assertIn("User", prompt)
            self.assertIn("active=True", prompt)

if __name__ == "__main__":
    unittest.main()
