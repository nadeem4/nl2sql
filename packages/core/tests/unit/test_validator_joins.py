
import unittest
from unittest.mock import MagicMock
from nl2sql.core.nodes.planner.node import PlannerNode
from nl2sql.core.nodes.schema.schemas import SchemaInfo, TableInfo, ColumnInfo
from nl2sql.core.nodes.planner.schemas import PlanModel
from nl2sql.core.schemas import GraphState
from nl2sql.core.datasource_registry import DatasourceRegistry

class TestMissingTablesReproduction(unittest.TestCase):
    def test_planner_missing_tables_in_plan(self):
        """
        Reproduce the issue where Planner fails to list joined tables in the 'tables' field.
        """
        # Mock Registry
        mock_registry = MagicMock(spec=DatasourceRegistry)
        
        # Schema with 3 tables
        schema_info = SchemaInfo(
            tables=[
                TableInfo(
                    name="factories", 
                    alias="t1", 
                    columns=[
                        ColumnInfo(name="t1.id", original_name="id", type="INT"),
                        ColumnInfo(name="t1.name", original_name="name", type="VARCHAR")
                    ]
                ),
                TableInfo(
                    name="machines", 
                    alias="t2", 
                    columns=[
                        ColumnInfo(name="t2.id", original_name="id", type="INT"),
                        ColumnInfo(name="t2.factory_id", original_name="factory_id", type="INT"),
                        ColumnInfo(name="t2.name", original_name="name", type="VARCHAR")
                    ]
                ),
                TableInfo(
                    name="maintenance_logs", 
                    alias="t3", 
                    columns=[
                        ColumnInfo(name="t3.id", original_name="id", type="INT"),
                        ColumnInfo(name="t3.machine_id", original_name="machine_id", type="INT"),
                        ColumnInfo(name="t3.log", original_name="log", type="TEXT")
                    ]
                )
            ]
        )
        
        # Mock LLM that returns a plan with missing tables in 'tables' list
        # It joins factories -> machines -> maintenance_logs
        # But only lists 'factories' in 'tables'
        mock_llm = MagicMock()
        
        # Simulate a "correct" plan from the LLM
        good_plan_data = {
            "entity_ids": ["e1"],
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
        
        # PlannerNode now takes registry
        node = PlannerNode(registry=mock_registry, llm=mock_llm)
        
        # Mock Intent Entities
        from nl2sql.core.nodes.intent.schemas import Entity, EntityRole
        state = GraphState(
            user_query="Show maintenance logs for all factories",
            schema_info=schema_info,
            selected_datasource_id="ds1",
            entities=[Entity(entity_id="e1", name="factories", role=EntityRole.REFERENCE)]
        )
        
        # Planner returns a DICT with updates
        planner_updates = node(state)
        
        # Merge updates into state for Validator
        # Note: GraphState is Pydantic, so we use model_copy(update=...)
        # But here plan is updated.
        state_dict = state.model_dump()
        state_dict.update(planner_updates)
        new_state = GraphState(**state_dict)
        
        # Now run Validator
        from nl2sql.core.nodes.validator.node import ValidatorNode
        validator = ValidatorNode(registry=mock_registry)
        
        # Validator returns DICT
        validation_updates = validator(new_state)
        
        # We expect NO errors in the updates
        self.assertFalse(validation_updates.get("errors"), f"Expected no validation errors, got: {validation_updates.get('errors')}")

if __name__ == "__main__":
    unittest.main()
