import unittest
from unittest.mock import MagicMock, patch
import json
from nl2sql.langgraph_pipeline import build_graph
from nl2sql.schemas import GraphState, IntentModel, PlanModel, SQLModel

class TestRefinements(unittest.TestCase):
    def test_error_recovery_loop(self):
        # Mock LLM that returns invalid SQL first, then valid SQL
        mock_llm = MagicMock()
        
        # 1. Intent: returns empty json
        intent_model = IntentModel()
        
        # 2. Planner: returns valid plan
        plan_model = PlanModel(tables=[{"name": "t1"}])
        
        # 3. Generator (1st try): returns SQL without LIMIT (invalid)
        sql_model_invalid = SQLModel(sql="SELECT col1 FROM t1")
        
        # 4. Generator (2nd try): returns SQL with LIMIT (valid)
        sql_model_valid = SQLModel(sql="SELECT col1 FROM t1 LIMIT 10")
        
        mock_llm.side_effect = [
            intent_model,
            plan_model,
            sql_model_invalid,
            sql_model_valid
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
                self.assertIn("LIMIT", result["sql_draft"]["sql"])
                # Verify generator was called twice
                # (Intent + Planner + Gen1 + Gen2 = 4 calls)
                self.assertEqual(mock_llm.call_count, 4)

    def test_intent_utilization_in_planner(self):
        mock_llm = MagicMock()
    
        # Intent returns specific entities (as object now)
        intent_data = {"entities": ["User"], "filters": ["active=True"]}
        intent_model = IntentModel(**intent_data)
        
        # Planner returns empty plan
        plan_data = {"tables": []}
        plan_model = PlanModel(**plan_data)
        
        # Generator returns valid SQL
        sql_data = {"sql": "SELECT 1 LIMIT 1"}
        sql_model = SQLModel(**sql_data)
    
        mock_llm.side_effect = [
            intent_model, # Intent
            plan_model,   # Planner
            sql_model     # Generator
        ]
    
        profile = MagicMock()
        profile.engine = "sqlite"
        profile.row_limit = 10
    
        with patch("nl2sql.langgraph_pipeline.get_capabilities") as mock_caps, \
             patch("nl2sql.nodes.schema_node.make_engine"), \
             patch("nl2sql.nodes.schema_node.inspect"):
    
            mock_caps.return_value.dialect = "sqlite"
    
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
