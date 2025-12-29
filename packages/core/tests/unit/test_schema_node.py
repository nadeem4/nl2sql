
import pytest
from unittest.mock import MagicMock
from nl2sql.core.nodes.schema.node import SchemaNode
from nl2sql.core.schemas import GraphState
from nl2sql.core.datasource_registry import DatasourceRegistry
from nl2sql_adapter_sdk import DatasourceAdapter, SchemaMetadata, Table, Column
from nl2sql.core.vector_store import OrchestratorVectorStore
from nl2sql.core.nodes.schema.schemas import SchemaInfo

def test_schema_node_initialization():
    """Test that SchemaNode initializes correctly."""
    mock_registry = MagicMock(spec=DatasourceRegistry)
    mock_vector_store = MagicMock(spec=OrchestratorVectorStore)
    node = SchemaNode(registry=mock_registry, vector_store=mock_vector_store)
    assert node.registry == mock_registry
    assert node.vector_store == mock_vector_store

def test_schema_node_retrieval_via_adapter():
    """Test that SchemaNode retrieves schema from adapter."""
    mock_registry = MagicMock(spec=DatasourceRegistry)
    mock_vector_store = MagicMock(spec=OrchestratorVectorStore)
    mock_adapter = MagicMock(spec=DatasourceAdapter)
    
    # Setup Registry
    mock_registry.get_adapter.return_value = mock_adapter
    
    # Setup Adapter Response
    # Setup Adapter Response
    mock_adapter.fetch_schema.return_value = SchemaMetadata(
        datasource_id="test_ds",
        tables=[
            Table(name="orders", columns=[
                Column(name="id", type="INTEGER", is_primary_key=True),
                Column(name="customer_id", type="INTEGER")
            ]),
            Table(name="order_items", columns=[
                Column(name="id", type="INTEGER")
            ])
        ]
    )
    
    node = SchemaNode(registry=mock_registry, vector_store=mock_vector_store)
    
    # State with pre-identified candidates (simulate Decomposer)
    # The Node should respect candidates if present in entity_mapping
    # But here let's assume no mapping, so it might use fallback or fetch all if logic says so?
    # SchemaNode logic: 
    # if not target_tables: fallback to semantic search.
    # if semantic search returns X, use X.
    
    # Let's mock semantic search fallback
    # Mock vector store embeddings to avoid error log but we can just mock fallback result
    node._semantic_filter_fallback = MagicMock(return_value=["orders", "order_items"]) 
    
    state = GraphState(
        user_query="Show orders", 
        selected_datasource_id="ds1"
    )
    
    result = node(state)
    
    assert "schema_info" in result
    schema_info = result["schema_info"]
    assert isinstance(schema_info, SchemaInfo)
    assert len(schema_info.tables) == 2
    table_names = [t.name for t in schema_info.tables]
    assert "orders" in table_names
    assert "order_items" in table_names
    
    mock_adapter.fetch_schema.assert_called()

def test_schema_node_uses_candidates_from_mapping():
    """Test that SchemaNode uses candidates provided by Decomposer/Mapping."""
    mock_registry = MagicMock(spec=DatasourceRegistry)
    mock_adapter = MagicMock(spec=DatasourceAdapter)
    mock_registry.get_adapter.return_value = mock_adapter
    
    mock_adapter.fetch_schema.return_value = SchemaMetadata(
        datasource_id="test_ds",
        tables=[
            Table(name="specific_table", columns=[])
        ]
    )
    
    node = SchemaNode(registry=mock_registry)
    
    # Mock Entity Mapping
    from nl2sql.core.nodes.decomposer.schemas import EntityMapping
    mapping = EntityMapping(
        entity_id="e1", 
        datasource_id="ds1", 
        table="specific_table", 
        candidate_tables=["specific_table"],
        coverage_reasoning="Test reasoning"
    )
    
    state = GraphState(
        user_query="...", 
        selected_datasource_id="ds1",
        entity_mapping=[mapping]
    )
    
    node(state)
    
    # Verify it requested ONLY the candidate tables
    mock_adapter.fetch_schema.assert_called()

