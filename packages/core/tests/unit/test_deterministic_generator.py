import pytest
from nl2sql.datasources import DatasourceRegistry
from nl2sql.pipeline.nodes.generator.node import GeneratorNode
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.nodes.planner.schemas import (
    PlanModel, TableRef, JoinSpec, SelectItem, 
    Expr
)

class MockRegistry(DatasourceRegistry):
    def __init__(self):
        super().__init__({})
        
    def get_profile(self, id):
        from types import SimpleNamespace
        return SimpleNamespace(engine="postgres", row_limit=1000)
    
    def get_adapter(self, id):
        from types import SimpleNamespace
        return SimpleNamespace(capabilities=lambda: SimpleNamespace())

@pytest.fixture
def generator():
    return GeneratorNode(registry=MockRegistry())

def test_deep_recursion(generator):
    """(col1 > 10 OR col2 < 5) AND col3 = 'test'"""
    
    # Unified Expr usage
    where_clause = Expr(
        kind="binary",
        op="AND",
        left=Expr(
            kind="binary",
            op="OR",
            left=Expr(
                kind="binary",
                op=">",
                left=Expr(kind="column", name="col1", table="t1"),
                right=Expr(kind="literal", value=10)
            ),
            right=Expr(
                kind="binary",
                op="<",
                left=Expr(kind="column", name="col2", table="t1"),
                right=Expr(kind="literal", value=5)
            )
        ),
        right=Expr(
            kind="binary",
            op="=",
            left=Expr(kind="column", name="col3", table="t1"),
            right=Expr(kind="literal", value="test")
        )
    )

    plan = PlanModel(
        query_type="READ",
        tables=[TableRef(name="users", alias="t1", ordinal=0)],
        select_items=[
            SelectItem(expr=Expr(kind="column", name="id", table="t1"), ordinal=0),
            SelectItem(expr=Expr(kind="column", name="name", table="t1"), ordinal=1)
        ],
        where=where_clause
    )
    
    state = GraphState(
        user_query="test deep recursion",
        selected_datasource_id="mock_db",
        plan=plan.model_dump()
    )
    
    result = generator(state)
    assert not result.get("errors"), f"Errors: {result.get('errors')}"
    sql = result["sql_draft"]
    
    print(f"Generated SQL: {sql}")
    
    # Check that precedence is respected (parens around OR)
    assert "t1.col1 > 10" in sql
    assert "t1.col2 < 5" in sql
    assert "OR" in sql
    assert "(t1.col1 > 10 OR t1.col2 < 5)" in sql

def test_ordinal_sorting(generator):
    """Ensure output order follows 'ordinal' field, not list index."""
    
    t1 = TableRef(name="users", alias="u", ordinal=0)
    t2 = TableRef(name="orders", alias="o", ordinal=1)
    
    # Input list is reversed: [t2, t1], but t1 has ordinal 0
    
    plan = PlanModel(
        tables=[t2, t1], 
        joins=[
            JoinSpec(
                left_table="users", right_table="orders", join_type="inner",
                ordinal=0,
                condition=Expr(
                    kind="binary",
                    op="=",
                    left=Expr(kind="column", name="id", table="u"),
                    right=Expr(kind="column", name="user_id", table="o")
                )
            )
        ],
        select_items=[
            SelectItem(expr=Expr(kind="column", name="id", table="u"), ordinal=1, alias="id_second"),
            SelectItem(expr=Expr(kind="column", name="date", table="o"), ordinal=0, alias="date_first")
        ]
    )
    
    state = GraphState(
        user_query="test ordinal", 
        selected_datasource_id="mock", 
        plan=plan.model_dump()
    )
    result = generator(state)
    assert not result.get("errors")
    sql = result["sql_draft"]
    
    # Check FROM clause order
    assert "FROM users" in sql or "FROM \"users\"" in sql
    assert "JOIN orders" in sql or "JOIN \"orders\"" in sql
    
    # Check SELECT clause order
    # "date_first" (ordinal 0) should appear before "id_second" (ordinal 1)
    col_date_idx = sql.find("date_first")
    col_id_idx = sql.find("id_second")
    
    assert col_date_idx != -1
    assert col_id_idx != -1
    assert col_date_idx < col_id_idx, f"Outputs not sorted! SQL: {sql}"
