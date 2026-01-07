import unittest
from unittest.mock import MagicMock
from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.state import GraphState
from nl2sql.datasources import DatasourceRegistry
from nl2sql_adapter_sdk import DatasourceAdapter
from nl2sql.pipeline.nodes.planner.schemas import PlanModel
from nl2sql.datasources import DatasourceProfile

class TestGeneratorHaving(unittest.TestCase):
    def setUp(self):
        self.mock_registry = MagicMock(spec=DatasourceRegistry)
        self.mock_adapter = MagicMock(spec=DatasourceAdapter)
        self.mock_profile = MagicMock(spec=DatasourceProfile)
        
        self.mock_registry.get_adapter.return_value = self.mock_adapter
        self.mock_registry.get_profile.return_value = self.mock_profile
        self.mock_registry.get_dialect.return_value = "sqlite"
        
        self.mock_profile.row_limit = 100
        self.mock_profile.engine = "sqlite"
        
        self.node = GeneratorNode(registry=self.mock_registry)

    def test_having_generated_correctly(self):
        """
        Test that GeneratorNode generates HAVING correctly from the plan.
        """
        plan = {
            "tables": [{"name": "orders", "alias": "o", "ordinal": 0}],
            "select_items": [
                {"expr": {"kind": "column", "column_name": "user_id", "alias": "o"}, "ordinal": 0},
                {"expr": {"kind": "func", "func_name": "COUNT", "args": [{"kind": "column", "column_name": "*"}], "is_aggregate": True}, "alias": "cnt", "ordinal": 1}
            ],
            "group_by": [{"expr": {"kind": "column", "column_name": "user_id", "alias": "o"}, "ordinal": 0}],
            "having": {
                "kind": "binary",
                "op": ">",
                "left": {"kind": "func", "func_name": "COUNT", "args": [{"kind": "column", "column_name": "*"}]},
                "right": {"kind": "literal", "value": 5}
            },
            "where": None,
            "joins": [],
            "order_by": []
        }
    
        state = GraphState(user_query="test", plan=PlanModel(**plan).model_dump(), selected_datasource_id="test_ds")
        
        new_state = self.node(state)
        
        if new_state.get("errors"):
            self.fail(f"Errors: {new_state['errors']}")
        
        sql = new_state["sql_draft"].lower()
        
        self.assertIn("having", sql)
        self.assertTrue("count(*) > 5" in sql or "count(*) > '5'" in sql or 'count(*) > 5' in sql)

    def test_having_with_alias(self):
        """
        Test HAVING with alias if supported (or just expression).
        """
        plan = {
            "tables": [{"name": "orders", "alias": "o", "ordinal": 0}],
            "select_items": [
                {"expr": {"kind": "column", "column_name": "user_id", "alias": "o"}, "ordinal": 0},
                {"expr": {"kind": "func", "func_name": "COUNT", "args": [{"kind": "column", "column_name": "*"}], "is_aggregate": True}, "alias": "cnt", "ordinal": 1}
            ],
            "group_by": [{"expr": {"kind": "column", "column_name": "user_id", "alias": "o"}, "ordinal": 0}],
            "having": {
                "kind": "binary",
                "op": ">",
                "left": {"kind": "column", "column_name": "cnt"},
                "right": {"kind": "literal", "value": 10}
            },
            "where": None,
            "joins": [],
            "order_by": []
        }
    
        state = GraphState(user_query="test", plan=plan, selected_datasource_id="test_ds")
        
        new_state = self.node(state)
        
        if new_state.get("errors"):
            self.fail(f"Errors: {new_state['errors']}")
        
        sql = new_state["sql_draft"].lower()
        
        self.assertIn("having", sql)
        self.assertTrue("cnt > 10" in sql or "cnt > '10'" in sql)

if __name__ == "__main__":
    unittest.main()
