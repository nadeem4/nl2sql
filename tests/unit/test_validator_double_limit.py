import unittest
from nl2sql.nodes.validator_node import ValidatorNode
from nl2sql.schemas import GraphState, GeneratedSQL

class TestValidatorDoubleLimit(unittest.TestCase):
    def test_double_limit_detection(self):
        """
        Test if ValidatorNode catches 'LIMIT 100 LIMIT 100'.
        """
        sql = "SELECT name FROM users LIMIT 100 LIMIT 100"
        state = GraphState(
            user_query="test",
            sql_draft=GeneratedSQL(sql=sql, rationale="test", limit_enforced=True, draft_only=False),
            schema_info=None
        )
        
        node = ValidatorNode(row_limit=1000)
        new_state = node(state)
        
        print(f"Errors: {new_state.errors}")
        
        # We expect an error either from parsing or specific check
        self.assertTrue(len(new_state.errors) > 0, "Validator should catch double LIMIT")
        self.assertIn("multiple limit clauses detected", new_state.errors[0].lower(), "Should detect multiple limits")

if __name__ == "__main__":
    unittest.main()
