
import pytest
from unittest.mock import MagicMock
from nl2sql.nodes.schema_node import SchemaNode
from nl2sql.schemas import GraphState

def test_schema_node_initialization(mock_profile, mock_vector_store):
    """Test that SchemaNode initializes correctly with a profile."""
import pytest
from unittest.mock import MagicMock, patch
import json
from nl2sql.nodes.schema_node import SchemaNode
from nl2sql.schemas import GraphState

def test_schema_node_initialization(mock_profile, mock_vector_store):
    """Test that SchemaNode initializes correctly with a profile."""
    node = SchemaNode(profile=mock_profile, vector_store=mock_vector_store)
    assert node.profile == mock_profile
    assert node.vector_store == mock_vector_store

def test_schema_node_retrieval(mock_profile):
    """Test that SchemaNode uses vector store and expands graph."""
    mock_vector_store = MagicMock()
    mock_vector_store.retrieve.return_value = ["orders"]
    
    # Mock state with intent
    state = GraphState(
        user_query="revenue",
        validation={"intent": {"entities": ["Q1"], "keywords": ["sales"]}}
    )
    
    node = SchemaNode(profile=mock_profile, vector_store=mock_vector_store)
    
    with patch("nl2sql.nodes.schema_node.make_engine"), \
         patch("nl2sql.nodes.schema_node.inspect") as mock_inspect:
        
        mock_inspector = mock_inspect.return_value
        # DB has orders, order_items, products
        mock_inspector.get_table_names.return_value = ["orders", "order_items", "products"]
        
        # orders has FK to order_items? No, usually order_items has FK to orders.
        # Let's test the other direction: Retrieve 'order_items', should pull 'orders' and 'products'
        # Or: Retrieve 'orders', check if it pulls things it refers to (e.g. customers)
        
        # Let's say 'orders' has FK to 'customers'
        def get_fks(table):
            if table == "orders":
                return [{"referred_table": "customers"}]
            return []
        
        mock_inspector.get_foreign_keys.side_effect = get_fks
        mock_inspector.get_table_names.return_value = ["orders", "customers", "products"]
        
        # Run node
        new_state = node(state)
        
        # 1. Verify Search Enrichment
        # Query should be "revenue Q1 sales"
        mock_vector_store.retrieve.assert_called_with("revenue Q1 sales")
        
        # 2. Verify Graph Expansion
        # Vector store returned ["orders"]
        # orders -> FK to customers
        # Result should contain orders AND customers
        assert new_state.schema_info is not None
        tables = new_state.schema_info.tables
        table_names = [t.name for t in tables]
        assert "orders" in table_names
        assert "customers" in table_names
        assert "products" not in table_names # Not connected to orders in this mock
        
        # Verify TableInfo structure
        orders_info = next(t for t in tables if t.name == "orders")
        assert orders_info.alias  # Should have an alias
        assert isinstance(orders_info.columns, list)
