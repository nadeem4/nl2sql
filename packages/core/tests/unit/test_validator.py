
import pytest
from unittest.mock import MagicMock
from nl2sql.pipeline.nodes.validator.node import ValidatorNode
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.nodes.schema.schemas import SchemaInfo, TableInfo, ColumnInfo
from nl2sql.pipeline.nodes.planner.schemas import PlanModel, TableRef, ColumnRef
from nl2sql.datasources import DatasourceRegistry
from nl2sql_adapter_sdk import DatasourceAdapter, CapabilitySet

@pytest.fixture
def mock_registry():
    registry = MagicMock(spec=DatasourceRegistry)
    adapter = MagicMock(spec=DatasourceAdapter)
    adapter.capabilities.return_value = CapabilitySet(
        supports_cte=True
    )
    registry.get_adapter.return_value = adapter
    return registry

@pytest.fixture
def mock_schema():
    return SchemaInfo(
        tables=[
            TableInfo(
                name="users", 
                alias="u", 
                columns=[
                    ColumnInfo(name="u.id", original_name="id", type="INT"),
                    ColumnInfo(name="u.name", original_name="name", type="VARCHAR"),
                    ColumnInfo(name="u.email", original_name="email", type="VARCHAR")
                ]
            ),
            TableInfo(
                name="orders", 
                alias="o", 
                columns=[
                    ColumnInfo(name="o.id", original_name="id", type="INT"),
                    ColumnInfo(name="o.user_id", original_name="user_id", type="INT"),
                    ColumnInfo(name="o.amount", original_name="amount", type="DECIMAL")
                ]
            )
        ]
    )

def test_validator_valid_plan(mock_schema, mock_registry):
    validator = ValidatorNode(registry=mock_registry)
    plan = PlanModel(
        entity_ids=["e1"],
        tables=[TableRef(name="users", alias="u")],
        select_columns=[ColumnRef(expr="u.name")],
        filters=[],
        joins=[],
        group_by=[],
        order_by=[],
        having=[]
    )
    state = GraphState(user_query="q", schema_info=mock_schema, plan=plan.model_dump(), selected_datasource_id="ds1")
    
    new_state = validator(state)
    assert not new_state.get("errors")

def test_validator_invalid_table(mock_schema, mock_registry):
    validator = ValidatorNode(registry=mock_registry)
    plan = PlanModel(
        entity_ids=["e1"],
        tables=[TableRef(name="invalid_table", alias="x")],
        select_columns=[ColumnRef(expr="x.id")]
    )
    state = GraphState(user_query="q", schema_info=mock_schema, plan=plan.model_dump(), selected_datasource_id="ds1")
    
    new_state = validator(state)
    assert any("not found in schema" in e.message for e in new_state["errors"])

def test_validator_alias_mismatch(mock_schema, mock_registry):
    validator = ValidatorNode(registry=mock_registry)
    plan = PlanModel(
        entity_ids=["e1"],
        tables=[TableRef(name="users", alias="x")], # Wrong alias, schema has 'u'
        select_columns=[ColumnRef(expr="x.name")]
    )
    state = GraphState(user_query="q", schema_info=mock_schema, plan=plan.model_dump(), selected_datasource_id="ds1")
    
    new_state = validator(state)
    assert new_state["errors"], "Expected errors but found none"
    error_msgs = [e.message for e in new_state["errors"]]
    assert any("not found in schema" in msg or "alias mismatch" in msg for msg in error_msgs), f"Unexpected errors: {error_msgs}"

def test_validator_invalid_column_alias_usage(mock_schema, mock_registry):
    validator = ValidatorNode(registry=mock_registry)
    # Alias used in filter (not allowed)
    plan = PlanModel(
        entity_ids=["e1"],
        tables=[TableRef(name="users", alias="u")],
        select_columns=[ColumnRef(expr="u.name", alias="user_name")],
        filters=[{"column": {"expr": "u.name", "alias": "user_name"}, "op": "=", "value": "John"}]
    )
    state = GraphState(user_query="q", schema_info=mock_schema, plan=plan.model_dump(), selected_datasource_id="ds1")
    
    new_state = validator(state)
    assert any("Aliases are only allowed in 'select_columns'" in e.message for e in new_state["errors"])

def test_validator_invalid_column_name(mock_schema, mock_registry):
    validator = ValidatorNode(registry=mock_registry)
    plan = PlanModel(
        entity_ids=["e1"],
        tables=[TableRef(name="users", alias="u")],
        select_columns=[ColumnRef(expr="u.invalid_col")]
    )
    state = GraphState(user_query="q", schema_info=mock_schema, plan=plan.model_dump(), selected_datasource_id="ds1")
    
    new_state = validator(state)
    assert any("Column 'u.invalid_col' not found" in e.message for e in new_state["errors"])
