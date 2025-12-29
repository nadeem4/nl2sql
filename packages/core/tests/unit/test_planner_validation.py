
import unittest
from unittest.mock import MagicMock
import json
from nl2sql.pipeline.nodes.planner.node import PlannerNode
from nl2sql.pipeline.nodes.schema.schemas import SchemaInfo, TableInfo, ColumnInfo
from nl2sql.pipeline.nodes.planner.schemas import PlanModel
from nl2sql.pipeline.state import GraphState

def test_planner_accepts_valid_columns():
    """Test that PlannerNode accepts valid plans."""
    schema_info = SchemaInfo(
        tables=[TableInfo(
            name="users", 
            alias="u", 
            columns=[
                ColumnInfo(name="u.id", original_name="id", type="INT"),
                ColumnInfo(name="u.name", original_name="name", type="VARCHAR")
            ]
        )]
    )
    
    mock_llm = MagicMock()
    plan_data = {
        "entity_ids": ["e1"],
        "tables": [{"name": "users", "alias": "u"}],
        "select_columns": [{"expr": "u.name"}],
        "filters": [],
        "joins": [],
        "group_by": [],
        "order_by": [],
        "having": []
    }
    mock_plan = PlanModel(**plan_data)
    mock_llm.return_value = mock_plan
    
    # Needs registry mock now
    from nl2sql.datasources import DatasourceRegistry
    mock_registry = MagicMock(spec=DatasourceRegistry)
    
    node = PlannerNode(registry=mock_registry, llm=mock_llm)
    
    # Mock Entity
    from nl2sql.pipeline.nodes.intent.schemas import Entity, EntityRole
    entity = Entity(entity_id="e1", name="users", role=EntityRole.REFERENCE)
    
    state = GraphState(
        user_query="get user names",
        schema_info=schema_info,
        selected_datasource_id="ds1",
        entities=[entity]
    )
    
    new_state = node(state)
    
    assert new_state.get("plan") is not None
    assert not new_state.get("errors")
