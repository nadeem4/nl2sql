import unittest
from unittest.mock import MagicMock
from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery

class TestSyntaxFix(unittest.TestCase):
    def test_order_by_generated_correctly(self):
        """
        Test that GeneratorNode generates ORDER BY correctly from the plan.
        """
        # Plan requires ordering
        plan = {
            "tables": [{"name": "users", "alias": "u"}],
            "order_by": [{"column": {"expr": "u.name"}, "direction": "asc"}],
            "limit": 10,
            "select_columns": [{"expr": "u.name"}],
            "filters": [],
            "joins": [],
            "group_by": [],
            "having": []
        }
    
        ctx = MagicMock()
        ctx.ds_registry.get_adapter.return_value = MagicMock(
            row_limit=100,
            max_bytes=1000,
            get_dialect=lambda: "sqlite",
        )
        state = SubgraphExecutionState(
            ast_planner_response=ASTPlannerResponse(plan=plan),
            sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="test"),
            trace_id="t",
        )
        node = GeneratorNode(ctx=ctx)
        
        new_state = node(state)
        
        if new_state.get("errors"):
            print(f"Errors: {new_state['errors']}")

        sql = new_state["generator_response"].sql_draft.lower()
        
        assert "order by" in sql
        assert "limit" in sql
        # Check order: ORDER BY comes before LIMIT
        assert sql.index("order by") < sql.index("limit")

if __name__ == "__main__":
    unittest.main()
