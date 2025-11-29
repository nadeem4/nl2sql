import unittest
from unittest.mock import MagicMock
from nl2sql.nodes.generator.node import GeneratorNode
from nl2sql.schemas import GraphState, SQLModel, PlanModel, OrderSpec

class TestSyntaxFix(unittest.TestCase):
    def test_order_by_appended_correctly(self):
        """
        Test that if the LLM generates SQL with LIMIT but misses ORDER BY,
        the GeneratorNode inserts ORDER BY *before* LIMIT, not after.
        """
        # Plan requires ordering
        plan = PlanModel(
            tables=[{"name": "users"}],
            order_by=[OrderSpec(expr="users.name", direction="asc")],
            limit=10
        )
        
        # LLM generates SQL with LIMIT but NO ORDER BY
        mock_llm = MagicMock()
        mock_llm.return_value = SQLModel(sql="SELECT name FROM users LIMIT 10")
        
        node = GeneratorNode(profile_engine="sqlite", row_limit=100, llm=mock_llm)
        
        state = GraphState(
            user_query="get users ordered by name",
            plan=plan.model_dump(), # Node expects dict for plan currently? 
                                    # Actually GeneratorNode expects state.plan to be dict (from graph state)
            schema_info=None
        )
        
        # Run node
        new_state = node(state)
        
        # Check generated SQL
        sql = new_state.sql_draft["sql"]
        print(f"Generated SQL: {sql}")
        
        # It should be: SELECT * FROM users ORDER BY users.name ASC LIMIT 10
        # NOT: SELECT * FROM users LIMIT 10 ORDER BY users.name ASC
        
        self.assertIn("ORDER BY users.name ASC", sql)
        
        # Check position: ORDER BY must be before LIMIT
        order_idx = sql.find("ORDER BY")
        limit_idx = sql.find("LIMIT")
        
        self.assertNotEqual(order_idx, -1, "ORDER BY missing")
        self.assertNotEqual(limit_idx, -1, "LIMIT missing")
        self.assertLess(order_idx, limit_idx, "ORDER BY must appear before LIMIT")

if __name__ == "__main__":
    unittest.main()
