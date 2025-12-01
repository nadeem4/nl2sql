
import unittest
from nl2sql.nodes.generator_node import GeneratorNode
from nl2sql.schemas import GraphState

class TestSliceErrorReproduction(unittest.TestCase):
    def test_generator_group_by_dict_error(self):
        """
        Reproduce the 'unhashable type: slice' error (or TypeError) when passing a dict to sqlglot.parse_one
        in _build_group_by.
        """
        # Plan with group_by as ColumnRef dicts (new schema)
        plan = {
            "tables": [{"name": "users", "alias": "u"}],
            "select_columns": [{"expr": "u.id"}],
            "group_by": [{"expr": "u.id", "is_derived": False}], # This is a dict!
            "filters": [],
            "joins": [],
            "having": [],
            "order_by": []
        }
    
        state = GraphState(user_query="test", plan=plan)
        node = GeneratorNode(profile_engine="sqlite", row_limit=100)
        
        # This should NOT raise an exception now
        new_state = node(state)
        
        if new_state.errors:
            print(f"Errors: {new_state.errors}")
        
        # We expect NO errors
        self.assertFalse(new_state.errors, f"Expected no errors, got: {new_state.errors}")
        self.assertIsNotNone(new_state.sql_draft)
        self.assertIn("GROUP BY", new_state.sql_draft["sql"])

if __name__ == "__main__":
    unittest.main()
