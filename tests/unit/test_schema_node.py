
import pytest
from unittest.mock import MagicMock
from nl2sql.nodes.schema_node import SchemaNode
from nl2sql.schemas import GraphState

def test_schema_node_initialization(mock_profile, mock_vector_store):
    """Test that SchemaNode initializes correctly with a profile."""
    node = SchemaNode(profile=mock_profile, vector_store=mock_vector_store)
    assert node.profile == mock_profile
    assert node.vector_store == mock_vector_store

def test_schema_node_retrieval(mock_profile, mock_vector_store):
    """Test schema retrieval logic."""
    node = SchemaNode(profile=mock_profile, vector_store=mock_vector_store)
    
    # Mock vector store to return specific tables
    mock_vector_store.retrieve.return_value = ["users"]
    
    # Mock engine inspection (requires patching inspect inside the module or mocking make_engine)
    # Since SchemaNode calls make_engine internally, we should patch it.
    with pytest.MonkeyPatch.context() as m:
        mock_inspect = MagicMock()
        mock_inspect.get_table_names.return_value = ["users", "orders"]
        mock_inspect.get_columns.return_value = [{"name": "id"}]
        mock_inspect.get_foreign_keys.return_value = []
        
        m.setattr("nl2sql.nodes.schema_node.inspect", lambda engine: mock_inspect)
        
        state = GraphState(user_query="show users")
        new_state = node(state)
        
        # Check that retrieved tables were filtered
        assert "users" in new_state.validation["schema_tables"]
        assert "orders" not in new_state.validation["schema_tables"] # Should be filtered by vector store
