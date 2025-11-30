import unittest
from nl2sql.nodes.generator_node import GeneratorNode
from nl2sql.schemas import GraphState

class TestSyntaxFix(unittest.TestCase):
    def test_order_by_generated_correctly(self):
        """
        Test that GeneratorNode generates ORDER BY correctly from the plan.
        """
        # Plan requires ordering
        plan = {
            "tables": [{"name": "users", "alias": "u"}],
            "order_by": [{"expr": "u.name", "direction": "asc"}],
            "limit": 10,
            "select_columns": ["u.name"],
            "needed_columns": ["u.name"]
        }
    
        state = GraphState(user_query="test", plan=plan)
        node = GeneratorNode(profile_engine="sqlite", row_limit=100)
        
        new_state = node(state)
        
        sql = new_state.sql_draft["sql"].lower()
        
        assert "order by" in sql
        assert "limit" in sql
        # Check order: ORDER BY comes before LIMIT
        assert sql.index("order by") < sql.index("limit")

if __name__ == "__main__":
    unittest.main()
