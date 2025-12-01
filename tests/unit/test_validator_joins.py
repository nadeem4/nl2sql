
import unittest
from unittest.mock import MagicMock
from nl2sql.nodes.planner.node import PlannerNode
from nl2sql.schemas import GraphState, SchemaInfo, TableInfo, PlanModel, TableRef, ColumnRef

class TestMissingTablesReproduction(unittest.TestCase):
    def test_planner_missing_tables_in_plan(self):
        """
        Reproduce the issue where Planner fails to list joined tables in the 'tables' field.
        """
        # Schema with 3 tables
        schema_info = SchemaInfo(
            tables=[
                TableInfo(name="factories", alias="t1", columns=["t1.id", "t1.name"]),
                TableInfo(name="machines", alias="t2", columns=["t2.id", "t2.factory_id", "t2.name"]),
                TableInfo(name="maintenance_logs", alias="t3", columns=["t3.id", "t3.machine_id", "t3.log"])
            ]
        )
        
        # Mock LLM that returns a plan with missing tables in 'tables' list
        # It joins factories -> machines -> maintenance_logs
        # But only lists 'factories' in 'tables'
        mock_llm = MagicMock()
        
        # Simulate a "correct" plan from the LLM
        good_plan_data = {
            "reasoning": "Join factories to machines to logs.",
            "tables": [
                {"name": "factories", "alias": "t1"},
                {"name": "machines", "alias": "t2"},
                {"name": "maintenance_logs", "alias": "t3"}
            ],
            "joins": [
                {"left": "factories", "right": "machines", "on": ["t1.id = t2.factory_id"], "join_type": "inner"},
                {"left": "machines", "right": "maintenance_logs", "on": ["t2.id = t3.machine_id"], "join_type": "inner"}
            ],
            "select_columns": [{"expr": "t3.log"}],
            "filters": [],
            "group_by": [],
            "having": [],
            "order_by": []
        }
        
        mock_plan = PlanModel(**good_plan_data)
        mock_llm.return_value = mock_plan
        
        node = PlannerNode(llm=mock_llm)
        
        state = GraphState(
            user_query="Show maintenance logs for all factories",
            schema_info=schema_info
        )
        
        new_state = node(state)
        
        # Now run Validator
        from nl2sql.nodes.validator_node import ValidatorNode
        validator = ValidatorNode()
        validated_state = validator(new_state)
        
        # We expect NO errors
        self.assertFalse(validated_state.errors, f"Expected no validation errors, got: {validated_state.errors}")

if __name__ == "__main__":
    unittest.main()
