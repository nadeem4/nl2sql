
import pytest
from unittest.mock import MagicMock
from types import SimpleNamespace

from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.nodes.ast_planner.schemas import (
    PlanModel, TableRef, JoinSpec, SelectItem,
    Expr, ASTPlannerResponse,
)
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery

@pytest.fixture
def generator():
    ctx = MagicMock()
    ctx.ds_registry.get_adapter.return_value = SimpleNamespace(
        row_limit=1000,
        max_bytes=100000,
        get_dialect=lambda: "sqlite"
    )
    return GeneratorNode(ctx=ctx)

class TestGeneratorNode:
    """Consolidated Generator Node Tests."""

    def test_deep_recursion(self, generator):
        """(col1 > 10 OR col2 < 5) AND col3 = 'test'"""
        where_clause = Expr(
            kind="binary", op="AND",
            left=Expr(
                kind="binary", op="OR",
                left=Expr(kind="binary", op=">", left=Expr(kind="column", column_name="col1", alias="t1"), right=Expr(kind="literal", value=10)),
                right=Expr(kind="binary", op="<", left=Expr(kind="column", column_name="col2", alias="t1"), right=Expr(kind="literal", value=5))
            ),
            right=Expr(kind="binary", op="=", left=Expr(kind="column", column_name="col3", alias="t1"), right=Expr(kind="literal", value="test"))
        )

        plan = PlanModel(
            query_type="READ",
            tables=[TableRef(name="users", alias="t1", ordinal=0)],
            select_items=[
                SelectItem(expr=Expr(kind="column", column_name="id", alias="t1"), ordinal=0),
                SelectItem(expr=Expr(kind="column", column_name="name", alias="t1"), ordinal=1)
            ],
            where=where_clause
        )
        
        state = SubgraphExecutionState(
            sub_query=SubQuery(id="sq1", datasource_id="mock_db", intent="q"),
            ast_planner_response=ASTPlannerResponse(plan=plan),
            trace_id="t",
        )
        result = generator(state)
        
        assert not result.get("errors")
        sql = result["generator_response"].sql_draft
        
        assert "(t1.col1 > 10 OR t1.col2 < 5)" in sql
        assert "AND t1.col3 = 'test'" in sql

    def test_ordinal_sorting(self, generator):
        """Ensure output order follows 'ordinal' field, not list index."""
        t1 = TableRef(name="users", alias="u", ordinal=0)
        t2 = TableRef(name="orders", alias="o", ordinal=1)
        
        # Input list is reversed: [t2, t1], but t1 has ordinal 0
        plan = PlanModel(
            tables=[t2, t1], 
            joins=[
                JoinSpec(
                    left_alias="u", right_alias="o", join_type="inner", ordinal=0,
                    condition=Expr(kind="binary", op="=", left=Expr(kind="column", column_name="id", alias="u"), right=Expr(kind="column", column_name="user_id", alias="o"))
                )
            ],
            select_items=[
                SelectItem(expr=Expr(kind="column", column_name="id", alias="u"), ordinal=1, alias="id_second"),
                SelectItem(expr=Expr(kind="column", column_name="date", alias="o"), ordinal=0, alias="date_first")
            ]
        )
        
        state = SubgraphExecutionState(
            sub_query=SubQuery(id="sq1", datasource_id="mock", intent="q"),
            ast_planner_response=ASTPlannerResponse(plan=plan),
            trace_id="t",
        )
        result = generator(state)
        
        assert not result.get("errors")
        sql = result["generator_response"].sql_draft
        
        # Check SELECT clause order: date_first (0) < id_second (1)
        col_date_idx = sql.find("date_first")
        col_id_idx = sql.find("id_second")
        assert col_date_idx != -1 and col_id_idx != -1
        assert col_date_idx < col_id_idx

    def test_having_clause(self, generator):
        """Test HAVING clause generation."""
        # Using dict definition for brevity/legacy compat check (GeneratorNode supports both Obj and Dict via Pydantic parse)
        # But let's be safe and use objects if possible. 
        # Actually, GraphState.plan is Dict (model_dump). So passing dict is correct integration.
        plan = {
            "tables": [{"name": "orders", "alias": "o", "ordinal": 0}],
            "select_items": [
                {"expr": {"kind": "column", "column_name": "user_id", "alias": "o"}, "ordinal": 0},
                {"expr": {"kind": "func", "func_name": "COUNT", "args": [{"kind": "column", "column_name": "*"}], "is_aggregate": True}, "alias": "cnt", "ordinal": 1}
            ],
            "group_by": [{"expr": {"kind": "column", "column_name": "user_id", "alias": "o"}, "ordinal": 0}],
            "having": {
                "kind": "binary", "op": ">",
                "left": {"kind": "func", "func_name": "COUNT", "args": [{"kind": "column", "column_name": "*"}]},
                "right": {"kind": "literal", "value": 5}
            },
            "where": None, "joins": [], "order_by": []
        }
    
        state = SubgraphExecutionState(
            ast_planner_response=ASTPlannerResponse(plan=PlanModel.model_validate(plan)),
            sub_query=SubQuery(id="sq1", datasource_id="test_ds", intent="q"),
            trace_id="t",
        )
        new_state = generator(state)
        
        assert not new_state.get("errors")
        sql = new_state["generator_response"].sql_draft.lower()
        assert "having" in sql
        assert "count(*) > 5" in sql or "count(*) > '5'" in sql

    def test_generator_slice_fix(self, generator):
        """
        Reproduce the 'unhashable type: slice' error when group_by contains dicts.
        """
        plan = {
            "tables": [{"name": "users", "alias": "u", "ordinal": 0}],
            "select_items": [{"expr": {"kind": "column", "column_name": "id", "alias": "u"}, "ordinal": 0}],
            "group_by": [{"expr": {"kind": "column", "column_name": "id", "alias": "u"}, "ordinal": 0}], 
            "where": None, "joins": [], "having": None, "order_by": []
        }
    
        state = SubgraphExecutionState(
            ast_planner_response=ASTPlannerResponse(plan=PlanModel.model_validate(plan)),
            sub_query=SubQuery(id="sq1", datasource_id="test_ds", intent="q"),
            trace_id="t",
        )
        new_state = generator(state)
        
        assert not new_state.get("errors")
        assert "GROUP BY" in new_state["generator_response"].sql_draft
