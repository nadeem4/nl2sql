
import pytest
from unittest.mock import MagicMock
from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.nodes.planner.schemas import PlanModel, TableRef, SelectItem, Expr
from nl2sql.datasources import DatasourceRegistry
from nl2sql_adapter_sdk import DatasourceAdapter, Table, Column

@pytest.fixture
def mock_registry():
    registry = MagicMock(spec=DatasourceRegistry)
    adapter = MagicMock(spec=DatasourceAdapter)
    adapter.fetch_schema.return_value.tables = []
    registry.get_adapter.return_value = adapter
    return registry

@pytest.fixture
def mock_tables():
    return [
        Table(
            name="users", 
            columns=[
                Column(name="id", type="INT"),
                Column(name="name", type="VARCHAR"),
                Column(name="email", type="VARCHAR")
            ]
        ),
        Table(
            name="orders", 
            columns=[
                Column(name="id", type="INT"),
                Column(name="user_id", type="INT"),
                Column(name="amount", type="DECIMAL")
            ]
        )
    ]

def _col_expr(alias: str, col: str) -> Expr:
    return Expr(kind="column", alias=alias, column_name=col)

def test_validator_valid_plan(mock_tables, mock_registry):
    validator = LogicalValidatorNode(registry=mock_registry)
    plan = PlanModel(
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[
            SelectItem(expr=_col_expr("u", "name"), ordinal=0)
        ],
        joins=[]
    )
    state = GraphState(
        user_query="q", 
        relevant_tables=mock_tables, 
        plan=plan, 
        selected_datasource_id="ds1",
        user_context={"allowed_tables": ["*"]}
    )
    
    new_state = validator(state)
    assert not new_state.get("errors")

def test_validator_invalid_table(mock_tables, mock_registry):
    validator = LogicalValidatorNode(registry=mock_registry)
    plan = PlanModel(
        tables=[TableRef(name="invalid_table", alias="x", ordinal=0)],
        select_items=[
            SelectItem(expr=_col_expr("x", "id"), ordinal=0)
        ]
    )
    state = GraphState(
        user_query="q", 
        relevant_tables=mock_tables, 
        plan=plan, 
        selected_datasource_id="ds1",
        user_context={"allowed_tables": ["*"]}
    )
    
    new_state = validator(state)
    # Actual: "Table 'invalid_table' not found in relevant tables."
    assert any("not found in relevant tables" in e.message for e in new_state["errors"])


def test_validator_undefined_alias(mock_tables, mock_registry):
    validator = LogicalValidatorNode(registry=mock_registry)
    plan = PlanModel(
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[
             SelectItem(expr=_col_expr("z", "name"), ordinal=0)
        ]
    )
    state = GraphState(
        user_query="q", 
        relevant_tables=mock_tables, 
        plan=plan, 
        selected_datasource_id="ds1",
        user_context={"allowed_tables": ["*"]}
    )
    
    new_state = validator(state)
    # Actual: "Column 'name' uses undeclared alias 'z'."
    assert any("uses undeclared alias 'z'" in e.message for e in new_state["errors"])


def test_validator_invalid_column_name(mock_tables, mock_registry):
    validator = LogicalValidatorNode(registry=mock_registry)
    plan = PlanModel(
        tables=[TableRef(name="users", alias="u", ordinal=0)],
        select_items=[
            SelectItem(expr=_col_expr("u", "invalid_col"), ordinal=0)
        ]
    )
    state = GraphState(
        user_query="q", 
        relevant_tables=mock_tables, 
        plan=plan, 
        selected_datasource_id="ds1",
        user_context={"allowed_tables": ["*"]}
    )
    
    new_state = validator(state)
    # Actual: "Column 'invalid_col' does not exist in table alias 'u'."
    assert any("does not exist in table alias 'u'" in e.message for e in new_state["errors"])
