import unittest
from nl2sql.nodes.generator_node import GeneratorNode
from nl2sql.schemas import GraphState, PlanModel, HavingSpec

class TestGeneratorHaving(unittest.TestCase):
    def test_having_generated_correctly(self):
        """
        Test that GeneratorNode generates HAVING correctly from the plan.
        """
        plan = {
            "tables": [{"name": "orders", "alias": "o"}],
            "select_columns": [
                {"expr": "o.user_id"},
                {"expr": "COUNT(*)", "alias": "cnt", "is_derived": True}
            ],
            "group_by": ["o.user_id"],
            "having": [{"expr": "COUNT(*)", "op": ">", "value": 5}],
            "filters": [],
            "joins": [],
            "order_by": []
        }
    
        state = GraphState(user_query="test", plan=plan)
        node = GeneratorNode(profile_engine="sqlite", row_limit=100)
        
        new_state = node(state)
        
        if new_state.errors:
            print(f"Errors: {new_state.errors}")
        
        sql = new_state.sql_draft["sql"].lower()
        
        assert "having" in sql
        assert "count(*) > 5" in sql or "count(*) > '5'" in sql or 'count(*) > 5' in sql

    def test_having_with_alias(self):
        """
        Test HAVING with alias if supported (or just expression).
        """
        plan = {
            "tables": [{"name": "orders", "alias": "o"}],
            "select_columns": [
                {"expr": "o.user_id"},
                {"expr": "COUNT(*)", "alias": "cnt", "is_derived": True}
            ],
            "group_by": ["o.user_id"],
            "having": [{"expr": "cnt", "op": ">", "value": 10}],
            "filters": [],
            "joins": [],
            "order_by": []
        }
    
        state = GraphState(user_query="test", plan=plan)
        node = GeneratorNode(profile_engine="sqlite", row_limit=100)
        
        new_state = node(state)
        
        if new_state.errors:
            print(f"Errors: {new_state.errors}")
        
        sql = new_state.sql_draft["sql"].lower()
        
        assert "having" in sql
        assert "cnt > 10" in sql or "cnt > '10'" in sql

if __name__ == "__main__":
    unittest.main()
