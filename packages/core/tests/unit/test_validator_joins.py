
import unittest
from unittest.mock import MagicMock
from nl2sql.pipeline.nodes.planner.node import PlannerNode
from nl2sql.pipeline.nodes.planner.schemas import PlanModel, TableRef, JoinSpec, SelectItem, Expr
from nl2sql.pipeline.state import GraphState
from nl2sql.datasources import DatasourceRegistry
from nl2sql_adapter_sdk import Table, Column

class TestMissingTablesReproduction(unittest.TestCase):
    def test_planner_missing_tables_in_plan(self):
        """
        Reproduce the issue where Planner fails to list joined tables in the 'tables' field.
        """
        # Mock Registry
        mock_registry = MagicMock(spec=DatasourceRegistry)
        
        # Schema with 3 tables
        relevant_tables = [
            Table(
                name="factories", 
                columns=[
                    Column(name="id", type="INT"),
                    Column(name="name", type="VARCHAR")
                ]
            ),
            Table(
                name="machines", 
                columns=[
                    Column(name="id", type="INT"),
                    Column(name="factory_id", type="INT"),
                    Column(name="name", type="VARCHAR")
                ]
            ),
            Table(
                name="maintenance_logs", 
                columns=[
                    Column(name="id", type="INT"),
                    Column(name="machine_id", type="INT"),
                    Column(name="log", type="TEXT")
                ]
            )
        ]
        
        # Mock LLM that returns a plan with missing tables in 'tables' list
        # It joins factories -> machines -> maintenance_logs
        # But only lists 'factories' in 'tables'
        mock_llm = MagicMock()
        
        # Simulate a "correct" plan from the LLM - using Objects because serialization depends on Node impl.
        # But the PlannerNode expects the LLM to return a PlanModel object.
        
        def _col(a, c): return Expr(kind="column", alias=a, column_name=c)
        def _bin_eq(l, r): return Expr(kind="binary", op="=", left=l, right=r)
        
        mock_plan = PlanModel(
            tables=[
                TableRef(name="factories", alias="t1", ordinal=0),
                TableRef(name="machines", alias="t2", ordinal=1),
                TableRef(name="maintenance_logs", alias="t3", ordinal=2)
            ],
            joins=[
                JoinSpec(
                    left_alias="t1", right_alias="t2", join_type="inner", ordinal=0,
                    condition=_bin_eq(_col("t1", "id"), _col("t2", "factory_id"))
                ),
                JoinSpec(
                    left_alias="t2", right_alias="t3", join_type="inner", ordinal=1,
                    condition=_bin_eq(_col("t2", "id"), _col("t3", "machine_id"))
                )
            ],
            select_items=[
                SelectItem(expr=_col("t3", "log"), ordinal=0)
            ],
            reasoning="Join factories to machines to logs."
        )
        
        mock_llm.return_value = mock_plan
        
        # PlannerNode now takes registry
        node = PlannerNode(registry=mock_registry, llm=mock_llm)
        
        state = GraphState(
            user_query="Show maintenance logs for all factories",
            relevant_tables=relevant_tables,
            selected_datasource_id="ds1",
            user_context={"allowed_tables": ["*"]}
        )
        
        # Planner returns a DICT with updates
        planner_updates = node(state)
        
        # Merge updates into state for Validator
        state_dict = state.model_dump()
        state_dict.update(planner_updates)
        new_state = GraphState(**state_dict)
        
        # Now run Validator
        from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
        validator = LogicalValidatorNode(registry=mock_registry)
        
        # Validator returns DICT
        validation_updates = validator(new_state)
        
        # We expect NO errors in the updates
        self.assertFalse(validation_updates.get("errors"), f"Expected no validation errors, got: {validation_updates.get('errors')}")

if __name__ == "__main__":
    unittest.main()
