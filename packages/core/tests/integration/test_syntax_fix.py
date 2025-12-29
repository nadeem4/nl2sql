import unittest
from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.state import GraphState

class TestSyntaxFix(unittest.TestCase):
    def test_order_by_generated_correctly(self):
        """
        Test that GeneratorNode generates ORDER BY correctly from the plan.
        """
        # Plan requires ordering
        plan = {
            "tables": [{"name": "users", "alias": "u"}],
            "order_by": [{"column": {"expr": "u.name"}, "direction": "asc"}],
            "limit": 10,
            "select_columns": [{"expr": "u.name"}],
            "filters": [],
            "joins": [],
            "group_by": [],
            "having": []
        }
    
        state = GraphState(user_query="test", plan=plan)
        node = GeneratorNode(profile_engine="sqlite", row_limit=100)
        
        new_state = node(state)
        
        if new_state.errors:
            print(f"Errors: {new_state.errors}")
        
        sql = new_state.sql_draft["sql"].lower()
        
        assert "order by" in sql
        assert "limit" in sql
        # Check order: ORDER BY comes before LIMIT
        assert sql.index("order by") < sql.index("limit")

if __name__ == "__main__":
    unittest.main()
