
import unittest
from unittest.mock import MagicMock
from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.state import GraphState
from nl2sql.datasources import DatasourceRegistry
from nl2sql_adapter_sdk import DatasourceAdapter, CapabilitySet
from nl2sql.datasources import DatasourceProfile

class TestSliceErrorReproduction(unittest.TestCase):
    def setUp(self):
        self.mock_registry = MagicMock(spec=DatasourceRegistry)
        self.mock_adapter = MagicMock(spec=DatasourceAdapter)
        self.mock_profile = MagicMock(spec=DatasourceProfile)
        
        self.mock_registry.get_adapter.return_value = self.mock_adapter
        self.mock_registry.get_profile.return_value = self.mock_profile
        
        self.mock_adapter.capabilities.return_value = CapabilitySet(
            supports_cte=True
        )
        self.mock_profile.row_limit = 100
        self.mock_profile.engine = "sqlite"
        
        self.node = GeneratorNode(registry=self.mock_registry)

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
    
        state = GraphState(user_query="test", plan=plan, selected_datasource_id="test_ds")
        
        # This should NOT raise an exception now
        new_state = self.node(state)
        
        if new_state.get("errors"):
            print(f"Errors: {new_state['errors']}")
        
        # We expect NO errors
        self.assertFalse(new_state.get("errors"), f"Expected no errors, got: {new_state.get('errors')}")
        self.assertIsNotNone(new_state.get("sql_draft"))
        self.assertIn("GROUP BY", new_state["sql_draft"])

if __name__ == "__main__":
    unittest.main()
