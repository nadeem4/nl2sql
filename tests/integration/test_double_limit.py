import unittest
from unittest.mock import MagicMock
from nl2sql.nodes.generator.node import GeneratorNode
from nl2sql.schemas import GraphState, SQLModel, PlanModel

class TestDoubleLimit(unittest.TestCase):
    def test_no_double_limit(self):
        """
        Test that GeneratorNode does not append a second LIMIT if one exists.
        """
        # Plan with limit
        plan = PlanModel(
            tables=[{"name": "users"}],
            limit=100
        )
        
        # LLM generates SQL with LIMIT
        mock_llm = MagicMock()
        mock_llm.return_value = SQLModel(sql="SELECT name FROM users LIMIT 100")
        
        node = GeneratorNode(profile_engine="sqlite", row_limit=100, llm=mock_llm)
        
        state = GraphState(
            user_query="get users",
            plan=plan.model_dump(),
            schema_info=None
        )
        
        # Run node
        new_state = node(state)
        
        # Check generated SQL
        sql = new_state.sql_draft["sql"]
        print(f"Generated SQL: {sql}")
        
        # Should have only one LIMIT
        self.assertEqual(sql.upper().count("LIMIT"), 1, f"Double LIMIT detected: {sql}")

if __name__ == "__main__":
    unittest.main()
